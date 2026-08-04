"""고객 분석 결과를 조회하는 인증 API입니다."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .auth import get_current_user
from ...database import get_db
from ...enums import RiskLevel
from ...models import Campaign, CustomerInsight, User
from ...schemas import (
    CustomerFeatureSnapshotResponse,
    CustomerInsightDetailResponse,
    CustomerInsightHistoryResponse,
    CustomerInsightListResponse,
    CustomerInsightResponse,
    CustomerInsightStats,
)
from ...services.insight_service import (
    InsightFilters,
    fetch_insight_page,
    fetch_customer_insight_history,
    fetch_latest_customer_insight,
)


insights_router = APIRouter(
    prefix="/api/v1/customer-insights",
    tags=["customer-insights"],
)
SortField = Literal[
    "churn_probability",
    "actual_transaction_count",
    "activity_gap",
    "expected_transaction_count",
    "total_trans_amt",
    "card_category",
    "contacts_count_12_mon",
    "scored_at",
]
SortOrder = Literal["asc", "desc"]


def _to_insight_response(insight: CustomerInsight) -> CustomerInsightResponse:
    """분석 결과 모델을 외부 응답 schema로 변환합니다."""
    return CustomerInsightResponse.model_validate(insight)


@insights_router.get(
    "",
    response_model=CustomerInsightListResponse,
    summary="최신 고객 분석 결과 목록 조회",
)
def list_customer_insights(
    risk_level: RiskLevel | None = Query(
        default=None,
        description="이탈 위험 등급 필터",
    ),
    cluster_name: str | None = Query(
        default=None,
        min_length=1,
        max_length=100,
        description="활동성 군집명 필터",
    ),
    customer_id: int | None = Query(
        default=None,
        ge=1,
        description="고객 ID 정확히 검색",
    ),
    campaign_candidates_only: bool = Query(
        default=False,
        description="캠페인 등록 가능한 고객만 조회",
    ),
    campaign_id: int | None = Query(
        default=None,
        ge=1,
        description="후보 등록 대상 캠페인 ID",
    ),
    sort_by: SortField = Query(
        default="churn_probability",
        description="정렬 기준",
    ),
    sort_order: SortOrder = Query(
        default="desc",
        description="정렬 방향",
    ),
    page: int = Query(default=1, ge=1, description="페이지 번호"),
    page_size: int = Query(
        default=50,
        ge=1,
        le=100,
        description="페이지당 결과 수",
    ),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CustomerInsightListResponse:
    """로그인 사용자가 고객별 최신 분석 결과를 필터·페이지 단위로 조회합니다."""
    if campaign_candidates_only and campaign_id is not None:
        if db.get(Campaign, campaign_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The campaign was not found.",
            )
    result = fetch_insight_page(
        db,
        filters=InsightFilters(
            risk_level=risk_level,
            cluster_name=cluster_name,
            customer_id=customer_id,
            campaign_candidates_only=campaign_candidates_only,
            campaign_id=campaign_id,
        ),
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    return CustomerInsightListResponse(
        items=[_to_insight_response(insight) for insight in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
        total_pages=result.total_pages,
        stats=CustomerInsightStats(
            total=result.total,
            average_churn_probability=result.average_churn_probability,
            risk_counts=result.risk_counts,
            cluster_counts=result.cluster_counts,
            cluster_options=result.cluster_options,
        ),
    )


@insights_router.get(
    "/history/{customer_id}",
    response_model=CustomerInsightHistoryResponse,
    summary="고객 분석 이력 조회",
)
def get_customer_insight_history(
    customer_id: int,
    limit: int = Query(default=24, ge=1, le=100),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CustomerInsightHistoryResponse:
    """고객의 분석 시점별 이탈 확률과 활동성 변화를 반환합니다."""
    items = fetch_customer_insight_history(db, customer_id, limit=limit)
    if not items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The customer insight history was not found.",
        )
    return CustomerInsightHistoryResponse(
        customer_id=customer_id,
        items=[_to_insight_response(insight) for insight in items],
    )


@insights_router.get(
    "/{customer_id}",
    response_model=CustomerInsightDetailResponse,
    summary="고객별 최신 분석 결과 상세 조회",
)
def get_customer_insight(
    customer_id: int,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CustomerInsightDetailResponse:
    """로그인 사용자가 고객 특성과 최신 분석 결과를 함께 조회합니다."""
    insight = fetch_latest_customer_insight(db, customer_id)
    if insight is None or insight.customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The customer insight was not found.",
        )

    return CustomerInsightDetailResponse(
        **_to_insight_response(insight).model_dump(),
        customer=insight.customer,
        customer_snapshot=(
            CustomerFeatureSnapshotResponse.model_validate(insight.customer_snapshot)
            if insight.customer_snapshot is not None
            else None
        ),
    )
