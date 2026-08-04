"""EDA 원천 데이터셋 기반 분석팀용 통계 API입니다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .auth import get_current_user
from ...models import User
from ...schemas import (
    CategoricalChurnRateResponse,
    FeatureCorrelationResponse,
    NumericDistributionResponse,
)
from ...services import analytics_service


analytics_router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@analytics_router.get(
    "/categorical-churn-rate",
    response_model=CategoricalChurnRateResponse,
    summary="범주형 변수별 이탈률 조회",
)
def get_categorical_churn_rate(
    field: str = Query(
        ...,
        description="Gender, Card_Category, Income_Category, Education_Level, Marital_Status 중 하나",
    ),
    _current_user: User = Depends(get_current_user),
) -> CategoricalChurnRateResponse:
    """인증된 모든 역할이 범주형 변수별 이탈률 분포를 조회할 수 있습니다."""
    if field not in analytics_service.CATEGORICAL_FIELDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported categorical field: {field}",
        )
    return CategoricalChurnRateResponse(
        field=field,
        items=analytics_service.categorical_churn_rate(field),
    )


@analytics_router.get(
    "/numeric-distribution",
    response_model=NumericDistributionResponse,
    summary="이탈 여부별 수치형 변수 분포 조회",
)
def get_numeric_distribution(
    field: str = Query(
        ...,
        description=(
            "Total_Trans_Ct, Total_Trans_Amt, Avg_Utilization_Ratio, "
            "Months_Inactive_12_mon, Contacts_Count_12_mon, "
            "Total_Relationship_Count 중 하나"
        ),
    ),
    _current_user: User = Depends(get_current_user),
) -> NumericDistributionResponse:
    """인증된 모든 역할이 이탈 여부별 수치형 변수 분포(박스플롯 입력)를 조회할 수 있습니다."""
    if field not in analytics_service.NUMERIC_DISTRIBUTION_FIELDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported numeric field: {field}",
        )
    return NumericDistributionResponse(
        field=field,
        by_target=analytics_service.numeric_distribution(field),
    )


@analytics_router.get(
    "/feature-correlation",
    response_model=FeatureCorrelationResponse,
    summary="변수-이탈 상관관계 조회",
)
def get_feature_correlation(
    _current_user: User = Depends(get_current_user),
) -> FeatureCorrelationResponse:
    """인증된 모든 역할이 수치형 변수와 이탈 간 상관계수를 조회할 수 있습니다."""
    return FeatureCorrelationResponse(items=analytics_service.feature_correlation())
