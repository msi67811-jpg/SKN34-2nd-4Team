"""캠페인 도메인의 조회·상태 전이·이력·중복 방지 규칙입니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session, selectinload

from ..enums import (
    CampaignEventType,
    CampaignLifecycleStatus,
    CampaignResultCode,
    CampaignStatus,
    ExperimentGroup,
    UserRole,
)
from ..models import Campaign, CampaignEvent, CampaignTarget, Customer, CustomerInsight, User


MUTABLE_ASSIGNEE_ROLES = {UserRole.OPERATIONS.value}
DEFAULT_CONTACT_COOLDOWN_DAYS = 30
# 개별 대상 등록 시 "더 급한 캠페인에 이미 걸린 고객인가"를 판정하는 우선순위입니다.
# 일괄 타기팅(bulk_targeting_service.SEGMENT_TARGETING_PRIORITIES)과 **같은 값**을
# 유지해야 두 경로의 중복 접촉 방지 규칙이 어긋나지 않습니다.
SEGMENT_PRIORITIES = {
    "small_balance_decline": 400,
    "dormant_full_payer": 350,
    "high_risk_retention": 300,
    "medium_reactivation": 200,
    "active_full_payer": 120,
    "low_risk_upsell": 100,
    "stable_prime": 90,
}
UNCLASSIFIED_CAMPAIGN_PRIORITY = 1000
OPEN_TARGET_STATUSES = {
    CampaignStatus.PENDING.value,
    CampaignStatus.ASSIGNED.value,
    CampaignStatus.CONTACTED.value,
}
OPEN_CAMPAIGN_STATUSES = {
    CampaignLifecycleStatus.DRAFT.value,
    CampaignLifecycleStatus.SCHEDULED.value,
    CampaignLifecycleStatus.ACTIVE.value,
    CampaignLifecycleStatus.PAUSED.value,
}
FINAL_RESULT_CODES = {
    CampaignResultCode.CONVERTED.value,
    CampaignResultCode.NOT_CONVERTED.value,
    CampaignResultCode.NO_RESPONSE.value,
    CampaignResultCode.DECLINED.value,
    CampaignResultCode.OPTED_OUT.value,
    CampaignResultCode.INVALID_CONTACT.value,
}

CAMPAIGN_STATUS_TRANSITIONS: dict[str, set[str]] = {
    CampaignLifecycleStatus.DRAFT.value: {
        CampaignLifecycleStatus.SCHEDULED.value,
        CampaignLifecycleStatus.ACTIVE.value,
        CampaignLifecycleStatus.CANCELLED.value,
    },
    CampaignLifecycleStatus.SCHEDULED.value: {
        CampaignLifecycleStatus.ACTIVE.value,
        CampaignLifecycleStatus.PAUSED.value,
        CampaignLifecycleStatus.CANCELLED.value,
    },
    CampaignLifecycleStatus.ACTIVE.value: {
        CampaignLifecycleStatus.PAUSED.value,
        CampaignLifecycleStatus.COMPLETED.value,
        CampaignLifecycleStatus.CANCELLED.value,
    },
    CampaignLifecycleStatus.PAUSED.value: {
        CampaignLifecycleStatus.ACTIVE.value,
        CampaignLifecycleStatus.COMPLETED.value,
        CampaignLifecycleStatus.CANCELLED.value,
    },
    CampaignLifecycleStatus.COMPLETED.value: set(),
    CampaignLifecycleStatus.CANCELLED.value: set(),
}

TARGET_STATUS_TRANSITIONS: dict[str, set[str]] = {
    CampaignStatus.PENDING.value: {
        CampaignStatus.ASSIGNED.value,
        CampaignStatus.CANCELLED.value,
    },
    CampaignStatus.ASSIGNED.value: {
        CampaignStatus.PENDING.value,
        CampaignStatus.CONTACTED.value,
        CampaignStatus.CANCELLED.value,
    },
    CampaignStatus.CONTACTED.value: {
        CampaignStatus.COMPLETED.value,
        CampaignStatus.CANCELLED.value,
    },
    CampaignStatus.COMPLETED.value: set(),
    CampaignStatus.CANCELLED.value: set(),
}


class CampaignDomainError(ValueError):
    """캠페인 업무 규칙 위반을 나타냅니다."""


class CampaignConflictError(CampaignDomainError):
    """중복 대상·중복 활성 업무처럼 현재 상태와 충돌하는 요청입니다."""


class CampaignTransitionError(CampaignDomainError):
    """허용되지 않은 캠페인 또는 대상 상태 전이입니다."""


class CampaignAssigneeError(CampaignDomainError):
    """캠페인 담당자로 지정할 수 없는 사용자입니다."""


@dataclass(frozen=True)
class CampaignStats:
    """캠페인 대상의 서버 집계 결과입니다."""

    total_targets: int
    unprocessed_targets: int
    contacted_targets: int
    converted_targets: int
    status_counts: dict[str, int]


@dataclass(frozen=True)
class CampaignTargetPage:
    """필터·페이지네이션 대상 목록과 집계 결과입니다."""

    items: list[CampaignTarget]
    total: int
    stats: CampaignStats


def _target_conditions(
    *,
    campaign_id: int | None = None,
    campaign_name: str | None = None,
    status: CampaignStatus | None = None,
    assigned_to_user_id: int | None = None,
    customer_id: int | None = None,
    converted: bool | None = None,
) -> list[Any]:
    conditions: list[Any] = []
    if campaign_id is not None:
        conditions.append(CampaignTarget.campaign_id == campaign_id)
    if campaign_name is not None:
        conditions.append(CampaignTarget.campaign_name == campaign_name)
    if status is not None:
        conditions.append(CampaignTarget.status == status.value)
    if assigned_to_user_id is not None:
        conditions.append(CampaignTarget.assigned_to_user_id == assigned_to_user_id)
    if customer_id is not None:
        conditions.append(CampaignTarget.customer_id == customer_id)
    if converted is not None:
        conditions.append(CampaignTarget.converted.is_(converted))
    return conditions


def _campaign_stats_query(conditions: list[Any]):
    return select(
        func.count(CampaignTarget.id),
        func.coalesce(
            func.sum(
                case(
                    (CampaignTarget.status.in_(
                        [CampaignStatus.PENDING.value, CampaignStatus.ASSIGNED.value]
                    ) & (
                        CampaignTarget.experiment_group
                        != ExperimentGroup.CONTROL.value
                    ), 1),
                    else_=0,
                )
            ),
            0,
        ),
        func.coalesce(
            func.sum(
                case(
                    (CampaignTarget.status.in_(
                        [CampaignStatus.CONTACTED.value, CampaignStatus.COMPLETED.value]
                    ), 1),
                    else_=0,
                )
            ),
            0,
        ),
        func.coalesce(
            func.sum(case((CampaignTarget.converted.is_(True), 1), else_=0)),
            0,
        ),
        *(
            func.coalesce(
                func.sum(
                    case((CampaignTarget.status == status.value, 1), else_=0)
                ),
                0,
            )
            for status in CampaignStatus
        ),
    ).where(*conditions)


def _to_stats(row: Any) -> CampaignStats:
    values = tuple(row)
    return CampaignStats(
        total_targets=int(values[0] or 0),
        unprocessed_targets=int(values[1] or 0),
        contacted_targets=int(values[2] or 0),
        converted_targets=int(values[3] or 0),
        status_counts={
            status.value: int(values[index] or 0)
            for index, status in enumerate(CampaignStatus, start=4)
        },
    )


def fetch_campaigns(
    db: Session,
    *,
    status: CampaignLifecycleStatus | None,
    name: str | None,
    created_by_user_id: int | None,
    page: int,
    page_size: int,
) -> tuple[list[Campaign], int, dict[int, CampaignStats]]:
    """캠페인 목록과 캠페인별 서버 집계를 조회합니다."""
    query: Select[tuple[Campaign]] = select(Campaign)
    if status is not None:
        query = query.where(Campaign.status == status.value)
    if name:
        query = query.where(Campaign.name.ilike(f"%{name}%"))
    if created_by_user_id is not None:
        query = query.where(Campaign.created_by_user_id == created_by_user_id)

    total = int(
        db.scalar(
            select(func.count()).select_from(query.order_by(None).subquery())
        )
        or 0
    )
    items = db.scalars(
        query.options(selectinload(Campaign.created_by))
        .order_by(Campaign.created_at.desc(), Campaign.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    ids = [campaign.id for campaign in items]
    stats_by_campaign: dict[int, CampaignStats] = {}
    if ids:
        rows = db.execute(
            _campaign_stats_query([CampaignTarget.campaign_id.in_(ids)])
            .add_columns(CampaignTarget.campaign_id)
            .group_by(CampaignTarget.campaign_id)
        ).all()
        for row in rows:
            stats_by_campaign[int(row[-1])] = _to_stats(row[:-1])
    return items, total, stats_by_campaign


def fetch_campaign(
    db: Session,
    campaign_id: int,
) -> Campaign | None:
    """캠페인 하나를 담당자·대상·이벤트 관계와 함께 조회합니다."""
    return db.scalar(
        select(Campaign)
        .options(
            selectinload(Campaign.created_by),
            selectinload(Campaign.targets).selectinload(CampaignTarget.assignee),
        )
        .where(Campaign.id == campaign_id)
    )


def validate_campaign_period(
    start_at: datetime | None,
    end_at: datetime | None,
) -> None:
    """캠페인 종료 시각이 시작 시각보다 빠르지 않은지 검증합니다."""
    if (
        start_at is not None
        and end_at is not None
        and _utc(end_at) < _utc(start_at)
    ):
        raise CampaignDomainError("Campaign end_at must be after start_at.")


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def validate_campaign_lifecycle_dates(
    lifecycle_status: CampaignLifecycleStatus,
    *,
    start_at: datetime | None,
    end_at: datetime | None,
    now: datetime | None = None,
) -> None:
    """예약·진행 상태가 실제 실행 기간과 모순되지 않게 검증합니다."""
    current = now or datetime.now(timezone.utc)
    if lifecycle_status == CampaignLifecycleStatus.SCHEDULED:
        if start_at is None:
            raise CampaignDomainError("A scheduled campaign requires start_at.")
        if _utc(start_at) <= current:
            raise CampaignDomainError("A scheduled campaign must start in the future.")
    if lifecycle_status == CampaignLifecycleStatus.ACTIVE:
        if start_at is not None and _utc(start_at) > current:
            raise CampaignDomainError("A campaign cannot be active before start_at.")
        if end_at is not None and _utc(end_at) <= current:
            raise CampaignDomainError("A campaign cannot be active after end_at.")


def validate_campaign_experiment(
    *,
    experiment_enabled: bool,
    control_group_ratio: float,
    fixed_cost: float,
    cost_per_contact: float,
    revenue_per_conversion: float,
    retention_window_days: int,
) -> None:
    """A/B 배정과 재무 입력의 업무 범위를 검증합니다."""
    if not 0 <= control_group_ratio < 1:
        raise CampaignDomainError("control_group_ratio must be between 0 and 1.")
    if experiment_enabled and control_group_ratio <= 0:
        raise CampaignDomainError(
            "An A/B campaign must reserve a positive control group ratio."
        )
    if min(fixed_cost, cost_per_contact, revenue_per_conversion) < 0:
        raise CampaignDomainError("Campaign financial values cannot be negative.")
    if not 1 <= retention_window_days <= 365:
        raise CampaignDomainError("retention_window_days must be between 1 and 365.")


def assign_experiment_group(
    campaign: Campaign,
    customer_id: int,
) -> str:
    """실험 seed와 고객 ID로 재실행에도 동일한 A/B 그룹을 배정합니다."""
    if not campaign.experiment_enabled or campaign.control_group_ratio <= 0:
        return ExperimentGroup.TREATMENT.value
    seed = campaign.experiment_seed or str(campaign.id)
    assignment_version = (
        campaign.experiment_assignment_version or "sha256_campaign_customer_v1"
    )
    assignment_key = (
        f"{seed}:{campaign.id}:{customer_id}"
        if assignment_version == "sha256_campaign_customer_v1"
        else f"{seed}:{customer_id}"
    )
    digest = hashlib.sha256(assignment_key.encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(2**64)
    if bucket < campaign.control_group_ratio:
        return ExperimentGroup.CONTROL.value
    return ExperimentGroup.TREATMENT.value


def create_campaign(
    db: Session,
    *,
    name: str,
    description: str | None,
    channel: str | None,
    lifecycle_status: CampaignLifecycleStatus,
    start_at: datetime | None,
    end_at: datetime | None,
    actor: User,
    segment_code: str | None = None,
    experiment_enabled: bool = False,
    control_group_ratio: float = 0.0,
    experiment_seed: str | None = None,
    fixed_cost: float = 0.0,
    cost_per_contact: float = 0.0,
    revenue_per_conversion: float = 0.0,
    retention_window_days: int = 30,
) -> Campaign:
    """캠페인 기본 정보와 생성 이벤트를 저장합니다."""
    if lifecycle_status != CampaignLifecycleStatus.DRAFT:
        raise CampaignTransitionError(
            "A campaign must be created as draft and activated through a status transition."
        )
    validate_campaign_period(start_at, end_at)
    validate_campaign_experiment(
        experiment_enabled=experiment_enabled,
        control_group_ratio=control_group_ratio,
        fixed_cost=fixed_cost,
        cost_per_contact=cost_per_contact,
        revenue_per_conversion=revenue_per_conversion,
        retention_window_days=retention_window_days,
    )
    campaign = Campaign(
        name=name,
        description=description,
        channel=channel,
        segment_code=segment_code,
        status=lifecycle_status.value,
        start_at=start_at,
        end_at=end_at,
        experiment_enabled=experiment_enabled,
        control_group_ratio=control_group_ratio,
        experiment_seed=experiment_seed or secrets.token_hex(16),
        experiment_assignment_version="sha256_seed_customer_v1",
        fixed_cost=fixed_cost,
        cost_per_contact=cost_per_contact,
        revenue_per_conversion=revenue_per_conversion,
        retention_window_days=retention_window_days,
        created_by_user_id=actor.id,
    )
    db.add(campaign)
    db.flush()
    _add_event(
        db,
        campaign=campaign,
        event_type=CampaignEventType.CREATED.value,
        to_status=campaign.status,
        actor=actor,
    )
    db.commit()
    db.refresh(campaign)
    campaign.created_by = actor
    return campaign


def update_campaign(
    db: Session,
    *,
    campaign: Campaign,
    name: str | None,
    description: str | None,
    channel: str | None,
    lifecycle_status: CampaignLifecycleStatus | None,
    start_at: datetime | None,
    end_at: datetime | None,
    actor: User,
    provided_fields: set[str],
    segment_code: str | None = None,
    experiment_enabled: bool | None = None,
    control_group_ratio: float | None = None,
    experiment_seed: str | None = None,
    fixed_cost: float | None = None,
    cost_per_contact: float | None = None,
    revenue_per_conversion: float | None = None,
    retention_window_days: int | None = None,
) -> Campaign:
    """캠페인 정보와 생명주기 상태를 허용된 범위에서 변경합니다."""
    terminal_statuses = {
        CampaignLifecycleStatus.COMPLETED.value,
        CampaignLifecycleStatus.CANCELLED.value,
    }
    has_requested_update = bool(provided_fields)
    if campaign.status in terminal_statuses and has_requested_update:
        raise CampaignConflictError("A closed campaign is immutable.")

    new_start = start_at if "start_at" in provided_fields else campaign.start_at
    new_end = end_at if "end_at" in provided_fields else campaign.end_at
    validate_campaign_period(new_start, new_end)
    effective_lifecycle_status = (
        lifecycle_status
        if lifecycle_status is not None
        else CampaignLifecycleStatus(campaign.status)
    )
    if lifecycle_status is not None and lifecycle_status.value != campaign.status:
        allowed = CAMPAIGN_STATUS_TRANSITIONS.get(campaign.status, set())
        if lifecycle_status.value not in allowed:
            raise CampaignTransitionError(
                f"Campaign status cannot change from {campaign.status} "
                f"to {lifecycle_status.value}."
            )
    validate_campaign_lifecycle_dates(
        effective_lifecycle_status,
        start_at=new_start,
        end_at=new_end,
    )
    target_count = int(
        db.scalar(
            select(func.count(CampaignTarget.id)).where(
                CampaignTarget.campaign_id == campaign.id
            )
        )
        or 0
    )
    experiment_or_finance_changes = any(
        (
            experiment_enabled is not None
            and experiment_enabled != bool(campaign.experiment_enabled),
            control_group_ratio is not None
            and control_group_ratio != campaign.control_group_ratio,
            experiment_seed is not None
            and experiment_seed != campaign.experiment_seed,
            fixed_cost is not None
            and fixed_cost != float(campaign.fixed_cost),
            cost_per_contact is not None
            and cost_per_contact != float(campaign.cost_per_contact),
            revenue_per_conversion is not None
            and revenue_per_conversion != float(campaign.revenue_per_conversion),
            retention_window_days is not None
            and retention_window_days != campaign.retention_window_days,
            "segment_code" in provided_fields
            and segment_code != campaign.segment_code,
        )
    )
    if target_count and experiment_or_finance_changes:
        raise CampaignConflictError(
            "Experiment, segment, and financial policies are immutable after targets exist."
        )
    next_experiment_enabled = (
        campaign.experiment_enabled
        if experiment_enabled is None
        else experiment_enabled
    )
    next_control_group_ratio = (
        campaign.control_group_ratio
        if control_group_ratio is None
        else control_group_ratio
    )
    next_fixed_cost = campaign.fixed_cost if fixed_cost is None else fixed_cost
    next_cost_per_contact = (
        campaign.cost_per_contact
        if cost_per_contact is None
        else cost_per_contact
    )
    next_revenue_per_conversion = (
        campaign.revenue_per_conversion
        if revenue_per_conversion is None
        else revenue_per_conversion
    )
    next_retention_window_days = (
        campaign.retention_window_days
        if retention_window_days is None
        else retention_window_days
    )
    validate_campaign_experiment(
        experiment_enabled=next_experiment_enabled,
        control_group_ratio=next_control_group_ratio,
        fixed_cost=next_fixed_cost,
        cost_per_contact=next_cost_per_contact,
        revenue_per_conversion=next_revenue_per_conversion,
        retention_window_days=next_retention_window_days,
    )
    if "name" in provided_fields and name is not None:
        if name != campaign.name and target_count:
            db.execute(
                CampaignTarget.__table__.update()
                .where(CampaignTarget.campaign_id == campaign.id)
                .values(campaign_name=name)
            )
        campaign.name = name
    if "start_at" in provided_fields:
        campaign.start_at = new_start
    if "end_at" in provided_fields:
        campaign.end_at = new_end
    if "description" in provided_fields:
        campaign.description = description
    if "channel" in provided_fields:
        campaign.channel = channel
    if "segment_code" in provided_fields:
        campaign.segment_code = segment_code
    if experiment_enabled is not None:
        campaign.experiment_enabled = experiment_enabled
    if control_group_ratio is not None:
        campaign.control_group_ratio = control_group_ratio
    if experiment_seed is not None:
        campaign.experiment_seed = experiment_seed
    if fixed_cost is not None:
        campaign.fixed_cost = fixed_cost
    if cost_per_contact is not None:
        campaign.cost_per_contact = cost_per_contact
    if revenue_per_conversion is not None:
        campaign.revenue_per_conversion = revenue_per_conversion
    if retention_window_days is not None:
        campaign.retention_window_days = retention_window_days

    if lifecycle_status is not None and lifecycle_status.value != campaign.status:
        if lifecycle_status == CampaignLifecycleStatus.COMPLETED:
            open_treatment_count = int(
                db.scalar(
                    select(func.count(CampaignTarget.id)).where(
                        CampaignTarget.campaign_id == campaign.id,
                        CampaignTarget.experiment_group
                        != ExperimentGroup.CONTROL.value,
                        CampaignTarget.status.in_(OPEN_TARGET_STATUSES),
                    )
                )
                or 0
            )
            if open_treatment_count:
                raise CampaignConflictError(
                    "A campaign cannot be completed while treatment targets remain open."
                )
        previous_status = campaign.status
        campaign.status = lifecycle_status.value
        if lifecycle_status == CampaignLifecycleStatus.ACTIVE and campaign.start_at is None:
            campaign.start_at = datetime.now(timezone.utc)
        if lifecycle_status == CampaignLifecycleStatus.CANCELLED:
            now = datetime.now(timezone.utc)
            open_targets = db.scalars(
                select(CampaignTarget)
                .where(
                    CampaignTarget.campaign_id == campaign.id,
                    CampaignTarget.status.in_(OPEN_TARGET_STATUSES),
                )
                .with_for_update()
            ).all()
            for target in open_targets:
                target_previous_status = target.status
                target.status = CampaignStatus.CANCELLED.value
                target.processed_at = now
                _add_event(
                    db,
                    campaign=campaign,
                    target=target,
                    event_type=CampaignEventType.STATUS_CHANGED.value,
                    from_status=target_previous_status,
                    to_status=target.status,
                    actor=actor,
                    note="Cancelled when the campaign was cancelled.",
                )
        _add_event(
            db,
            campaign=campaign,
            event_type=CampaignEventType.STATUS_CHANGED.value,
            from_status=previous_status,
            to_status=campaign.status,
            actor=actor,
        )
    db.commit()
    db.refresh(campaign)
    campaign.created_by = db.get(User, campaign.created_by_user_id)
    return campaign


def validate_assignee(assignee: User | None) -> None:
    """활성 운영 담당자만 캠페인 대상에 지정할 수 있습니다."""
    if assignee is None:
        return
    if not assignee.is_active:
        raise CampaignAssigneeError("The assigned user must be active.")
    if assignee.role not in MUTABLE_ASSIGNEE_ROLES:
        raise CampaignAssigneeError(
            "Only active operations users can be assigned."
        )


def _add_event(
    db: Session,
    *,
    campaign: Campaign,
    event_type: str,
    actor: User | None,
    target: CampaignTarget | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> CampaignEvent:
    event = CampaignEvent(
        campaign_id=campaign.id,
        campaign_target_id=target.id if target is not None else None,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor_user_id=actor.id if actor is not None else None,
        note=note,
        metadata_json=metadata_json,
    )
    db.add(event)
    return event


def _lock_customer(db: Session, customer_id: int) -> None:
    """동일 고객 동시 타기팅 시 중복 검사를 직렬화합니다."""
    db.execute(
        select(Customer.customer_id)
        .where(Customer.customer_id == customer_id)
        .with_for_update()
    ).first()


def _campaign_priority(campaign: Campaign) -> int:
    return SEGMENT_PRIORITIES.get(
        campaign.segment_code or "",
        UNCLASSIFIED_CAMPAIGN_PRIORITY,
    )


def _enforce_contact_eligibility(
    db: Session,
    *,
    campaign: Campaign,
    customer_id: int,
    actor: User,
    excluded_target_id: int | None = None,
) -> None:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise CampaignDomainError("The target customer was not found.")
    if customer.marketing_opt_out:
        raise CampaignConflictError("The customer has opted out of marketing contact.")

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=DEFAULT_CONTACT_COOLDOWN_DAYS
    )
    if customer.last_contacted_at is not None and _utc(customer.last_contacted_at) >= cutoff:
        raise CampaignConflictError(
            "The customer was contacted within the contact cooldown period."
        )
    recent_target_id = db.scalar(
        select(CampaignTarget.id)
        .where(
            CampaignTarget.customer_id == customer_id,
            CampaignTarget.status.in_(
                [CampaignStatus.CONTACTED.value, CampaignStatus.COMPLETED.value]
            ),
            CampaignTarget.processed_at >= cutoff,
        )
        .limit(1)
    )
    if recent_target_id is not None:
        raise CampaignConflictError(
            "The customer was contacted within the contact cooldown period."
        )

    existing_campaign_target_id = db.scalar(
        select(CampaignTarget.id)
        .where(
            CampaignTarget.customer_id == customer_id,
            CampaignTarget.campaign_id == campaign.id,
        )
        .limit(1)
    )
    if existing_campaign_target_id is not None:
        raise CampaignConflictError(
            "The customer is already registered in this campaign."
        )

    query = (
        select(CampaignTarget)
        .join(Campaign, Campaign.id == CampaignTarget.campaign_id)
        .options(selectinload(CampaignTarget.campaign))
        .where(
            CampaignTarget.customer_id == customer_id,
            CampaignTarget.status.in_(OPEN_TARGET_STATUSES),
            Campaign.status.in_(OPEN_CAMPAIGN_STATUSES),
        )
        .with_for_update()
    )
    if excluded_target_id is not None:
        query = query.where(CampaignTarget.id != excluded_target_id)
    existing_targets = db.scalars(query).all()
    preemptable_targets: list[tuple[CampaignTarget, Campaign]] = []
    for existing_target in existing_targets:
        existing_campaign = existing_target.campaign
        if existing_campaign is None:
            raise CampaignConflictError(
                "The customer already has an active campaign target."
            )
        if existing_campaign.id == campaign.id:
            raise CampaignConflictError(
                "The customer is already registered in this campaign."
            )
        can_preempt = (
            _campaign_priority(campaign) > _campaign_priority(existing_campaign)
            and existing_target.status
            in {CampaignStatus.PENDING.value, CampaignStatus.ASSIGNED.value}
        )
        if not can_preempt:
            raise CampaignConflictError(
                "The customer already has an equal-or-higher priority active campaign."
            )
        preemptable_targets.append((existing_target, existing_campaign))

    for existing_target, existing_campaign in preemptable_targets:
        previous_status = existing_target.status
        existing_target.status = CampaignStatus.CANCELLED.value
        existing_target.processed_at = datetime.now(timezone.utc)
        _add_event(
            db,
            campaign=existing_campaign,
            target=existing_target,
            event_type=CampaignEventType.STATUS_CHANGED.value,
            from_status=previous_status,
            to_status=existing_target.status,
            actor=actor,
            note=f"Preempted by higher-priority campaign {campaign.id}.",
            metadata_json={"replacement_campaign_id": campaign.id},
        )


def create_campaign_target(
    db: Session,
    *,
    campaign: Campaign,
    insight: CustomerInsight,
    assignee: User | None,
    actor: User,
    commit: bool = True,
) -> CampaignTarget:
    """캠페인 대상과 생성·배정 이벤트를 저장합니다."""
    if campaign.status not in {
        CampaignLifecycleStatus.DRAFT.value,
        CampaignLifecycleStatus.SCHEDULED.value,
        CampaignLifecycleStatus.ACTIVE.value,
    }:
        raise CampaignConflictError(
            "Targets can only be added to draft, scheduled, or active campaigns."
        )
    validate_assignee(assignee)
    _lock_customer(db, insight.customer_id)
    _enforce_contact_eligibility(
        db,
        campaign=campaign,
        customer_id=insight.customer_id,
        actor=actor,
    )
    experiment_group = assign_experiment_group(campaign, insight.customer_id)
    effective_assignee = (
        assignee
        if experiment_group == ExperimentGroup.TREATMENT.value
        else None
    )

    target = CampaignTarget(
        campaign_id=campaign.id,
        customer_id=insight.customer_id,
        customer_insight_id=insight.id,
        campaign_name=campaign.name,
        assigned_to_user_id=(
            effective_assignee.id if effective_assignee is not None else None
        ),
        experiment_group=experiment_group,
        status=(
            CampaignStatus.ASSIGNED.value
            if effective_assignee is not None
            else CampaignStatus.PENDING.value
        ),
        converted=False,
    )
    db.add(target)
    db.flush()
    _add_event(
        db,
        campaign=campaign,
        target=target,
        event_type=CampaignEventType.CREATED.value,
        to_status=target.status,
        actor=actor,
    )
    if effective_assignee is not None:
        _add_event(
            db,
            campaign=campaign,
            target=target,
            event_type=CampaignEventType.ASSIGNED.value,
            to_status=target.status,
            actor=actor,
            metadata_json={"assigned_to_user_id": effective_assignee.id},
        )
    if commit:
        db.commit()
        db.refresh(target)
    else:
        db.flush()
    target.campaign = campaign
    target.assignee = effective_assignee
    return target


def get_or_create_legacy_campaign(
    db: Session,
    *,
    name: str,
    actor: User,
) -> Campaign:
    """기존 campaign_name 요청을 실제 캠페인으로 승격합니다."""
    campaign = db.scalar(select(Campaign).where(Campaign.name == name))
    if campaign is not None:
        return campaign
    campaign = Campaign(
        name=name,
        status=CampaignLifecycleStatus.DRAFT.value,
        created_by_user_id=actor.id,
    )
    db.add(campaign)
    db.flush()
    _add_event(
        db,
        campaign=campaign,
        event_type=CampaignEventType.CREATED.value,
        to_status=campaign.status,
        actor=actor,
        note="Created from legacy campaign_name target request.",
    )
    return campaign


def update_campaign_target(
    db: Session,
    *,
    target: CampaignTarget,
    status: CampaignStatus | None,
    assignee: User | None,
    result: str | None,
    result_notes: str | None,
    result_code: CampaignResultCode | None,
    converted: bool | None,
    actor: User,
    retained: bool | None = None,
    outcome_revenue: float | None = None,
    assignee_provided: bool = False,
    retained_provided: bool | None = None,
    outcome_revenue_provided: bool | None = None,
) -> CampaignTarget:
    """대상 상태·담당자·결과를 규칙에 맞게 갱신하고 이벤트를 남깁니다."""
    campaign = target.campaign or db.get(Campaign, target.campaign_id)
    if campaign is None:
        raise CampaignDomainError("The campaign target is not linked to a campaign.")
    if retained_provided is None:
        retained_provided = retained is not None
    if outcome_revenue_provided is None:
        outcome_revenue_provided = outcome_revenue is not None
    has_any_update = any(
        value is not None
        for value in (
            status,
            result,
            result_notes,
            result_code,
            converted,
            retained,
            outcome_revenue,
        )
    ) or assignee_provided or bool(retained_provided) or bool(outcome_revenue_provided)
    if (
        campaign.status == CampaignLifecycleStatus.CANCELLED.value
        or target.status == CampaignStatus.CANCELLED.value
    ) and has_any_update:
        raise CampaignConflictError("Cancelled campaign targets cannot be changed.")
    if (
        campaign.status == CampaignLifecycleStatus.COMPLETED.value
        and (status is not None or assignee_provided)
    ):
        raise CampaignConflictError(
            "A completed campaign does not allow target workflow changes."
        )

    validate_assignee(assignee)
    previous_status = target.status
    next_status = status.value if status is not None else target.status
    if assignee_provided and assignee is not None and status is None and target.status == CampaignStatus.PENDING.value:
        next_status = CampaignStatus.ASSIGNED.value
    if assignee_provided and assignee is None and status is None and target.status == CampaignStatus.ASSIGNED.value:
        next_status = CampaignStatus.PENDING.value
    next_assignee_id = (
        assignee.id
        if assignee is not None
        else (None if assignee_provided else target.assigned_to_user_id)
    )
    if target.experiment_group == ExperimentGroup.CONTROL.value:
        if (assignee_provided and assignee is not None) or next_status in {
            CampaignStatus.ASSIGNED.value,
            CampaignStatus.CONTACTED.value,
            CampaignStatus.COMPLETED.value,
        }:
            raise CampaignTransitionError(
                "Control-group targets cannot be assigned or contacted."
            )
    if next_status == CampaignStatus.ASSIGNED.value and next_assignee_id is None:
        raise CampaignDomainError(
            "An assigned target must have an operations assignee."
        )

    contact_workflow_update = (
        next_status in {CampaignStatus.CONTACTED.value, CampaignStatus.COMPLETED.value}
        and next_status != previous_status
    ) or result_code == CampaignResultCode.CONTACTED
    if (
        target.experiment_group == ExperimentGroup.TREATMENT.value
        and contact_workflow_update
        and campaign.status != CampaignLifecycleStatus.ACTIVE.value
    ):
        raise CampaignConflictError(
            "Treatment targets can only be contacted or completed in an active campaign."
        )
    if next_status != previous_status:
        allowed = TARGET_STATUS_TRANSITIONS.get(previous_status, set())
        if next_status not in allowed:
            raise CampaignTransitionError(
                f"Target status cannot change from {previous_status} to {next_status}."
            )

    previous_assignee_id = target.assigned_to_user_id
    if assignee_provided:
        if target.status not in {
            CampaignStatus.PENDING.value,
            CampaignStatus.ASSIGNED.value,
        }:
            raise CampaignTransitionError(
                "A target cannot be reassigned after contact has started."
            )
        target.assigned_to_user_id = assignee.id if assignee is not None else None
        target.assignee = assignee
        _add_event(
            db,
            campaign=campaign,
            target=target,
            event_type=CampaignEventType.ASSIGNED.value,
            from_status=previous_status,
            to_status=next_status,
            actor=actor,
            metadata_json={
                "from_assigned_to_user_id": previous_assignee_id,
                "assigned_to_user_id": (
                    assignee.id if assignee is not None else None
                ),
            },
        )

    if next_status != previous_status:
        target.status = next_status
        now = datetime.now(timezone.utc)
        if next_status in {
            CampaignStatus.CONTACTED.value,
            CampaignStatus.COMPLETED.value,
        }:
            target.processed_at = target.processed_at or now
            customer = db.get(Customer, target.customer_id)
            if customer is not None:
                customer.last_contacted_at = now
        if next_status == CampaignStatus.CONTACTED.value:
            target.contacted_at = target.contacted_at or now
        if next_status == CampaignStatus.COMPLETED.value:
            target.completed_at = target.completed_at or now
        elif next_status == CampaignStatus.CANCELLED.value:
            target.processed_at = target.processed_at or now
        _add_event(
            db,
            campaign=campaign,
            target=target,
            event_type=CampaignEventType.STATUS_CHANGED.value,
            from_status=previous_status,
            to_status=next_status,
            actor=actor,
        )

    previous_result_state = {
        "result_code": target.result_code,
        "converted": bool(target.converted),
        "retained": target.retained,
        "outcome_revenue": (
            float(target.outcome_revenue)
            if target.outcome_revenue is not None
            else None
        ),
    }
    result_changed = (
        result is not None
        or result_notes is not None
        or result_code is not None
        or retained_provided
        or outcome_revenue_provided
    )
    if result is not None:
        target.result = result
    if result_notes is not None:
        target.result_notes = result_notes
    if next_status == CampaignStatus.CONTACTED.value and result_code is None:
        result_code = CampaignResultCode.CONTACTED
        result_changed = True
    if result_code is not None:
        if result_code == CampaignResultCode.CONTACTED:
            if target.experiment_group == ExperimentGroup.CONTROL.value:
                raise CampaignDomainError("Control-group targets cannot be contacted.")
            if next_status not in {
                CampaignStatus.CONTACTED.value,
                CampaignStatus.COMPLETED.value,
            }:
                raise CampaignDomainError(
                    "The contacted result code requires a contacted or completed target."
                )
        elif result_code.value in FINAL_RESULT_CODES:
            if (
                target.experiment_group == ExperimentGroup.TREATMENT.value
                and next_status != CampaignStatus.COMPLETED.value
            ):
                raise CampaignDomainError(
                    "A treatment result code requires a completed target."
                )
        target.result_code = result_code.value
        if result_code == CampaignResultCode.CONVERTED:
            converted = True
        elif result_code.value in FINAL_RESULT_CODES:
            converted = False
        if result_code == CampaignResultCode.OPTED_OUT:
            customer = db.get(Customer, target.customer_id)
            if customer is not None:
                customer.marketing_opt_out = True
    if (
        target.experiment_group == ExperimentGroup.TREATMENT.value
        and next_status == CampaignStatus.COMPLETED.value
        and (result_code.value if result_code is not None else target.result_code)
        not in FINAL_RESULT_CODES
    ):
        raise CampaignDomainError(
            "A completed treatment target requires a final structured result code."
        )
    if converted is not None:
        if (
            converted
            and target.experiment_group == ExperimentGroup.TREATMENT.value
            and next_status != CampaignStatus.COMPLETED.value
        ):
            raise CampaignDomainError(
                "A target must be completed before it can be marked converted."
            )
        target.converted = converted
        if converted:
            target.converted_at = target.converted_at or datetime.now(timezone.utc)
            target.result_code = CampaignResultCode.CONVERTED.value
        else:
            target.converted_at = None
            if target.result_code == CampaignResultCode.CONVERTED.value:
                target.result_code = CampaignResultCode.NOT_CONVERTED.value
        result_changed = True
    if retained_provided:
        if retained is None:
            target.retained = None
            target.retention_checked_at = None
        else:
            if (
                target.experiment_group == ExperimentGroup.TREATMENT.value
                and next_status != CampaignStatus.COMPLETED.value
            ):
                raise CampaignDomainError(
                    "A treatment target must be completed before retention is recorded."
                )
            observation_anchor = (
                target.completed_at
                if target.experiment_group == ExperimentGroup.TREATMENT.value
                else (campaign.start_at or target.created_at)
            )
            if observation_anchor is None:
                raise CampaignDomainError(
                    "Retention cannot be recorded before the observation period starts."
                )
            retention_available_at = _utc(observation_anchor) + timedelta(
                days=campaign.retention_window_days
            )
            if datetime.now(timezone.utc) < retention_available_at:
                raise CampaignDomainError(
                    "Retention cannot be recorded before retention_window_days has elapsed."
                )
            target.retained = retained
            target.retention_checked_at = datetime.now(timezone.utc)
        result_changed = True
    if outcome_revenue_provided:
        if outcome_revenue is None:
            target.outcome_revenue = None
        else:
            if outcome_revenue < 0:
                raise CampaignDomainError("Outcome revenue cannot be negative.")
            if not target.converted:
                raise CampaignDomainError(
                    "Outcome revenue can only be recorded for a converted target."
                )
            target.outcome_revenue = outcome_revenue
        result_changed = True
    if result_changed:
        _add_event(
            db,
            campaign=campaign,
            target=target,
            event_type=(
                CampaignEventType.CONVERSION_UPDATED.value
                if converted is not None or result_code == CampaignResultCode.CONVERTED
                else CampaignEventType.RESULT_UPDATED.value
            ),
            actor=actor,
            metadata_json={
                "before": previous_result_state,
                "result_code": target.result_code,
                "converted": bool(target.converted),
                "retained": target.retained,
                "outcome_revenue": (
                    float(target.outcome_revenue)
                    if target.outcome_revenue is not None
                    else None
                ),
                "experiment_group": target.experiment_group,
            },
        )

    db.commit()
    db.refresh(target)
    target.campaign = campaign
    target.assignee = (
        db.get(User, target.assigned_to_user_id)
        if target.assigned_to_user_id is not None
        else None
    )
    return target


def fetch_campaign_targets(
    db: Session,
    *,
    campaign_id: int | None,
    campaign_name: str | None,
    status: CampaignStatus | None,
    assigned_to_user_id: int | None,
    customer_id: int | None,
    converted: bool | None,
    page: int,
    page_size: int,
    sort_by_priority: bool = False,
) -> CampaignTargetPage:
    """캠페인 대상의 서버 필터·페이지네이션·집계를 반환합니다."""
    conditions = _target_conditions(
        campaign_id=campaign_id,
        campaign_name=campaign_name,
        status=status,
        assigned_to_user_id=assigned_to_user_id,
        customer_id=customer_id,
        converted=converted,
    )
    query: Select[tuple[CampaignTarget]] = select(CampaignTarget).where(*conditions)
    total = int(
        db.scalar(
            select(func.count()).select_from(query.order_by(None).subquery())
        )
        or 0
    )
    items = db.scalars(
        query.options(
            selectinload(CampaignTarget.assignee),
            selectinload(CampaignTarget.campaign),
        )
        .order_by(CampaignTarget.created_at.desc(), CampaignTarget.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    stats = _to_stats(db.execute(_campaign_stats_query(conditions)).one())
    return CampaignTargetPage(items=items, total=total, stats=stats)


def fetch_campaign_events(
    db: Session,
    *,
    campaign_id: int,
    campaign_target_id: int | None,
    page: int,
    page_size: int,
) -> tuple[list[CampaignEvent], int]:
    """캠페인 또는 특정 대상의 이벤트 이력을 조회합니다."""
    query: Select[tuple[CampaignEvent]] = select(CampaignEvent).where(
        CampaignEvent.campaign_id == campaign_id
    )
    if campaign_target_id is not None:
        query = query.where(CampaignEvent.campaign_target_id == campaign_target_id)
    total = int(
        db.scalar(
            select(func.count()).select_from(query.order_by(None).subquery())
        )
        or 0
    )
    events = db.scalars(
        query.options(selectinload(CampaignEvent.actor))
        .order_by(CampaignEvent.created_at.desc(), CampaignEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return events, total
