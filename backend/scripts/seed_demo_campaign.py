"""로컬 시연용 합성 시계열·A/B 캠페인 데이터를 생성합니다.

실제 고객 원본을 변경하지 않고, 현재 고객을 기준으로 과거 분석 스냅샷과
성과 결과를 합성합니다. 운영 환경에서 실행되지 않도록 로컬 DB만 허용합니다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.config import get_database_url
from backend.app.database import initialize_database
from backend.scripts.db_safety import validate_local_database
from backend.app.enums import (
    BulkTargetingSegment,
    CampaignEventType,
    CampaignLifecycleStatus,
    CampaignResultCode,
    CampaignStatus,
    ExperimentGroup,
    ModelRunStatus,
    RiskLevel,
)
from backend.app.models import (
    Campaign,
    CampaignEvent,
    CampaignTarget,
    Customer,
    CustomerFeatureSnapshot,
    CustomerInsight,
    DecisionPolicy,
    ModelRun,
    ScoringBatch,
    User,
)


DEMO_PREFIX = "[DEMO]"
DEMO_DATES = (date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1))
DEMO_SOURCE_HASH = hashlib.sha256(b"cardops-demo-dataset-v1").hexdigest()
DEMO_POLICY_HASH = hashlib.sha256(b"cardops-demo-policy-v1").hexdigest()
SNAPSHOT_ATTRIBUTES = (
    "customer_age",
    "gender",
    "dependent_count",
    "education_level",
    "marital_status",
    "income_category",
    "card_category",
    "months_on_book",
    "total_relationship_count",
    "months_inactive_12_mon",
    "contacts_count_12_mon",
    "credit_limit",
    "total_revolving_bal",
    "avg_open_to_buy",
    "total_amt_chng_q4_q1",
    "total_trans_amt",
    "total_trans_ct",
    "total_ct_chng_q4_q1",
    "avg_utilization_ratio",
)


@dataclass(frozen=True)
class DemoCampaignSpec:
    """시연 캠페인별 합성 성과 비율입니다."""

    segment: BulkTargetingSegment
    title: str
    risk_level: RiskLevel
    limit: int
    treatment_conversion_rate: float
    control_conversion_rate: float
    treatment_retention_rate: float
    control_retention_rate: float
    revenue_per_conversion: float


CAMPAIGN_SPECS = (
    DemoCampaignSpec(
        segment=BulkTargetingSegment.HIGH_RISK_RETENTION,
        title="고위험 리텐션 A/B 테스트",
        risk_level=RiskLevel.HIGH,
        limit=180,
        treatment_conversion_rate=0.32,
        control_conversion_rate=0.10,
        treatment_retention_rate=0.68,
        control_retention_rate=0.47,
        revenue_per_conversion=120_000,
    ),
    DemoCampaignSpec(
        segment=BulkTargetingSegment.MEDIUM_REACTIVATION,
        title="중위험 재활성화 A/B 테스트",
        risk_level=RiskLevel.MEDIUM,
        limit=180,
        treatment_conversion_rate=0.26,
        control_conversion_rate=0.09,
        treatment_retention_rate=0.61,
        control_retention_rate=0.43,
        revenue_per_conversion=95_000,
    ),
    DemoCampaignSpec(
        segment=BulkTargetingSegment.LOW_RISK_UPSELL,
        title="우량 고객 업셀링 A/B 테스트",
        risk_level=RiskLevel.LOW,
        limit=180,
        treatment_conversion_rate=0.24,
        control_conversion_rate=0.12,
        treatment_retention_rate=0.76,
        control_retention_rate=0.58,
        revenue_per_conversion=150_000,
    ),
)


def build_parser() -> argparse.ArgumentParser:
    """시연 데이터 생성 CLI 옵션을 구성합니다."""
    parser = argparse.ArgumentParser(
        description="Seed synthetic time-series and A/B campaign data for local demos."
    )
    parser.add_argument(
        "--limit-per-campaign",
        type=int,
        default=None,
        help="캠페인별 대상 수를 줄입니다(기본값: 180, 최소 20).",
    )
    return parser


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _feature_values(customer: Customer, period_index: int) -> dict[str, Any]:
    """현재 고객 특성에 자연스러운 월별 변동을 더해 합성 입력을 만듭니다."""
    values = {
        attribute: getattr(customer, attribute)
        for attribute in SNAPSHOT_ATTRIBUTES
    }
    # 기간이 최근으로 올수록 활동량이 회복되는 흐름을 보여줍니다.
    inactivity_shift = 2 - period_index
    activity_factor = 0.86 + period_index * 0.07
    values["months_inactive_12_mon"] = int(
        _clamp(values["months_inactive_12_mon"] + inactivity_shift, 0, 12)
    )
    values["contacts_count_12_mon"] = max(
        0,
        int(round(values["contacts_count_12_mon"] - inactivity_shift * 0.4)),
    )
    values["total_trans_amt"] = max(
        0,
        int(round(values["total_trans_amt"] * activity_factor)),
    )
    values["total_trans_ct"] = max(
        0,
        int(round(values["total_trans_ct"] * activity_factor)),
    )
    values["total_amt_chng_q4_q1"] = max(
        0.0,
        round(values["total_amt_chng_q4_q1"] * activity_factor, 4),
    )
    values["total_ct_chng_q4_q1"] = max(
        0.0,
        round(values["total_ct_chng_q4_q1"] * activity_factor, 4),
    )
    values["avg_utilization_ratio"] = round(
        _clamp(values["avg_utilization_ratio"] - inactivity_shift * 0.015, 0, 1),
        6,
    )
    return values


def _feature_hash(values: dict[str, Any]) -> str:
    """스냅샷 입력 특성의 재현 가능한 SHA-256을 계산합니다."""
    encoded = json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _latest_insights(session: Session) -> dict[int, tuple[CustomerInsight, Customer]]:
    """고객별 현재 최신 분석 결과와 고객 레코드를 반환합니다."""
    rows = session.execute(
        select(CustomerInsight, Customer).join(
            Customer,
            Customer.customer_id == CustomerInsight.customer_id,
        )
    ).all()
    latest: dict[int, tuple[CustomerInsight, Customer]] = {}
    for insight, customer in rows:
        current = latest.get(customer.customer_id)
        current_key = (
            current[0].as_of_date or date.min,
            current[0].scored_at,
            current[0].id,
        ) if current is not None else None
        candidate_key = (
            insight.as_of_date or date.min,
            insight.scored_at,
            insight.id,
        )
        if current_key is None or candidate_key > current_key:
            latest[customer.customer_id] = (insight, customer)
    return latest


def _choose_customers(
    session: Session,
    *,
    limit: int,
) -> dict[BulkTargetingSegment, list[tuple[CustomerInsight, Customer]]]:
    """현재 최신 분석을 기준으로 세그먼트별 시연 고객을 고릅니다."""
    latest = _latest_insights(session)
    active_customer_ids = set(
        session.scalars(
            select(CampaignTarget.customer_id).where(
                CampaignTarget.status.in_(
                    [
                        CampaignStatus.PENDING.value,
                        CampaignStatus.ASSIGNED.value,
                        CampaignStatus.CONTACTED.value,
                    ]
                )
            )
        ).all()
    )
    available = [
        pair
        for customer_id, pair in latest.items()
        if customer_id not in active_customer_ids
    ]
    by_risk: dict[str, list[tuple[CustomerInsight, Customer]]] = {
        risk.value: [] for risk in RiskLevel
    }
    for pair in available:
        by_risk[pair[0].risk_level].append(pair)

    selected: dict[BulkTargetingSegment, list[tuple[CustomerInsight, Customer]]] = {}
    used_customer_ids: set[int] = set()
    for spec in CAMPAIGN_SPECS:
        candidates = [
            pair
            for pair in by_risk[spec.risk_level.value]
            if pair[1].customer_id not in used_customer_ids
        ]
        if spec.risk_level is RiskLevel.HIGH:
            candidates.sort(key=lambda pair: pair[0].churn_probability, reverse=True)
        elif spec.risk_level is RiskLevel.MEDIUM:
            candidates.sort(key=lambda pair: pair[0].activity_gap)
        else:
            candidates.sort(
                key=lambda pair: pair[0].expected_transaction_count,
                reverse=True,
            )
        chosen = candidates[:limit]
        if len(chosen) < limit:
            raise RuntimeError(
                f"Not enough {spec.risk_level.value} customers for the demo: "
                f"requested {limit}, available {len(chosen)}."
            )
        selected[spec.segment] = chosen
        used_customer_ids.update(pair[1].customer_id for pair in chosen)
    return selected


def _ensure_policy(session: Session) -> DecisionPolicy:
    """시연용 immutable 정책 레코드를 준비합니다."""
    policy = session.scalar(
        select(DecisionPolicy).where(
            DecisionPolicy.policy_sha256 == DEMO_POLICY_HASH,
        )
    )
    if policy is None:
        policy = DecisionPolicy(
            version="demo-synthetic-v1",
            policy_sha256=DEMO_POLICY_HASH,
            medium_threshold=0.5,
            high_threshold=0.85,
            activity_gap_quantile=0.2,
            policy_json={
                "demo": True,
                "source": "synthetic",
                "description": "Local-only synthetic policy for product demonstration.",
            },
        )
        session.add(policy)
        session.flush()
    return policy


def _create_scoring_history(
    session: Session,
    *,
    policy: DecisionPolicy,
    selected: dict[BulkTargetingSegment, list[tuple[CustomerInsight, Customer]]],
) -> dict[tuple[date, int], CustomerInsight]:
    """세 시점의 합성 스냅샷과 분석 결과를 생성합니다."""
    all_pairs = [pair for pairs in selected.values() for pair in pairs]
    historical_insights: dict[tuple[date, int], CustomerInsight] = {}
    for period_index, as_of_date in enumerate(DEMO_DATES):
        started_at = datetime.combine(
            as_of_date,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ) + timedelta(hours=9)
        batch_key = hashlib.sha256(
            f"{DEMO_PREFIX}:batch:{as_of_date.isoformat()}".encode()
        ).hexdigest()
        reuse_key = hashlib.sha256(
            f"{DEMO_PREFIX}:reuse:{as_of_date.isoformat()}".encode()
        ).hexdigest()
        batch = ScoringBatch(
            batch_key_sha256=batch_key,
            reuse_key_sha256=reuse_key,
            attempt_number=1,
            as_of_date=as_of_date,
            source_dataset_sha256=DEMO_SOURCE_HASH,
            dataset_sha256=DEMO_SOURCE_HASH,
            decision_policy_id=policy.id,
            status=ModelRunStatus.SUCCEEDED.value,
            processed_rows=len(all_pairs),
            started_at=started_at,
            completed_at=started_at + timedelta(minutes=5),
            created_at=started_at,
        )
        session.add(batch)
        session.flush()

        model_runs: dict[str, ModelRun] = {}
        for task in ("classification", "regression", "clustering"):
            artifact_hash = hashlib.sha256(
                f"{DEMO_PREFIX}:artifact:{task}".encode()
            ).hexdigest()
            model_run = ModelRun(
                task=task,
                model_name=f"demo-{task}",
                model_version="synthetic-v1",
                artifact_path=f"/demo/synthetic/{task}.joblib",
                artifact_sha256=artifact_hash,
                dataset_sha256=DEMO_SOURCE_HASH,
                scoring_batch_id=batch.id,
                decision_policy_sha256=policy.policy_sha256,
                medium_threshold=policy.medium_threshold,
                high_threshold=policy.high_threshold,
                activity_gap_quantile=policy.activity_gap_quantile,
                status=ModelRunStatus.SUCCEEDED.value,
                processed_rows=len(all_pairs),
                started_at=started_at,
                completed_at=started_at + timedelta(minutes=5),
                created_at=started_at,
            )
            session.add(model_run)
            model_runs[task] = model_run
        session.flush()

        for base_insight, customer in all_pairs:
            values = _feature_values(customer, period_index)
            snapshot = CustomerFeatureSnapshot(
                customer_id=customer.customer_id,
                feature_sha256=_feature_hash(values),
                source_dataset_sha256=DEMO_SOURCE_HASH,
                as_of_date=as_of_date,
                as_of_at=started_at,
                **values,
            )
            session.add(snapshot)
            session.flush()

            drift = (period_index - 1) * 0.045
            churn_probability = _clamp(
                float(base_insight.churn_probability) - drift,
                0.03,
                0.98,
            )
            activity_gap = round(float(base_insight.activity_gap) + drift * 12, 4)
            expected_transactions = max(
                0.0,
                round(
                    float(base_insight.expected_transaction_count)
                    * (0.9 + period_index * 0.06),
                    3,
                ),
            )
            insight = CustomerInsight(
                customer_id=customer.customer_id,
                customer_snapshot_id=snapshot.id,
                scoring_batch_id=batch.id,
                classification_run_id=model_runs["classification"].id,
                regression_run_id=model_runs["regression"].id,
                clustering_run_id=model_runs["clustering"].id,
                churn_probability=churn_probability,
                as_of_date=as_of_date,
                risk_level=base_insight.risk_level,
                expected_transaction_count=expected_transactions,
                activity_gap=activity_gap,
                cluster_name=base_insight.cluster_name,
                cluster_confidence=base_insight.cluster_confidence,
                recommended_action=base_insight.recommended_action,
                reason_codes={
                    "demo": True,
                    "synthetic_period": as_of_date.isoformat(),
                    "source": "synthetic_demo_seed",
                },
                scored_at=started_at + timedelta(minutes=5),
                created_at=started_at + timedelta(minutes=5),
            )
            session.add(insight)
            session.flush()
            historical_insights[(as_of_date, customer.customer_id)] = insight
    return historical_insights


def _add_campaign_event(
    session: Session,
    *,
    campaign_id: int,
    target_id: int | None,
    event_type: CampaignEventType,
    actor_user_id: int | None,
    created_at: datetime,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        CampaignEvent(
            campaign_id=campaign_id,
            campaign_target_id=target_id,
            event_type=event_type.value,
            actor_user_id=actor_user_id,
            from_status=from_status,
            to_status=to_status,
            note=note,
            metadata_json=metadata or {"demo": True},
            created_at=created_at,
        )
    )


def _create_campaigns(
    session: Session,
    *,
    selected: dict[BulkTargetingSegment, list[tuple[CustomerInsight, Customer]]],
    historical_insights: dict[tuple[date, int], CustomerInsight],
    admin: User | None,
    operations: User | None,
    limit: int,
) -> int:
    """성과 대시보드에서 바로 조회할 합성 A/B 캠페인을 생성합니다."""
    campaign_count = 0
    campaign_start = datetime(2026, 7, 1, 9, tzinfo=timezone.utc)
    campaign_end = datetime(2026, 7, 31, 18, tzinfo=timezone.utc)
    observed_at = datetime(2026, 8, 1, 18, tzinfo=timezone.utc)
    for spec_index, spec in enumerate(CAMPAIGN_SPECS):
        campaign_name = f"{DEMO_PREFIX} {spec.title}"
        campaign = Campaign(
            name=campaign_name,
            description=(
                "시연용 합성 데이터입니다. 대상군·대조군의 전환·유지·ROI 차이를 "
                "확인하기 위한 로컬 Demo 캠페인입니다."
            ),
            channel="전화",
            segment_code=spec.segment.value,
            status=CampaignLifecycleStatus.COMPLETED.value,
            start_at=campaign_start,
            end_at=campaign_end,
            experiment_enabled=True,
            control_group_ratio=0.2,
            experiment_seed=hashlib.sha256(
                f"{DEMO_PREFIX}:campaign:{spec.segment.value}".encode()
            ).hexdigest()[:32],
            experiment_assignment_version="sha256_seed_customer_v1",
            fixed_cost=180_000,
            cost_per_contact=3_500,
            revenue_per_conversion=spec.revenue_per_conversion,
            retention_window_days=30,
            created_by_user_id=admin.id if admin is not None else None,
            created_at=campaign_start,
            updated_at=campaign_end,
        )
        session.add(campaign)
        session.flush()
        campaign_count += 1

        _add_campaign_event(
            session,
            campaign_id=campaign.id,
            target_id=None,
            event_type=CampaignEventType.CREATED,
            actor_user_id=admin.id if admin is not None else None,
            created_at=campaign_start,
            to_status=CampaignLifecycleStatus.DRAFT.value,
            note="시연용 캠페인 생성",
        )
        for from_status, to_status, event_time in (
            (
                CampaignLifecycleStatus.DRAFT.value,
                CampaignLifecycleStatus.SCHEDULED.value,
                campaign_start + timedelta(minutes=1),
            ),
            (
                CampaignLifecycleStatus.SCHEDULED.value,
                CampaignLifecycleStatus.ACTIVE.value,
                campaign_start + timedelta(minutes=2),
            ),
            (
                CampaignLifecycleStatus.ACTIVE.value,
                CampaignLifecycleStatus.COMPLETED.value,
                campaign_end,
            ),
        ):
            _add_campaign_event(
                session,
                campaign_id=campaign.id,
                target_id=None,
                event_type=CampaignEventType.STATUS_CHANGED,
                actor_user_id=admin.id if admin is not None else None,
                created_at=event_time,
                from_status=from_status,
                to_status=to_status,
                note="시연용 캠페인 상태 변경",
            )

        for target_index, (base_insight, customer) in enumerate(
            selected[spec.segment][:limit]
        ):
            target_rng = random.Random(
                20260802 + customer.customer_id + spec_index * 100_000
            )
            is_control = target_index % 5 == 0
            treatment_total = sum(
                index % 5 != 0 for index in range(limit)
            )
            control_total = limit - treatment_total
            treatment_position = sum(
                index % 5 != 0 for index in range(target_index)
            )
            control_position = sum(
                index % 5 == 0 for index in range(target_index)
            )
            experiment_group = (
                ExperimentGroup.CONTROL.value
                if is_control
                else ExperimentGroup.TREATMENT.value
            )
            conversion_rate = (
                spec.control_conversion_rate
                if is_control
                else spec.treatment_conversion_rate
            )
            retention_rate = (
                spec.control_retention_rate
                if is_control
                else spec.treatment_retention_rate
            )
            conversion_target_count = max(
                1,
                round(
                    conversion_rate
                    * (control_total if is_control else treatment_total)
                ),
            )
            retention_target_count = max(
                1,
                round(
                    retention_rate
                    * (control_total if is_control else treatment_total)
                ),
            )
            converted = (
                control_position if is_control else treatment_position
            ) < conversion_target_count
            retained = (
                control_position if is_control else treatment_position
            ) < retention_target_count
            completed_at = (
                None
                if is_control
                else campaign_start + timedelta(days=1)
            )
            contacted_at = None if is_control else campaign_start + timedelta(days=1)
            converted_at = observed_at if converted else None
            result_code = (
                CampaignResultCode.CONVERTED.value
                if converted
                else CampaignResultCode.NOT_CONVERTED.value
            )
            result = (
                "자연 전환(대조군 시연)"
                if is_control and converted
                else "대조군 관측 완료"
                if is_control
                else "혜택 안내 후 전환"
                if converted
                else "혜택 안내 완료·미전환"
            )
            outcome_revenue = (
                round(
                    spec.revenue_per_conversion
                    * (0.85 + target_rng.random() * 0.3),
                    2,
                )
                if converted
                else None
            )
            target = CampaignTarget(
                customer_id=customer.customer_id,
                customer_insight_id=historical_insights[
                    (DEMO_DATES[-1], customer.customer_id)
                ].id,
                campaign_id=campaign.id,
                campaign_name=campaign_name,
                experiment_group=experiment_group,
                assigned_to_user_id=(
                    operations.id
                    if not is_control and operations is not None
                    else None
                ),
                status=(
                    CampaignStatus.PENDING.value
                    if is_control
                    else CampaignStatus.COMPLETED.value
                ),
                processed_at=completed_at,
                contacted_at=contacted_at,
                completed_at=completed_at,
                converted_at=converted_at,
                result=result,
                result_notes="시연용 합성 결과이며 실제 고객 접촉 결과가 아닙니다.",
                result_code=result_code,
                converted=converted,
                retained=retained,
                retention_checked_at=observed_at,
                outcome_revenue=outcome_revenue,
                created_at=campaign_start,
                updated_at=observed_at,
            )
            session.add(target)
            session.flush()
            _add_campaign_event(
                session,
                campaign_id=campaign.id,
                target_id=target.id,
                event_type=CampaignEventType.CREATED,
                actor_user_id=admin.id if admin is not None else None,
                created_at=campaign_start,
                from_status=None,
                to_status=CampaignStatus.PENDING.value,
                note="시연용 대상 생성",
            )
            if not is_control:
                _add_campaign_event(
                    session,
                    campaign_id=campaign.id,
                    target_id=target.id,
                    event_type=CampaignEventType.ASSIGNED,
                    actor_user_id=admin.id if admin is not None else None,
                    created_at=campaign_start + timedelta(minutes=3),
                    from_status=CampaignStatus.PENDING.value,
                    to_status=CampaignStatus.ASSIGNED.value,
                    note="시연용 운영 담당자 배정",
                )
                _add_campaign_event(
                    session,
                    campaign_id=campaign.id,
                    target_id=target.id,
                    event_type=CampaignEventType.STATUS_CHANGED,
                    actor_user_id=operations.id if operations is not None else None,
                    created_at=contacted_at or campaign_start,
                    from_status=CampaignStatus.ASSIGNED.value,
                    to_status=CampaignStatus.CONTACTED.value,
                    note="시연용 대상군 접촉",
                )
                _add_campaign_event(
                    session,
                    campaign_id=campaign.id,
                    target_id=target.id,
                    event_type=CampaignEventType.STATUS_CHANGED,
                    actor_user_id=operations.id if operations is not None else None,
                    created_at=completed_at or campaign_start,
                    from_status=CampaignStatus.CONTACTED.value,
                    to_status=CampaignStatus.COMPLETED.value,
                    note="시연용 대상군 처리 완료",
                )
            _add_campaign_event(
                session,
                campaign_id=campaign.id,
                target_id=target.id,
                event_type=CampaignEventType.CONVERSION_UPDATED,
                actor_user_id=operations.id if operations is not None else None,
                created_at=observed_at,
                note="시연용 전환·유지 관측 결과",
                metadata={
                    "demo": True,
                    "experiment_group": experiment_group,
                    "converted": converted,
                    "retained": retained,
                    "outcome_revenue": outcome_revenue,
                },
            )
    return campaign_count


def _remove_existing_demo_data(session: Session) -> int:
    """이전 시연 데이터를 지웁니다(재실행 가능하게).

    `import_customers --replace`는 customers와 그 자식만 지우고 campaigns와
    scoring_batches는 남깁니다. 그대로 두면 대상이 0명인 빈 캠페인이 남는데,
    예전에는 이 상태에서 재실행해도 "이미 있음"으로 건너뛰어 복구가 되지
    않았습니다. batch_key_sha256 유일 제약 때문에 시연용 배치도 함께 지워야
    다시 만들 수 있습니다.
    """
    demo_names = [f"{DEMO_PREFIX} {spec.title}" for spec in CAMPAIGN_SPECS]
    campaign_ids = list(
        session.scalars(select(Campaign.id).where(Campaign.name.in_(demo_names))).all()
    )
    if campaign_ids:
        session.execute(
            delete(CampaignEvent).where(CampaignEvent.campaign_id.in_(campaign_ids))
        )
        session.execute(
            delete(CampaignTarget).where(CampaignTarget.campaign_id.in_(campaign_ids))
        )
        session.execute(delete(Campaign).where(Campaign.id.in_(campaign_ids)))

    batch_ids = list(
        session.scalars(
            select(ScoringBatch.id).where(
                ScoringBatch.dataset_sha256 == DEMO_SOURCE_HASH
            )
        ).all()
    )
    if batch_ids:
        # insights는 snapshots를 참조하므로 자식 → 부모 순서를 지킵니다.
        session.execute(
            delete(CustomerInsight).where(
                CustomerInsight.scoring_batch_id.in_(batch_ids)
            )
        )
        session.execute(
            delete(ModelRun).where(ModelRun.scoring_batch_id.in_(batch_ids))
        )
        session.execute(delete(ScoringBatch).where(ScoringBatch.id.in_(batch_ids)))
    session.execute(
        delete(CustomerFeatureSnapshot).where(
            CustomerFeatureSnapshot.source_dataset_sha256 == DEMO_SOURCE_HASH
        )
    )
    session.flush()
    return len(campaign_ids)


def seed_demo_data(session: Session, *, limit: int) -> dict[str, int]:
    """합성 시계열과 A/B 캠페인을 한 트랜잭션으로 생성합니다."""
    removed_campaigns = _remove_existing_demo_data(session)

    admin = session.scalar(
        select(User)
        .where(User.role == "admin", User.is_active.is_(True))
        .order_by(User.id)
    )
    operations = session.scalar(
        select(User)
        .where(User.role == "operations", User.is_active.is_(True))
        .order_by(User.id)
    )
    selected = _choose_customers(session, limit=limit)
    policy = _ensure_policy(session)
    historical_insights = _create_scoring_history(
        session,
        policy=policy,
        selected=selected,
    )
    campaign_count = _create_campaigns(
        session,
        selected=selected,
        historical_insights=historical_insights,
        admin=admin,
        operations=operations,
        limit=limit,
    )
    session.commit()
    return {
        "removed_campaigns": removed_campaigns,
        "campaigns": campaign_count,
        "customers": limit * len(CAMPAIGN_SPECS),
        "snapshots": limit * len(CAMPAIGN_SPECS) * len(DEMO_DATES),
        "insights": limit * len(CAMPAIGN_SPECS) * len(DEMO_DATES),
        "targets": limit * len(CAMPAIGN_SPECS),
    }


def main() -> None:
    """환경변수 DB에 로컬 시연 데이터를 적재합니다."""
    args = build_parser().parse_args()
    limit = args.limit_per_campaign or CAMPAIGN_SPECS[0].limit
    if limit < 20:
        raise ValueError("--limit-per-campaign must be at least 20.")
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("DATABASE_URL must be configured before seeding demo data.")
    validate_local_database(database_url)

    engine, session_factory = initialize_database(database_url)
    try:
        with session_factory() as session:
            summary = seed_demo_data(session, limit=limit)
    finally:
        engine.dispose()

    if summary.get("removed_campaigns"):
        print(f"기존 [DEMO] 캠페인 {summary['removed_campaigns']}개를 제거하고 다시 만듭니다.")
    print("Demo data seeded:")
    for key, value in summary.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
