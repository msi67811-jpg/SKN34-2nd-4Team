"""데이터베이스와 API에서 공통으로 사용하는 제한된 상태값입니다."""

from enum import Enum


class UserRole(str, Enum):
    """CardOps 팀 사용자의 업무 역할입니다."""

    ADMIN = "admin"
    ANALYST = "analyst"
    OPERATIONS = "operations"
    MARKETING = "marketing"


class ModelRunStatus(str, Enum):
    """모델 배치 실행의 처리 상태입니다."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RiskLevel(str, Enum):
    """고객 이탈 위험 등급입니다."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CampaignStatus(str, Enum):
    """캠페인 대상 고객의 업무 처리 상태입니다."""

    PENDING = "pending"
    ASSIGNED = "assigned"
    CONTACTED = "contacted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CampaignLifecycleStatus(str, Enum):
    """캠페인 자체의 실행 생명주기 상태입니다."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CampaignEventType(str, Enum):
    """캠페인·대상 업무 이력의 이벤트 종류입니다."""

    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    RESULT_UPDATED = "result_updated"
    CONVERSION_UPDATED = "conversion_updated"


class CampaignResultCode(str, Enum):
    """캠페인 접촉 결과를 집계 가능한 코드로 표준화합니다."""

    CONTACTED = "contacted"
    CONVERTED = "converted"
    NOT_CONVERTED = "not_converted"
    NO_RESPONSE = "no_response"
    DECLINED = "declined"
    OPTED_OUT = "opted_out"
    INVALID_CONTACT = "invalid_contact"


class ExperimentGroup(str, Enum):
    """캠페인 A/B 실험에서 고객이 배정된 그룹입니다."""

    TREATMENT = "treatment"
    CONTROL = "control"


class BulkTargetingSegment(str, Enum):
    """분석 결과를 캠페인 대상으로 변환하는 표준 세그먼트입니다."""

    HIGH_RISK_RETENTION = "high_risk_retention"
    MEDIUM_REACTIVATION = "medium_reactivation"
    LOW_RISK_UPSELL = "low_risk_upsell"
    # 아래 4종은 예측 위험도가 아니라 고객 원본 지표(리볼빙 잔액·거래건수)로
    # 정의합니다. 원본 데이터 10,127명에 대해 train/test 분할 양쪽에서 실제
    # 이탈률 차이가 재현되는 것을 확인한 조건만 채택했습니다.
    SMALL_BALANCE_DECLINE = "small_balance_decline"
    DORMANT_FULL_PAYER = "dormant_full_payer"
    ACTIVE_FULL_PAYER = "active_full_payer"
    STABLE_PRIME = "stable_prime"


class BulkTargetingRunStatus(str, Enum):
    """일괄 타기팅 배치의 생명주기 상태입니다."""

    PREVIEWED = "previewed"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
