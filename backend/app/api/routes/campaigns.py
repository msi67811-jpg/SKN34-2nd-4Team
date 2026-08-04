"""캠페인 기본 정보·대상·이력과 업무 규칙을 제공하는 API입니다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import get_current_user, require_roles
from ...database import get_db
from ...enums import CampaignLifecycleStatus, CampaignStatus, ExperimentGroup, UserRole
from ...models import Campaign, CampaignTarget, CustomerInsight, User
from ...schemas import (
    CampaignCreateRequest,
    CampaignEventListResponse,
    CampaignEventResponse,
    CampaignListResponse,
    CampaignResponse,
    CampaignStatsResponse,
    CampaignTargetCreateRequest,
    CampaignTargetListResponse,
    CampaignTargetResponse,
    CampaignTargetUpdateRequest,
    CampaignUpdateRequest,
)
from ...services.campaign_service import (
    CampaignAssigneeError,
    CampaignConflictError,
    CampaignDomainError,
    CampaignStats,
    CampaignTransitionError,
    create_campaign,
    create_campaign_target,
    fetch_campaign,
    fetch_campaign_events,
    fetch_campaign_targets,
    fetch_campaigns,
    get_or_create_legacy_campaign,
    update_campaign,
    update_campaign_target,
)


campaigns_router = APIRouter(
    prefix="/api/v1",
    tags=["campaigns"],
)
# 캠페인 기획·생성은 관리자와 마케팅팀이 담당하고, 고객 접촉 결과 처리는
# 관리자와 운영팀이 담당합니다. 조회 API는 모든 인증 사용자에게 열려 있습니다.
CAMPAIGN_MANAGE_ROLES = (UserRole.ADMIN, UserRole.MARKETING)
CAMPAIGN_TARGET_CREATE_ROLES = (UserRole.ADMIN, UserRole.MARKETING)
CAMPAIGN_TARGET_UPDATE_ROLES = (UserRole.ADMIN, UserRole.OPERATIONS)


def _domain_error(exc: CampaignDomainError) -> HTTPException:
    if isinstance(exc, CampaignConflictError):
        return HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    if isinstance(exc, CampaignTransitionError):
        return HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    if isinstance(exc, CampaignAssigneeError):
        return HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    return HTTPException(
        status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    )


def _to_stats_response(stats) -> CampaignStatsResponse:
    return CampaignStatsResponse(
        total_targets=stats.total_targets,
        unprocessed_targets=stats.unprocessed_targets,
        contacted_targets=stats.contacted_targets,
        converted_targets=stats.converted_targets,
        status_counts=stats.status_counts,
    )


EMPTY_STATS = CampaignStats(
    total_targets=0,
    unprocessed_targets=0,
    contacted_targets=0,
    converted_targets=0,
    status_counts={},
)


def _to_campaign_response(campaign: Campaign, stats) -> CampaignResponse:
    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        description=campaign.description,
        channel=campaign.channel,
        segment_code=campaign.segment_code,
        status=campaign.status,
        start_at=campaign.start_at,
        end_at=campaign.end_at,
        experiment_enabled=bool(campaign.experiment_enabled),
        control_group_ratio=campaign.control_group_ratio,
        experiment_policy_locked=stats.total_targets > 0,
        experiment_assignment_version=campaign.experiment_assignment_version,
        fixed_cost=campaign.fixed_cost,
        cost_per_contact=campaign.cost_per_contact,
        revenue_per_conversion=campaign.revenue_per_conversion,
        retention_window_days=campaign.retention_window_days,
        created_by_user_id=campaign.created_by_user_id,
        created_by_display_name=(
            campaign.created_by.display_name
            if campaign.created_by is not None
            else None
        ),
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        stats=_to_stats_response(stats),
    )


def _to_campaign_target_response(target: CampaignTarget) -> CampaignTargetResponse:
    return CampaignTargetResponse(
        id=target.id,
        customer_id=target.customer_id,
        customer_insight_id=target.customer_insight_id,
        campaign_id=target.campaign_id,
        campaign_name=target.campaign_name,
        experiment_group=target.experiment_group,
        bulk_targeting_run_id=target.bulk_targeting_run_id,
        campaign_status=(
            target.campaign.status
            if target.campaign is not None
            else None
        ),
        assigned_to_user_id=target.assigned_to_user_id,
        assigned_to_display_name=(
            target.assignee.display_name if target.assignee is not None else None
        ),
        status=target.status,
        processed_at=target.processed_at,
        contacted_at=target.contacted_at,
        completed_at=target.completed_at,
        converted_at=target.converted_at,
        result=target.result,
        result_notes=target.result_notes,
        result_code=target.result_code,
        converted=bool(target.converted),
        retained=target.retained,
        retention_checked_at=target.retention_checked_at,
        outcome_revenue=target.outcome_revenue,
        created_at=target.created_at,
        updated_at=target.updated_at,
    )


def _to_event_response(event) -> CampaignEventResponse:
    return CampaignEventResponse(
        id=event.id,
        campaign_id=event.campaign_id,
        campaign_target_id=event.campaign_target_id,
        event_type=event.event_type,
        from_status=event.from_status,
        to_status=event.to_status,
        actor_user_id=event.actor_user_id,
        actor_display_name=event.actor.display_name if event.actor is not None else None,
        note=event.note,
        metadata_json=event.metadata_json,
        created_at=event.created_at,
    )


@campaigns_router.get(
    "/campaigns",
    response_model=CampaignListResponse,
    summary="캠페인 목록과 서버 집계 조회",
)
def list_campaigns(
    campaign_status: CampaignLifecycleStatus | None = Query(
        default=None,
        alias="status",
        description="캠페인 생명주기 상태",
    ),
    name: str | None = Query(default=None, min_length=1, max_length=150),
    created_by_user_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignListResponse:
    """캠페인별 전체·미처리·접촉·전환 수를 서버에서 집계합니다."""
    items, total, stats_by_campaign = fetch_campaigns(
        db,
        status=campaign_status,
        name=name,
        created_by_user_id=created_by_user_id,
        page=page,
        page_size=page_size,
    )
    return CampaignListResponse(
        items=[
            _to_campaign_response(
                item,
                stats_by_campaign.get(
                    item.id,
                    EMPTY_STATS,
                ),
            )
            for item in items
        ],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=(total + page_size - 1) // page_size if total else 0,
    )


@campaigns_router.post(
    "/campaigns",
    response_model=CampaignResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="캠페인 생성",
)
def create_campaign_api(
    payload: CampaignCreateRequest,
    current_user: User = Depends(require_roles(*CAMPAIGN_MANAGE_ROLES)),
    db: Session = Depends(get_db),
) -> CampaignResponse:
    """캠페인 기본 정보와 실행 기간을 생성합니다."""
    try:
        campaign = create_campaign(
            db,
            name=payload.name,
            description=payload.description,
            channel=payload.channel,
            lifecycle_status=payload.status,
            start_at=payload.start_at,
            end_at=payload.end_at,
            actor=current_user,
            segment_code=payload.segment_code.value if payload.segment_code else None,
            experiment_enabled=payload.experiment_enabled,
            control_group_ratio=(
                payload.control_group_ratio if payload.experiment_enabled else 0.0
            ),
            experiment_seed=None,
            fixed_cost=payload.fixed_cost,
            cost_per_contact=payload.cost_per_contact,
            revenue_per_conversion=payload.revenue_per_conversion,
            retention_window_days=payload.retention_window_days,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="A campaign with the same name already exists.",
        ) from None
    except CampaignDomainError as exc:
        raise _domain_error(exc) from None
    return _to_campaign_response(
        campaign,
        EMPTY_STATS,
    )


@campaigns_router.get(
    "/campaigns/{campaign_id}",
    response_model=CampaignResponse,
    summary="캠페인 상세 조회",
)
def get_campaign_api(
    campaign_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignResponse:
    campaign = fetch_campaign(db, campaign_id)
    if campaign is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="The campaign was not found.",
        )
    target_page = fetch_campaign_targets(
        db,
        campaign_id=campaign_id,
        campaign_name=None,
        status=None,
        assigned_to_user_id=None,
        customer_id=None,
        converted=None,
        page=1,
        page_size=1,
    )
    return _to_campaign_response(campaign, target_page.stats)


@campaigns_router.patch(
    "/campaigns/{campaign_id}",
    response_model=CampaignResponse,
    summary="캠페인 정보·상태 변경",
)
def update_campaign_api(
    campaign_id: int,
    payload: CampaignUpdateRequest,
    current_user: User = Depends(require_roles(*CAMPAIGN_MANAGE_ROLES)),
    db: Session = Depends(get_db),
) -> CampaignResponse:
    campaign = db.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
    )
    if campaign is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="The campaign was not found.",
        )
    provided = payload.model_dump(exclude_unset=True)
    try:
        campaign = update_campaign(
            db,
            campaign=campaign,
            name=payload.name,
            description=payload.description,
            channel=payload.channel,
            lifecycle_status=payload.status,
            start_at=payload.start_at,
            end_at=payload.end_at,
            actor=current_user,
            provided_fields=set(provided),
            segment_code=(
                payload.segment_code.value
                if payload.segment_code is not None
                else None
            ),
            experiment_enabled=payload.experiment_enabled,
            control_group_ratio=payload.control_group_ratio,
            experiment_seed=None,
            fixed_cost=payload.fixed_cost,
            cost_per_contact=payload.cost_per_contact,
            revenue_per_conversion=payload.revenue_per_conversion,
            retention_window_days=payload.retention_window_days,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="A campaign with the same name already exists.",
        ) from None
    except CampaignDomainError as exc:
        db.rollback()
        raise _domain_error(exc) from None
    target_page = fetch_campaign_targets(
        db,
        campaign_id=campaign_id,
        campaign_name=None,
        status=None,
        assigned_to_user_id=None,
        customer_id=None,
        converted=None,
        page=1,
        page_size=1,
    )
    return _to_campaign_response(campaign, target_page.stats)


@campaigns_router.get(
    "/campaigns/{campaign_id}/targets",
    response_model=CampaignTargetListResponse,
    summary="캠페인별 대상 목록 조회",
)
def list_campaign_targets_by_campaign(
    campaign_id: int,
    target_status: CampaignStatus | None = Query(default=None, alias="status"),
    assigned_to_user_id: int | None = Query(default=None, ge=1),
    customer_id: int | None = Query(default=None, ge=1),
    converted: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignTargetListResponse:
    if db.get(Campaign, campaign_id) is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="The campaign was not found.",
        )
    result = fetch_campaign_targets(
        db,
        campaign_id=campaign_id,
        campaign_name=None,
        status=target_status,
        assigned_to_user_id=assigned_to_user_id,
        customer_id=customer_id,
        converted=converted,
        page=page,
        page_size=page_size,
    )
    return CampaignTargetListResponse(
        items=[_to_campaign_target_response(item) for item in result.items],
        page=page,
        page_size=page_size,
        total=result.total,
        total_pages=(result.total + page_size - 1) // page_size if result.total else 0,
        stats=_to_stats_response(result.stats),
    )


@campaigns_router.get(
    "/campaigns/{campaign_id}/events",
    response_model=CampaignEventListResponse,
    summary="캠페인 이벤트 이력 조회",
)
def list_campaign_events(
    campaign_id: int,
    campaign_target_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignEventListResponse:
    if db.get(Campaign, campaign_id) is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="The campaign was not found.",
        )
    events, total = fetch_campaign_events(
        db,
        campaign_id=campaign_id,
        campaign_target_id=campaign_target_id,
        page=page,
        page_size=page_size,
    )
    return CampaignEventListResponse(
        items=[_to_event_response(event) for event in events],
        page=page,
        page_size=page_size,
        total=total,
    )


@campaigns_router.get(
    "/campaign-targets",
    response_model=CampaignTargetListResponse,
    summary="캠페인 대상 목록 조회",
)
def list_campaign_targets(
    target_status: CampaignStatus | None = Query(
        default=None,
        alias="status",
        description="캠페인 처리 상태 필터",
    ),
    campaign_id: int | None = Query(default=None, ge=1),
    campaign_name: str | None = Query(default=None, min_length=1, max_length=150),
    assigned_to_user_id: int | None = Query(default=None, ge=1),
    customer_id: int | None = Query(default=None, ge=1),
    converted: bool | None = Query(default=None),
    sort_by_priority: bool = Query(
        default=False, description="이탈위험 우선순위(이탈확률↓, 활동성갭↑)로 정렬"
    ),  # ← 추가
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignTargetListResponse:
    """기존 대상 API도 캠페인·담당자·전환 필터와 서버 집계를 지원합니다."""
    result = fetch_campaign_targets(
        db,
        campaign_id=campaign_id,
        campaign_name=campaign_name,
        status=target_status,
        assigned_to_user_id=assigned_to_user_id,
        customer_id=customer_id,
        converted=converted,
        page=page,
        page_size=page_size,
    )
    return CampaignTargetListResponse(
        items=[_to_campaign_target_response(item) for item in result.items],
        page=page,
        page_size=page_size,
        total=result.total,
        total_pages=(result.total + page_size - 1) // page_size if result.total else 0,
        stats=_to_stats_response(result.stats),
    )


def _resolve_campaign(
    db: Session,
    *,
    payload: CampaignTargetCreateRequest,
    actor: User,
) -> Campaign:
    if payload.campaign_id is not None:
        campaign = db.scalar(
            select(Campaign)
            .where(Campaign.id == payload.campaign_id)
            .with_for_update()
        )
        if campaign is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="The campaign was not found.",
            )
        return campaign

    assert payload.campaign_name is not None
    return get_or_create_legacy_campaign(
        db,
        name=payload.campaign_name,
        actor=actor,
    )


@campaigns_router.post(
    "/campaign-targets",
    response_model=CampaignTargetResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="캠페인 대상 등록",
)
def create_campaign_target_api(
    payload: CampaignTargetCreateRequest,
    current_user: User = Depends(require_roles(*CAMPAIGN_TARGET_CREATE_ROLES)),
    db: Session = Depends(get_db),
) -> CampaignTargetResponse:
    """분석 결과를 캠페인 대상에 등록하고 중복 접촉을 차단합니다."""
    insight = db.get(CustomerInsight, payload.customer_insight_id)
    if insight is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="The customer insight was not found.",
        )
    assignee = None
    if payload.assigned_to_user_id is not None:
        assignee = db.get(User, payload.assigned_to_user_id)
        if assignee is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="The assigned user was not found.",
            )
    try:
        campaign = _resolve_campaign(db, payload=payload, actor=current_user)
        target = create_campaign_target(
            db,
            campaign=campaign,
            insight=insight,
            assignee=assignee,
            actor=current_user,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="The campaign target already exists or conflicts with an active target.",
        ) from None
    except CampaignDomainError as exc:
        db.rollback()
        raise _domain_error(exc) from None
    return _to_campaign_target_response(target)


@campaigns_router.patch(
    "/campaign-targets/{target_id}",
    response_model=CampaignTargetResponse,
    summary="캠페인 대상 처리 상태·결과 변경",
)
def update_campaign_target_api(
    target_id: int,
    payload: CampaignTargetUpdateRequest,
    current_user: User = Depends(require_roles(*CAMPAIGN_TARGET_UPDATE_ROLES)),
    db: Session = Depends(get_db),
) -> CampaignTargetResponse:
    """대상 상태 전이·담당자 역할·결과 코드를 서버에서 검증합니다."""
    target = db.scalar(
        select(CampaignTarget)
        .where(CampaignTarget.id == target_id)
        .with_for_update()
    )
    if target is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="The campaign target was not found.",
        )
    assignee_provided = "assigned_to_user_id" in payload.model_fields_set
    assignee = None
    if assignee_provided and payload.assigned_to_user_id is not None:
        assignee = db.get(User, payload.assigned_to_user_id)
        if assignee is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="The assigned user was not found.",
            )
    if current_user.role == UserRole.OPERATIONS.value:
        if target.experiment_group == ExperimentGroup.CONTROL.value:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Operations users cannot edit control-group outcomes.",
            )
        if target.assigned_to_user_id not in {None, current_user.id}:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Operations users can only process their own assigned targets.",
            )
        if assignee_provided and assignee is not None and assignee.id != current_user.id:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Operations users can only assign a target to themselves.",
            )
    try:
        target = update_campaign_target(
            db,
            target=target,
            status=payload.status,
            assignee=assignee,
            result=payload.result,
            result_notes=payload.result_notes,
            result_code=payload.result_code,
            converted=payload.converted,
            actor=current_user,
            retained=payload.retained,
            outcome_revenue=payload.outcome_revenue,
            assignee_provided=assignee_provided,
            retained_provided="retained" in payload.model_fields_set,
            outcome_revenue_provided="outcome_revenue" in payload.model_fields_set,
        )
    except CampaignDomainError as exc:
        db.rollback()
        raise _domain_error(exc) from None
    return _to_campaign_target_response(target)
