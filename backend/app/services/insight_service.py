"""고객 분석 결과 조회에 필요한 데이터 접근·조회 규칙입니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, case, desc, exists, false, func, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from ..enums import CampaignStatus, RiskLevel
from ..models import Campaign, CampaignTarget, Customer, CustomerInsight
from .campaign_service import (
    DEFAULT_CONTACT_COOLDOWN_DAYS,
    OPEN_CAMPAIGN_STATUSES,
    OPEN_TARGET_STATUSES,
    SEGMENT_PRIORITIES,
    UNCLASSIFIED_CAMPAIGN_PRIORITY,
    _campaign_priority,
)


@dataclass(frozen=True)
class InsightFilters:
    """최신 고객 분석 결과에 적용할 조회 조건입니다."""

    risk_level: RiskLevel | None = None
    cluster_name: str | None = None
    customer_id: int | None = None
    campaign_candidates_only: bool = False
    campaign_id: int | None = None


@dataclass(frozen=True)
class InsightPage:
    """페이지 결과와 필터 기준 요약 통계입니다."""

    items: list[CustomerInsight]
    page: int
    page_size: int
    total: int
    total_pages: int
    average_churn_probability: float
    risk_counts: dict[str, int]
    cluster_counts: dict[str, int]
    cluster_options: dict[str, int]


def _latest_query() -> Select[tuple[CustomerInsight]]:
    """고객별 최신 분석 스냅샷만 선택하는 기본 쿼리입니다."""
    ranked_insights = (
        select(
            CustomerInsight.id.label("insight_id"),
            func.row_number()
            .over(
                partition_by=CustomerInsight.customer_id,
                order_by=(
                    desc(CustomerInsight.as_of_date),
                    desc(CustomerInsight.scored_at),
                    desc(CustomerInsight.id),
                ),
            )
            .label("row_number"),
        )
        .subquery()
    )
    return (
        select(CustomerInsight)
        .join(
            ranked_insights,
            ranked_insights.c.insight_id == CustomerInsight.id,
        )
        .where(ranked_insights.c.row_number == 1)
    )


def _campaign_priority_expression():
    """캠페인 등록 서비스와 동일한 우선순위를 SQL 표현식으로 만듭니다."""
    return case(
        (
            Campaign.segment_code == "high_risk_retention",
            SEGMENT_PRIORITIES["high_risk_retention"],
        ),
        (
            Campaign.segment_code == "medium_reactivation",
            SEGMENT_PRIORITIES["medium_reactivation"],
        ),
        (
            Campaign.segment_code == "low_risk_upsell",
            SEGMENT_PRIORITIES["low_risk_upsell"],
        ),
        else_=UNCLASSIFIED_CAMPAIGN_PRIORITY,
    )


def _campaign_candidate_conditions(
    db: Session,
    *,
    campaign_id: int | None = None,
) -> list[object]:
    """캠페인 등록 시점에 충돌하는 고객을 목록 단계에서 제외합니다."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=DEFAULT_CONTACT_COOLDOWN_DAYS)
    recent_customer_contact = exists(
        select(Customer.customer_id).where(
            Customer.customer_id == CustomerInsight.customer_id,
            or_(
                Customer.marketing_opt_out.is_(True),
                Customer.last_contacted_at >= cutoff,
            ),
        )
    )
    recent_target_contact = exists(
        select(CampaignTarget.id).where(
            CampaignTarget.customer_id == CustomerInsight.customer_id,
            CampaignTarget.status.in_(
                [CampaignStatus.CONTACTED.value, CampaignStatus.COMPLETED.value]
            ),
            CampaignTarget.processed_at >= cutoff,
        )
    )
    same_campaign_target = None
    active_target_conditions: list[object] = [
        CampaignTarget.customer_id == CustomerInsight.customer_id,
        CampaignTarget.status.in_(OPEN_TARGET_STATUSES),
        Campaign.status.in_(OPEN_CAMPAIGN_STATUSES),
    ]
    if campaign_id is not None:
        selected_campaign = db.get(Campaign, campaign_id)
        if selected_campaign is None:
            return [false()]
        selected_priority = _campaign_priority(selected_campaign)
        same_campaign_target = exists(
            select(CampaignTarget.id).where(
                CampaignTarget.customer_id == CustomerInsight.customer_id,
                CampaignTarget.campaign_id == campaign_id,
            )
        )
        # 선택 캠페인이 대체할 수 없는 동일·상위 우선순위 대상만 제외합니다.
        active_target_conditions.append(
            or_(
                CampaignTarget.status == CampaignStatus.CONTACTED.value,
                _campaign_priority_expression() >= selected_priority,
            )
        )
    active_priority_conflict = exists(
        select(CampaignTarget.id)
        .join(Campaign, Campaign.id == CampaignTarget.campaign_id)
        .where(*active_target_conditions)
    )
    conditions = [
        ~recent_customer_contact,
        ~recent_target_contact,
        ~active_priority_conflict,
    ]
    if same_campaign_target is not None:
        conditions.append(~same_campaign_target)
    return conditions


def _filtered_query(
    db: Session,
    filters: InsightFilters,
) -> Select[tuple[CustomerInsight]]:
    """최신 스냅샷 쿼리에 필터를 적용합니다."""
    conditions = []
    if filters.risk_level is not None:
        conditions.append(CustomerInsight.risk_level == filters.risk_level.value)
    if filters.cluster_name is not None:
        conditions.append(CustomerInsight.cluster_name == filters.cluster_name)
    if filters.customer_id is not None:
        conditions.append(CustomerInsight.customer_id == filters.customer_id)
    if filters.campaign_candidates_only:
        conditions.extend(
            _campaign_candidate_conditions(db, campaign_id=filters.campaign_id)
        )
    return _latest_query().where(*conditions)


def fetch_insight_page(
    db: Session,
    *,
    filters: InsightFilters,
    sort_by: str,
    sort_order: str,
    page: int,
    page_size: int,
) -> InsightPage:
    """최신 분석 결과 목록과 요약 통계를 조회합니다."""
    query = _filtered_query(db, filters)
    filtered_subquery = query.order_by(None).subquery()
    total = int(
        db.scalar(select(func.count()).select_from(filtered_subquery)) or 0
    )
    risk_rows = db.execute(
        select(
            filtered_subquery.c.risk_level,
            func.count().label("count"),
        ).group_by(filtered_subquery.c.risk_level)
    ).all()
    cluster_rows = db.execute(
        select(
            filtered_subquery.c.cluster_name,
            func.count().label("count"),
        ).group_by(filtered_subquery.c.cluster_name)
    ).all()
    cluster_options_query = _filtered_query(
        db,
        InsightFilters(
            risk_level=filters.risk_level,
            customer_id=filters.customer_id,
            campaign_candidates_only=filters.campaign_candidates_only,
            campaign_id=filters.campaign_id,
        )
    )
    cluster_options_subquery = cluster_options_query.order_by(None).subquery()
    cluster_option_rows = db.execute(
        select(
            cluster_options_subquery.c.cluster_name,
            func.count().label("count"),
        ).group_by(cluster_options_subquery.c.cluster_name)
    ).all()
    average_probability = db.scalar(
        select(func.avg(filtered_subquery.c.churn_probability))
    )

    sort_column = {
        "churn_probability": CustomerInsight.churn_probability,
        "actual_transaction_count": CustomerInsight.expected_transaction_count + CustomerInsight.activity_gap,
        "activity_gap": CustomerInsight.activity_gap,
        "expected_transaction_count": CustomerInsight.expected_transaction_count,
        "scored_at": CustomerInsight.scored_at,
    }.get(sort_by)

    card_category_rank = {
        "Blue": 1,
        "Silver": 2,
        "Gold": 3,
        "Platinum": 4,
    }

    def _card_category_rank(customer: Customer):
        """카드 등급을 알파벳순이 아니라 실제 등급 순서(낮음→높음)로 정렬합니다."""
        return case(card_category_rank, value=customer.card_category, else_=0)

    customer_sort_columns = {
        "total_trans_amt": lambda customer: customer.total_trans_amt,
        "card_category": _card_category_rank,
        "contacts_count_12_mon": lambda customer: customer.contacts_count_12_mon,
    }

    if sort_by in customer_sort_columns:
        sort_customer = aliased(Customer)
        customer_sort_column = customer_sort_columns[sort_by](sort_customer)
        ordered_query = query.join(
            sort_customer,
            sort_customer.customer_id == CustomerInsight.customer_id,
        ).order_by(
            customer_sort_column.asc() if sort_order == "asc" else customer_sort_column.desc(),
            CustomerInsight.id.desc(),
        )
    else:
        ordered_query = query.order_by(
            sort_column.asc() if sort_order == "asc" else sort_column.desc(),
            CustomerInsight.id.desc(),
        )
    items = db.scalars(
        ordered_query
        .options(selectinload(CustomerInsight.customer))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    total_pages = (total + page_size - 1) // page_size if total else 0
    return InsightPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        average_churn_probability=float(average_probability or 0.0),
        risk_counts={str(row[0]): int(row[1]) for row in risk_rows},
        cluster_counts={str(row[0]): int(row[1]) for row in cluster_rows},
        cluster_options={str(row[0]): int(row[1]) for row in cluster_option_rows},
    )


def fetch_latest_customer_insight(
    db: Session,
    customer_id: int,
) -> CustomerInsight | None:
    """고객 한 명의 최신 분석 결과와 고객 특성을 조회합니다."""
    query = _filtered_query(db, InsightFilters(customer_id=customer_id)).options(
        selectinload(CustomerInsight.customer),
        selectinload(CustomerInsight.customer_snapshot),
    )
    return db.scalar(query)


def fetch_customer_insight_history(
    db: Session,
    customer_id: int,
    *,
    limit: int = 24,
) -> list[CustomerInsight]:
    """고객의 분석 시점별 이력을 최신순으로 조회합니다."""
    return db.scalars(
        select(CustomerInsight)
        .where(CustomerInsight.customer_id == customer_id)
        .options(selectinload(CustomerInsight.customer))
        .order_by(
            CustomerInsight.as_of_date.desc(),
            CustomerInsight.scored_at.desc(),
            CustomerInsight.id.desc(),
        )
        .limit(limit)
    ).all()
