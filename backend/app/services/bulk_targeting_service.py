"""분석 결과를 캠페인 대상으로 변환하는 세그먼트 일괄 타기팅 규칙입니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import floor
import secrets
from typing import Any

from sqlalchemy import Select, case, desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..enums import (
    BulkTargetingRunStatus,
    BulkTargetingSegment,
    CampaignEventType,
    CampaignLifecycleStatus,
    CampaignStatus,
    ModelRunStatus,
    RiskLevel,
)
from ..models import (
    BulkTargetingCandidateSnapshot,
    BulkTargetingRun,
    Campaign,
    CampaignTarget,
    Customer,
    CustomerInsight,
    ScoringBatch,
    User,
)
from ..schemas import BulkTargetingPreviewRequest
from .campaign_service import (
    CampaignConflictError,
    CampaignDomainError,
    OPEN_CAMPAIGN_STATUSES,
    OPEN_TARGET_STATUSES,
    _add_event,
    create_campaign_target,
)


DEFAULT_CLUSTER_NAME = "우량(예상이상)"

# 완납형(리볼빙 잔액 0)을 "이탈 임박"과 "우량"으로 가르는 연간 거래건수 기준입니다.
# 원본 10,127명 검증: 55건 이상 1,480명은 이탈률 12.2%인 반면, 55건 미만 990명은
# 72.0%로 갈립니다(train 11.6%/72.6%, test 13.5%/70.5%로 재현).
FULL_PAYER_ACTIVE_TRANSACTIONS = 55
# 소액 잔액 판정 상한과 거래 감소 판정 기준입니다.
SMALL_BALANCE_LIMIT = 500
TRANSACTION_DECLINE_RATIO = 0.6
# 이탈률이 가장 낮은 리볼빙 잔액 구간입니다(4.6%, 전체 평균 16.1%).
STABLE_BALANCE_MIN = 1_000
STABLE_BALANCE_MAX = 2_000

# 세그먼트 우선순위 — 값이 클수록 우선합니다.
# "이미 동급 이상 우선순위 캠페인에 걸린 고객은 하위 세그먼트가 가져가지 않는다"는
# 기존 규칙에 그대로 사용되므로, 신규 세그먼트를 추가할 때 여기에도 반드시 넣어야
# 합니다(누락 시 미리보기 단계에서 KeyError).
SEGMENT_TARGETING_PRIORITIES = {
    # 실제 이탈률 약 90% — 가장 시급합니다.
    BulkTargetingSegment.SMALL_BALANCE_DECLINE.value: 400,
    # 실제 이탈률 72% — 고위험 예측군보다 앞세웁니다.
    BulkTargetingSegment.DORMANT_FULL_PAYER.value: 350,
    BulkTargetingSegment.HIGH_RISK_RETENTION.value: 300,
    BulkTargetingSegment.MEDIUM_REACTIVATION.value: 200,
    # 이탈률이 평균 이하인 우량군이라 리텐션 캠페인에 양보합니다.
    BulkTargetingSegment.ACTIVE_FULL_PAYER.value: 120,
    BulkTargetingSegment.LOW_RISK_UPSELL.value: 100,
    BulkTargetingSegment.STABLE_PRIME.value: 90,
}

DEFAULT_CAMPAIGN_NAMES = {
    BulkTargetingSegment.HIGH_RISK_RETENTION.value: "고위험 고객 리텐션 일괄 캠페인",
    BulkTargetingSegment.MEDIUM_REACTIVATION.value: "중위험 고객 재활성화 일괄 캠페인",
    BulkTargetingSegment.LOW_RISK_UPSELL.value: "우량 고객 업셀링 일괄 캠페인",
    BulkTargetingSegment.SMALL_BALANCE_DECLINE.value: "소액 잔액·거래 급감 긴급 컨택",
    BulkTargetingSegment.DORMANT_FULL_PAYER.value: "완납형 저활동 고객 리텐션",
    BulkTargetingSegment.ACTIVE_FULL_PAYER.value: "완납형 우량 고객 거래 활성화",
    BulkTargetingSegment.STABLE_PRIME.value: "안정 우량 고객 업셀링",
}

# 캠페인 설명 = "왜 이 고객들을 골랐는가". 마케팅 담당자가 근거 없이 캠페인을
# 집행하지 않도록, 선정 조건과 검증된 이탈률을 캠페인에 그대로 기록합니다.
DEFAULT_CAMPAIGN_DESCRIPTIONS = {
    BulkTargetingSegment.HIGH_RISK_RETENTION.value: (
        "선정 근거: 이탈 예측 모델이 고위험(high)으로 분류한 고객입니다. "
        "이탈 확률이 높은 순으로 우선 배정합니다."
    ),
    BulkTargetingSegment.MEDIUM_REACTIVATION.value: (
        "선정 근거: 중위험(medium)이면서 활동성 갭이 하위 분위수에 속하는 고객입니다. "
        "예상 대비 거래가 줄어든 정도가 큰 순으로 배정합니다."
    ),
    BulkTargetingSegment.LOW_RISK_UPSELL.value: (
        "선정 근거: 저위험(low)이면서 '우량(예상이상)' 군집에 속하는 고객입니다. "
        "예상 거래 건수가 많은 순으로 배정합니다."
    ),
    BulkTargetingSegment.SMALL_BALANCE_DECLINE.value: (
        f"선정 근거: 리볼빙 잔액이 1~{SMALL_BALANCE_LIMIT - 1}원으로 소액인데 분기 거래건수가 "
        f"{TRANSACTION_DECLINE_RATIO}배 미만으로 급감한 고객입니다. "
        "이 조건의 실제 이탈률은 약 90% 전체 평균 16.1%의 5배가 넘습니다."
        " 대상 수는 적지만 이탈이 임박한 신호이므로 "
        "가장 먼저 컨택합니다."
    ),
    BulkTargetingSegment.DORMANT_FULL_PAYER.value: (
        f"선정 근거: 리볼빙 잔액이 0원(완납)이면서 연간 거래가 "
        f"{FULL_PAYER_ACTIVE_TRANSACTIONS}건 미만인 고객입니다. "
        "완납 고객 전체의 실제 이탈률은 36.2%인데, 그중 거래가 저조한 이 그룹만 보면 "
        "72.0%로 치솟습니다 잔액이 없어 전환 비용이 없고 "
        "카드 사용도 줄어든 상태라 이탈 직전으로 판단합니다."
    ),
    BulkTargetingSegment.ACTIVE_FULL_PAYER.value: (
        f"선정 근거: 리볼빙 잔액이 0원(완납)이지만 연간 거래가 "
        f"{FULL_PAYER_ACTIVE_TRANSACTIONS}건 이상인 고객입니다. "
        "같은 완납 고객이라도 거래가 활발하면 실제 이탈률이 12.2%로 전체 평균(16.1%)보다 "
        "낮습니다. 이미 안정적인 고객이므로 리볼빙 잔액을 늘리도록 유도하지 않고, "
        "거래 리워드와 상품 교차판매로 관계를 넓히는 것을 목표로 합니다."
    ),
    BulkTargetingSegment.STABLE_PRIME.value: (
        f"선정 근거: 리볼빙 잔액이 {STABLE_BALANCE_MIN:,}~{STABLE_BALANCE_MAX:,}원 구간인 고객입니다. "
        "이 구간의 실제 이탈률은 4.6%로 전체 잔액 구간 중 가장 낮습니다. "
        "잔액이 0원이거나 2,000원을 넘으면 이탈률이 다시 올라가므로(각 36.2%, 15.3%) "
        "이 구간을 유지시키는 것이 목표이며, 한도 상향·프리미엄 전환 제안에 적합합니다."
    ),
}


class BulkTargetingDomainError(CampaignDomainError):
    """일괄 타기팅 배치의 업무 규칙 위반입니다."""


class BulkTargetingStateError(BulkTargetingDomainError):
    """일괄 타기팅 배치의 현재 상태와 맞지 않는 요청입니다."""


@dataclass(frozen=True)
class BulkTargetingCandidate:
    """일괄 타기팅 후보와 제외 판정에 필요한 상태입니다."""

    insight: CustomerInsight
    customer: Customer
    has_active_campaign: bool
    recently_contacted: bool
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class BulkTargetingScan:
    """세그먼트 후보 전체와 상호 배타적인 제외 집계입니다."""

    candidates: list[BulkTargetingCandidate]
    evaluated_candidates: list[BulkTargetingCandidate]
    segment_matched_count: int
    skipped_active_campaign_count: int
    skipped_recent_contact_count: int
    skipped_opt_out_count: int


def _latest_insight_query(source_as_of_date: date | None) -> Select[Any]:
    """고객별 최신 인사이트를, 필요하면 지정 시점 이전으로 제한합니다."""
    conditions: list[Any] = []
    if source_as_of_date is not None:
        conditions.extend(
            [
                CustomerInsight.as_of_date.is_not(None),
                CustomerInsight.as_of_date <= source_as_of_date,
            ]
        )
    ranked = (
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
        .where(*conditions)
        .subquery()
    )
    return select(CustomerInsight).join(
        ranked,
        ranked.c.insight_id == CustomerInsight.id,
    ).where(ranked.c.row_number == 1)


def _lower_quantile(values: list[float], quantile: float) -> float:
    """외부 통계 패키지 없이 선형 보간으로 하위 분위수를 계산합니다."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _activity_gap_threshold(
    db: Session,
    *,
    source_as_of_date: date | None,
    quantile: float,
    scoring_batch_id: int | None = None,
) -> float:
    """최신 분석 결과의 activity_gap 하위 분위수 기준을 계산합니다."""
    if scoring_batch_id is not None:
        values = db.scalars(
            select(CustomerInsight.activity_gap).where(
                CustomerInsight.scoring_batch_id == scoring_batch_id
            )
        ).all()
    else:
        latest = _latest_insight_query(source_as_of_date).subquery()
        values = db.scalars(select(latest.c.activity_gap)).all()
    return _lower_quantile(values, quantile)


def _resolve_scoring_batch(
    db: Session,
    payload: BulkTargetingPreviewRequest,
) -> ScoringBatch:
    """타기팅 후보 전체가 공유할 성공 scoring batch를 하나로 고정합니다."""
    query = select(ScoringBatch).where(
        ScoringBatch.status == ModelRunStatus.SUCCEEDED.value
    )
    if payload.scoring_batch_id is not None:
        query = query.where(ScoringBatch.id == payload.scoring_batch_id)
    if payload.source_as_of_date is not None:
        query = query.where(ScoringBatch.as_of_date <= payload.source_as_of_date)
    batch = db.scalar(
        query.order_by(
            ScoringBatch.as_of_date.desc(),
            ScoringBatch.completed_at.desc(),
            ScoringBatch.id.desc(),
        ).limit(1)
    )
    if batch is None:
        raise BulkTargetingDomainError(
            "A succeeded scoring batch is required for bulk targeting."
        )
    return batch


def _build_rules(
    db: Session,
    payload: BulkTargetingPreviewRequest,
    *,
    experiment_seed: str | None = None,
) -> dict[str, Any]:
    """요청값을 immutable 정책 JSON으로 정규화합니다."""
    segment = payload.segment.value
    scoring_batch = _resolve_scoring_batch(db, payload)
    source_as_of_date = scoring_batch.as_of_date
    rules: dict[str, Any] = {
        "segment": segment,
        "campaign_name": payload.campaign_name or DEFAULT_CAMPAIGN_NAMES[segment],
        # 사용자가 설명을 비워두면 세그먼트 선정 근거를 기본값으로 남깁니다 —
        # 나중에 캠페인 목록만 봐도 "왜 이 고객들인가"를 알 수 있어야 합니다.
        "description": payload.description or DEFAULT_CAMPAIGN_DESCRIPTIONS.get(segment),
        "channel": payload.channel,
        "assigned_to_user_id": payload.assigned_to_user_id,
        "recent_contact_days": payload.recent_contact_days,
        "activity_gap_quantile": payload.activity_gap_quantile,
        "cluster_name": payload.cluster_name or DEFAULT_CLUSTER_NAME,
        "max_targets": payload.max_targets,
        "source_as_of_date": (
            source_as_of_date.isoformat() if source_as_of_date is not None else None
        ),
        "scoring_batch_id": scoring_batch.id,
        "decision_policy_id": scoring_batch.decision_policy_id,
        "dataset_sha256": scoring_batch.dataset_sha256,
        "experiment_enabled": payload.experiment_enabled,
        "control_group_ratio": (
            payload.control_group_ratio if payload.experiment_enabled else 0.0
        ),
        "fixed_cost": payload.fixed_cost,
        "cost_per_contact": payload.cost_per_contact,
        "revenue_per_conversion": payload.revenue_per_conversion,
        "retention_window_days": payload.retention_window_days,
        "experiment_seed": experiment_seed or secrets.token_hex(16),
    }
    if segment == BulkTargetingSegment.MEDIUM_REACTIVATION.value:
        rules["activity_gap_threshold"] = _activity_gap_threshold(
            db,
            source_as_of_date=source_as_of_date,
            quantile=payload.activity_gap_quantile,
            scoring_batch_id=scoring_batch.id,
        )
    return rules


def _date_from_rules(rules: dict[str, Any]) -> date | None:
    value = rules.get("source_as_of_date")
    return date.fromisoformat(value) if value else None


def _segment_condition(rules: dict[str, Any]) -> Any:
    segment = str(rules["segment"])
    if segment == BulkTargetingSegment.HIGH_RISK_RETENTION.value:
        return CustomerInsight.risk_level == RiskLevel.HIGH.value
    if segment == BulkTargetingSegment.MEDIUM_REACTIVATION.value:
        return (
            CustomerInsight.risk_level == RiskLevel.MEDIUM.value,
            CustomerInsight.activity_gap <= float(rules["activity_gap_threshold"]),
        )
    if segment == BulkTargetingSegment.LOW_RISK_UPSELL.value:
        return (
            CustomerInsight.risk_level == RiskLevel.LOW.value,
            CustomerInsight.cluster_name == str(rules["cluster_name"]),
        )
    # 아래 4종은 예측 위험도가 아니라 고객 원본 지표로 판정합니다.
    # 분류 모델의 확률 분포에 의존하지 않아 배치 결과가 흔들려도 대상이 유지됩니다.
    if segment == BulkTargetingSegment.SMALL_BALANCE_DECLINE.value:
        return (
            Customer.total_revolving_bal > 0,
            Customer.total_revolving_bal < SMALL_BALANCE_LIMIT,
            Customer.total_ct_chng_q4_q1 < TRANSACTION_DECLINE_RATIO,
        )
    if segment == BulkTargetingSegment.DORMANT_FULL_PAYER.value:
        return (
            Customer.total_revolving_bal == 0,
            Customer.total_trans_ct < FULL_PAYER_ACTIVE_TRANSACTIONS,
        )
    if segment == BulkTargetingSegment.ACTIVE_FULL_PAYER.value:
        return (
            Customer.total_revolving_bal == 0,
            Customer.total_trans_ct >= FULL_PAYER_ACTIVE_TRANSACTIONS,
        )
    if segment == BulkTargetingSegment.STABLE_PRIME.value:
        return (
            Customer.total_revolving_bal >= STABLE_BALANCE_MIN,
            Customer.total_revolving_bal < STABLE_BALANCE_MAX,
        )
    raise BulkTargetingDomainError(f"Unsupported targeting segment: {segment}.")


def _is_recent_contacted_at(value: datetime | None, cutoff: datetime) -> bool:
    if value is None:
        return False
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return normalized >= cutoff


def _candidate_query(
    rules: dict[str, Any],
    *,
    ignore_run_id: int | None = None,
) -> Select[Any]:
    source_as_of_date = _date_from_rules(rules)
    scoring_batch_id = rules.get("scoring_batch_id")
    latest_ids = (
        _latest_insight_query(source_as_of_date).subquery()
        if scoring_batch_id is None
        else None
    )
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=int(rules["recent_contact_days"])
    )

    segment_priority = SEGMENT_TARGETING_PRIORITIES[str(rules["segment"])]
    existing_priority = case(
        *(
            (Campaign.segment_code == segment_code, priority)
            for segment_code, priority in SEGMENT_TARGETING_PRIORITIES.items()
        ),
        # 수동·미분류 캠페인은 의도 확인 없이 자동 취소하지 않습니다.
        else_=1000,
    )
    active_target_query = (
        select(CampaignTarget.id)
        .join(Campaign, Campaign.id == CampaignTarget.campaign_id)
        .where(
            CampaignTarget.customer_id == Customer.customer_id,
            CampaignTarget.status.in_(OPEN_TARGET_STATUSES),
            Campaign.status.in_(OPEN_CAMPAIGN_STATUSES),
            or_(
                CampaignTarget.status == CampaignStatus.CONTACTED.value,
                existing_priority >= segment_priority,
            ),
        )
    )
    if ignore_run_id is not None:
        active_target_query = active_target_query.where(
            or_(
                CampaignTarget.bulk_targeting_run_id.is_(None),
                CampaignTarget.bulk_targeting_run_id != ignore_run_id,
            )
        )
    has_active_campaign = active_target_query.correlate(Customer).exists()
    recent_target = (
        select(CampaignTarget.id)
        .where(
            CampaignTarget.customer_id == Customer.customer_id,
            CampaignTarget.status.in_(
                [CampaignStatus.CONTACTED.value, CampaignStatus.COMPLETED.value]
            ),
            CampaignTarget.processed_at >= cutoff,
        )
        .correlate(Customer)
        .exists()
    )

    segment_condition = _segment_condition(rules)
    if isinstance(segment_condition, tuple):
        segment_conditions = list(segment_condition)
    else:
        segment_conditions = [segment_condition]
    insight_source_condition = (
        CustomerInsight.scoring_batch_id == int(scoring_batch_id)
        if scoring_batch_id is not None
        else CustomerInsight.id.in_(select(latest_ids.c.id))
    )
    query = (
        select(
            CustomerInsight,
            Customer,
            has_active_campaign.label("has_active_campaign"),
            recent_target.label("recent_target"),
        )
        .join(Customer, Customer.customer_id == CustomerInsight.customer_id)
        .where(
            insight_source_condition,
            *segment_conditions,
        )
    )
    segment = str(rules["segment"])
    if segment == BulkTargetingSegment.HIGH_RISK_RETENTION.value:
        query = query.order_by(
            CustomerInsight.churn_probability.desc(),
            CustomerInsight.activity_gap.asc(),
            CustomerInsight.scored_at.desc(),
        )
    elif segment == BulkTargetingSegment.MEDIUM_REACTIVATION.value:
        query = query.order_by(
            CustomerInsight.activity_gap.asc(),
            CustomerInsight.churn_probability.desc(),
            CustomerInsight.scored_at.desc(),
        )
    elif segment in {
        BulkTargetingSegment.SMALL_BALANCE_DECLINE.value,
        BulkTargetingSegment.DORMANT_FULL_PAYER.value,
    }:
        # 이탈 임박 세그먼트 — 거래가 가장 적게 남은 고객부터 컨택합니다.
        query = query.order_by(
            Customer.total_trans_ct.asc(),
            Customer.total_ct_chng_q4_q1.asc(),
            CustomerInsight.churn_probability.desc(),
        )
    elif segment in {
        BulkTargetingSegment.ACTIVE_FULL_PAYER.value,
        BulkTargetingSegment.STABLE_PRIME.value,
    }:
        # 업셀·활성화 세그먼트 — 거래가 가장 활발한 고객부터 제안합니다.
        query = query.order_by(
            Customer.total_trans_ct.desc(),
            Customer.total_trans_amt.desc(),
            CustomerInsight.churn_probability.asc(),
        )
    else:
        query = query.order_by(
            CustomerInsight.expected_transaction_count.desc(),
            CustomerInsight.activity_gap.desc(),
            CustomerInsight.churn_probability.asc(),
        )
    return query


def scan_bulk_targeting(
    db: Session,
    rules: dict[str, Any],
    *,
    ignore_run_id: int | None = None,
) -> BulkTargetingScan:
    """세그먼트와 제외 조건을 적용해 preview 대상과 집계를 계산합니다."""
    candidates: list[BulkTargetingCandidate] = []
    evaluated_candidates: list[BulkTargetingCandidate] = []
    skipped_active = 0
    skipped_recent = 0
    skipped_opt_out = 0
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=int(rules["recent_contact_days"])
    )
    rows = db.execute(_candidate_query(rules, ignore_run_id=ignore_run_id)).all()
    for insight, customer, has_active_campaign, recent_target in rows:
        recently_contacted = _is_recent_contacted_at(customer.last_contacted_at, cutoff) or bool(
            recent_target
        )
        exclusion_reason = None
        if customer.marketing_opt_out:
            exclusion_reason = "opted_out"
        elif bool(has_active_campaign):
            exclusion_reason = "active_campaign"
        elif recently_contacted:
            exclusion_reason = "recent_contact"
        candidate = BulkTargetingCandidate(
            insight=insight,
            customer=customer,
            has_active_campaign=bool(has_active_campaign),
            recently_contacted=recently_contacted,
            exclusion_reason=exclusion_reason,
        )
        evaluated_candidates.append(candidate)
        if customer.marketing_opt_out:
            skipped_opt_out += 1
        elif candidate.has_active_campaign:
            skipped_active += 1
        elif candidate.recently_contacted:
            skipped_recent += 1
        else:
            candidates.append(candidate)
    return BulkTargetingScan(
        candidates=candidates,
        evaluated_candidates=evaluated_candidates,
        segment_matched_count=len(rows),
        skipped_active_campaign_count=skipped_active,
        skipped_recent_contact_count=skipped_recent,
        skipped_opt_out_count=skipped_opt_out,
    )


def _apply_scan_counts(run: BulkTargetingRun, scan: BulkTargetingScan) -> None:
    run.eligible_count = len(scan.candidates)
    run.preview_count = min(len(scan.candidates), int(run.rules_json["max_targets"]))
    run.skipped_active_campaign_count = scan.skipped_active_campaign_count
    run.skipped_recent_contact_count = scan.skipped_recent_contact_count
    run.skipped_opt_out_count = scan.skipped_opt_out_count


def preview_bulk_targeting(
    db: Session,
    *,
    payload: BulkTargetingPreviewRequest,
    actor: User,
    rerun_of_id: int | None = None,
    experiment_seed: str | None = None,
) -> tuple[BulkTargetingRun, BulkTargetingScan]:
    """정책을 고정한 preview run을 생성합니다."""
    if payload.assigned_to_user_id is not None:
        assignee = db.get(User, payload.assigned_to_user_id)
        if assignee is None:
            raise BulkTargetingDomainError("The assigned user was not found.")
        from .campaign_service import validate_assignee

        validate_assignee(assignee)
    rules = _build_rules(db, payload, experiment_seed=experiment_seed)
    scan = scan_bulk_targeting(db, rules)
    run = BulkTargetingRun(
        segment_code=payload.segment.value,
        status=BulkTargetingRunStatus.PREVIEWED.value,
        requested_by_user_id=actor.id,
        rerun_of_id=rerun_of_id,
        scoring_batch_id=int(rules["scoring_batch_id"]),
        source_as_of_date=_date_from_rules(rules),
        rules_json=rules,
    )
    db.add(run)
    db.flush()
    _apply_scan_counts(run, scan)
    selected_customer_ids = {
        candidate.customer.customer_id
        for candidate in scan.candidates[: int(rules["max_targets"])]
    }
    for rank, candidate in enumerate(scan.evaluated_candidates, start=1):
        db.add(
            BulkTargetingCandidateSnapshot(
                run_id=run.id,
                customer_id=candidate.customer.customer_id,
                customer_insight_id=candidate.insight.id,
                rank=rank,
                eligible=candidate.exclusion_reason is None,
                selected=candidate.customer.customer_id in selected_customer_ids,
                exclusion_reason=candidate.exclusion_reason,
            )
        )
    db.commit()
    run = get_bulk_targeting_run(db, run.id) or run
    return run, scan


def execute_bulk_targeting(
    db: Session,
    *,
    run: BulkTargetingRun,
    actor: User,
) -> BulkTargetingRun:
    """고정된 preview 후보로 draft 캠페인과 대상을 원자적으로 생성합니다."""
    locked_run = db.scalar(
        select(BulkTargetingRun)
        .where(BulkTargetingRun.id == run.id)
        .with_for_update()
    )
    if locked_run is None:
        raise BulkTargetingDomainError("The bulk targeting run was not found.")
    if locked_run.status == BulkTargetingRunStatus.EXECUTED.value:
        return get_bulk_targeting_run(db, locked_run.id) or locked_run
    if locked_run.status != BulkTargetingRunStatus.PREVIEWED.value:
        raise BulkTargetingStateError(
            "Only a previewed bulk targeting run can be executed."
        )
    run = locked_run
    rules = dict(run.rules_json)
    candidate_snapshots = db.scalars(
        select(BulkTargetingCandidateSnapshot)
        .where(
            BulkTargetingCandidateSnapshot.run_id == run.id,
            BulkTargetingCandidateSnapshot.selected.is_(True),
        )
        .options(
            selectinload(BulkTargetingCandidateSnapshot.customer),
            selectinload(BulkTargetingCandidateSnapshot.customer_insight),
        )
        .order_by(BulkTargetingCandidateSnapshot.rank)
        .with_for_update()
    ).all()
    if not candidate_snapshots:
        raise BulkTargetingStateError(
            "The preview has no selected candidates to execute."
        )
    campaign = Campaign(
        name=str(rules["campaign_name"]),
        description=rules.get("description"),
        channel=rules.get("channel"),
        segment_code=run.segment_code,
        status=CampaignLifecycleStatus.DRAFT.value,
        experiment_enabled=bool(rules.get("experiment_enabled", False)),
        control_group_ratio=float(rules.get("control_group_ratio", 0.0)),
        experiment_seed=rules.get("experiment_seed") or secrets.token_hex(16),
        experiment_assignment_version="sha256_seed_customer_v1",
        fixed_cost=float(rules.get("fixed_cost", 0.0)),
        cost_per_contact=float(rules.get("cost_per_contact", 0.0)),
        revenue_per_conversion=float(rules.get("revenue_per_conversion", 0.0)),
        retention_window_days=int(rules.get("retention_window_days", 30)),
        created_by_user_id=actor.id,
    )
    db.add(campaign)
    db.flush()
    run.campaign_id = campaign.id
    run.campaign = campaign
    run.status = BulkTargetingRunStatus.EXECUTED.value
    run.executed_at = datetime.now(timezone.utc)
    _add_event(
        db,
        campaign=campaign,
        event_type=CampaignEventType.CREATED.value,
        to_status=campaign.status,
        actor=actor,
        note="Created by segment bulk targeting.",
        metadata_json={
            "bulk_targeting_run_id": run.id,
            "segment": run.segment_code,
            "eligible_count": run.eligible_count,
            "preview_count": run.preview_count,
            "scoring_batch_id": run.scoring_batch_id,
        },
    )

    assigned_to_user_id = rules.get("assigned_to_user_id")
    assignee = (
        db.get(User, int(assigned_to_user_id))
        if assigned_to_user_id is not None
        else None
    )
    run.created_count = 0
    for candidate_snapshot in candidate_snapshots:
        locked_customer = db.scalar(
            select(Customer)
            .where(Customer.customer_id == candidate_snapshot.customer_id)
            .with_for_update()
        )
        if locked_customer is None:
            candidate_snapshot.execution_status = "skipped"
            continue
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=int(rules["recent_contact_days"])
        )
        if locked_customer.marketing_opt_out:
            candidate_snapshot.execution_status = "skipped"
            candidate_snapshot.exclusion_reason = "opted_out"
            continue
        if _is_recent_contacted_at(locked_customer.last_contacted_at, cutoff):
            candidate_snapshot.execution_status = "skipped"
            candidate_snapshot.exclusion_reason = "recent_contact"
            continue
        try:
            target = create_campaign_target(
                db,
                campaign=campaign,
                insight=candidate_snapshot.customer_insight,
                assignee=assignee,
                actor=actor,
                commit=False,
            )
        except CampaignConflictError as exc:
            candidate_snapshot.execution_status = "skipped"
            message = str(exc).lower()
            if "opted out" in message:
                candidate_snapshot.exclusion_reason = "opted_out"
            elif "cooldown" in message:
                candidate_snapshot.exclusion_reason = "recent_contact"
            else:
                candidate_snapshot.exclusion_reason = "active_campaign"
            continue
        target.bulk_targeting_run_id = run.id
        db.flush()
        candidate_snapshot.execution_status = "created"
        candidate_snapshot.campaign_target_id = target.id
        run.created_count += 1

    db.commit()
    db.refresh(run)
    return run


def cancel_bulk_targeting(
    db: Session,
    *,
    run: BulkTargetingRun,
    actor: User,
) -> BulkTargetingRun:
    """preview를 폐기하거나 실행된 배치의 미처리 대상을 취소합니다."""
    locked_run = db.scalar(
        select(BulkTargetingRun)
        .where(BulkTargetingRun.id == run.id)
        .with_for_update()
    )
    if locked_run is None:
        raise BulkTargetingDomainError("The bulk targeting run was not found.")
    run = locked_run
    if run.status == BulkTargetingRunStatus.CANCELLED.value:
        return get_bulk_targeting_run(db, run.id) or run
    if run.status == BulkTargetingRunStatus.PREVIEWED.value:
        run.status = BulkTargetingRunStatus.CANCELLED.value
        run.cancelled_at = datetime.now(timezone.utc)
        db.execute(
            BulkTargetingCandidateSnapshot.__table__.update()
            .where(
                BulkTargetingCandidateSnapshot.run_id == run.id,
                BulkTargetingCandidateSnapshot.selected.is_(True),
                BulkTargetingCandidateSnapshot.execution_status == "pending",
            )
            .values(execution_status="cancelled")
        )
        db.commit()
        return get_bulk_targeting_run(db, run.id) or run

    now = datetime.now(timezone.utc)
    cancelled_count = 0
    targets = db.scalars(
        select(CampaignTarget)
        .where(
            CampaignTarget.bulk_targeting_run_id == run.id,
            CampaignTarget.status.in_(
                [CampaignStatus.PENDING.value, CampaignStatus.ASSIGNED.value]
            ),
        )
        .options(selectinload(CampaignTarget.campaign))
        .with_for_update()
    ).all()
    for target in targets:
        previous_status = target.status
        target.status = CampaignStatus.CANCELLED.value
        target.processed_at = now
        if target.campaign is not None:
            _add_event(
                db,
                campaign=target.campaign,
                target=target,
                event_type=CampaignEventType.STATUS_CHANGED.value,
                from_status=previous_status,
                to_status=target.status,
                actor=actor,
                note="Cancelled by bulk targeting run.",
            )
        cancelled_count += 1
        candidate_snapshot = db.scalar(
            select(BulkTargetingCandidateSnapshot).where(
                BulkTargetingCandidateSnapshot.run_id == run.id,
                BulkTargetingCandidateSnapshot.campaign_target_id == target.id,
            )
        )
        if candidate_snapshot is not None:
            candidate_snapshot.execution_status = "cancelled"
    run.status = BulkTargetingRunStatus.CANCELLED.value
    run.cancelled_at = now
    run.cancelled_target_count += cancelled_count
    db.commit()
    return get_bulk_targeting_run(db, run.id) or run


def rerun_bulk_targeting(
    db: Session,
    *,
    run: BulkTargetingRun,
    actor: User,
    campaign_name: str | None = None,
    max_targets: int | None = None,
) -> tuple[BulkTargetingRun, BulkTargetingScan]:
    """기존 정책을 복사해 새 preview run을 만들고 이전 배치를 유지합니다."""
    if run.status != BulkTargetingRunStatus.CANCELLED.value:
        raise BulkTargetingStateError(
            "Only a cancelled bulk targeting run can be rerun."
        )
    rules = dict(run.rules_json)
    rules["campaign_name"] = campaign_name or f"{rules['campaign_name']} 재실행"
    if max_targets is not None:
        rules["max_targets"] = max_targets
    payload = BulkTargetingPreviewRequest(
        segment=BulkTargetingSegment(run.segment_code),
        campaign_name=rules["campaign_name"],
        description=rules.get("description"),
        channel=rules.get("channel"),
        assigned_to_user_id=rules.get("assigned_to_user_id"),
        recent_contact_days=int(rules["recent_contact_days"]),
        activity_gap_quantile=float(rules["activity_gap_quantile"]),
        cluster_name=rules.get("cluster_name"),
        max_targets=int(rules["max_targets"]),
        source_as_of_date=_date_from_rules(rules),
        scoring_batch_id=int(rules["scoring_batch_id"]),
        experiment_enabled=bool(rules.get("experiment_enabled", False)),
        control_group_ratio=float(rules.get("control_group_ratio", 0.2)),
        fixed_cost=float(rules.get("fixed_cost", 0.0)),
        cost_per_contact=float(rules.get("cost_per_contact", 0.0)),
        revenue_per_conversion=float(rules.get("revenue_per_conversion", 0.0)),
        retention_window_days=int(rules.get("retention_window_days", 30)),
    )
    return preview_bulk_targeting(
        db,
        payload=payload,
        actor=actor,
        rerun_of_id=run.id,
        experiment_seed=str(rules["experiment_seed"]),
    )


def fetch_bulk_targeting_runs(
    db: Session,
    *,
    page: int,
    page_size: int,
) -> tuple[list[BulkTargetingRun], int]:
    """일괄 타기팅 실행 이력을 최신순으로 조회합니다."""
    query = select(BulkTargetingRun)
    total = int(
        db.scalar(select(func.count()).select_from(query.subquery())) or 0
    )
    items = db.scalars(
        query.options(selectinload(BulkTargetingRun.campaign))
        .order_by(BulkTargetingRun.created_at.desc(), BulkTargetingRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return items, total


def get_bulk_targeting_run(db: Session, run_id: int) -> BulkTargetingRun | None:
    """일괄 타기팅 배치 하나를 조회합니다."""
    return db.scalar(
        select(BulkTargetingRun)
        .options(
            selectinload(BulkTargetingRun.campaign),
            selectinload(BulkTargetingRun.candidate_snapshots).selectinload(
                BulkTargetingCandidateSnapshot.customer_insight
            ),
        )
        .where(BulkTargetingRun.id == run_id)
    )


def fetch_bulk_targeting_preview_insights(
    db: Session,
    run: BulkTargetingRun,
) -> list[CustomerInsight]:
    """DB에 고정된 미리보기 순서로 선택 고객의 인사이트를 반환합니다."""
    snapshots = db.scalars(
        select(BulkTargetingCandidateSnapshot)
        .where(
            BulkTargetingCandidateSnapshot.run_id == run.id,
            BulkTargetingCandidateSnapshot.selected.is_(True),
        )
        .options(selectinload(BulkTargetingCandidateSnapshot.customer_insight))
        .order_by(BulkTargetingCandidateSnapshot.rank)
    ).all()
    return [snapshot.customer_insight for snapshot in snapshots]
