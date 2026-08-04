"""CardOps API 라우터를 한 곳에서 조합합니다."""

from fastapi import APIRouter

from .routes import (
    analytics,
    auth,
    bulk_targeting,
    campaigns,
    customers,
    insights,
    model_runs,
    performance,
    predictions,
    system,
)


api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(predictions.router)
api_router.include_router(auth.auth_router)
api_router.include_router(insights.insights_router)
api_router.include_router(model_runs.model_runs_router)
api_router.include_router(campaigns.campaigns_router)
api_router.include_router(bulk_targeting.bulk_targeting_router)
api_router.include_router(customers.customers_router)
api_router.include_router(performance.performance_router)
api_router.include_router(analytics.analytics_router)
