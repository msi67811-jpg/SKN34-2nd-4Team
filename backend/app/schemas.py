"""API가 주고받는 요청 및 응답 데이터 구조를 정의합니다."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .enums import (
    CampaignLifecycleStatus,
    CampaignResultCode,
    CampaignStatus,
    BulkTargetingRunStatus,
    BulkTargetingSegment,
    ExperimentGroup,
    ModelRunStatus,
    RiskLevel,
    UserRole,
)


def _ensure_utc(value: datetime) -> datetime:
    """타임존이 없는 datetime을 UTC로 간주해 offset을 붙입니다.

    MySQL ``DATETIME``에는 타임존이 없어 ORM이 naive datetime을 돌려줍니다.
    그대로 직렬화하면 ``2026-08-04T02:27:22``처럼 offset 없는 문자열이 되고,
    브라우저의 ``new Date()``는 offset이 없는 값을 로컬 시간으로 해석하므로
    UTC로 저장된 시각이 KST로 잘못 표시됩니다. 애플리케이션은 항상 UTC로
    기록하므로, API 경계에서 UTC임을 명시해 이 혼동을 없앱니다.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


# API가 주고받는 모든 시각은 UTC offset을 포함합니다.
UtcDatetime = Annotated[datetime, AfterValidator(_ensure_utc)]


# 프론트엔드용 snake_case 필드명을 학습 데이터의 원본 컬럼명으로 연결합니다.
PREDICTION_FIELD_MAP = {
    "customer_age": "Customer_Age",
    "gender": "Gender",
    "dependent_count": "Dependent_count",
    "education_level": "Education_Level",
    "marital_status": "Marital_Status",
    "income_category": "Income_Category",
    "card_category": "Card_Category",
    "months_on_book": "Months_on_book",
    "total_relationship_count": "Total_Relationship_Count",
    "months_inactive_12_mon": "Months_Inactive_12_mon",
    "contacts_count_12_mon": "Contacts_Count_12_mon",
    "credit_limit": "Credit_Limit",
    "total_revolving_bal": "Total_Revolving_Bal",
    "avg_open_to_buy": "Avg_Open_To_Buy",
    "total_amt_chng_q4_q1": "Total_Amt_Chng_Q4_Q1",
    "total_trans_amt": "Total_Trans_Amt",
    "total_trans_ct": "Total_Trans_Ct",
    "total_ct_chng_q4_q1": "Total_Ct_Chng_Q4_Q1",
    "avg_utilization_ratio": "Avg_Utilization_Ratio",
}


class LivenessResponse(BaseModel):
    """API 프로세스의 생존 상태 응답입니다."""

    status: Literal["ok"]
    service: str
    version: str


class HealthResponse(BaseModel):
    """API와 적재된 모델의 준비 상태 응답입니다."""

    status: Literal["ok"]
    service: str
    version: str
    model_loaded: bool
    model_name: str
    model_artifact: str
    manifest_generated_at: str


class PredictionRequest(BaseModel):
    """학습된 분류 파이프라인이 요구하는 고객 특성 입력값입니다."""

    # 정의되지 않은 필드가 실수로 모델 입력에 섞이지 않도록 거부합니다.
    model_config = ConfigDict(extra="forbid")

    customer_age: int = Field(ge=26, le=73)
    gender: Literal["F", "M"]
    dependent_count: int = Field(ge=0, le=5)
    education_level: Literal[
        "College",
        "Doctorate",
        "Graduate",
        "High School",
        "Post-Graduate",
        "Uneducated",
        "Unknown",
    ]
    marital_status: Literal["Divorced", "Married", "Single", "Unknown"]
    income_category: Literal[
        "$120K +",
        "$40K - $60K",
        "$60K - $80K",
        "$80K - $120K",
        "Less than $40K",
        "Unknown",
    ]
    card_category: Literal["Blue", "Gold", "Platinum", "Silver"]
    months_on_book: int = Field(ge=13, le=56)
    total_relationship_count: int = Field(ge=1, le=6)
    months_inactive_12_mon: int = Field(ge=0, le=6)
    contacts_count_12_mon: int = Field(ge=0, le=6)
    credit_limit: float = Field(ge=1438.3, le=34516.0)
    total_revolving_bal: int = Field(ge=0, le=2517)
    avg_open_to_buy: float = Field(ge=3.0, le=34516.0)
    total_amt_chng_q4_q1: float = Field(ge=0.0, le=3.397)
    total_trans_amt: int = Field(ge=510, le=18484)
    total_trans_ct: int = Field(ge=10, le=139)
    total_ct_chng_q4_q1: float = Field(ge=0.0, le=3.714)
    avg_utilization_ratio: float = Field(ge=0.0, le=0.999)

    def to_model_input(self) -> dict[str, Any]:
        """검증된 API 입력을 모델이 학습한 원본 컬럼명 구조로 변환합니다."""
        values = self.model_dump()
        return {
            model_field: values[request_field]
            for request_field, model_field in PREDICTION_FIELD_MAP.items()
        }


class PredictionResponse(BaseModel):
    """고객 이탈 예측 결과와 사용된 모델 정보를 반환하는 응답입니다."""

    prediction: int
    status: str
    churn_probability: float = Field(ge=0.0, le=1.0)
    decision_threshold: float = Field(ge=0.0, le=1.0)
    model_name: str
    model_version: str


class CustomerProfileResponse(BaseModel):
    """분석 상세 화면에 제공할 고객 원본 특성입니다."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: int
    customer_age: int
    gender: str
    dependent_count: int
    education_level: str
    marital_status: str
    income_category: str
    card_category: str
    months_on_book: int
    total_relationship_count: int
    months_inactive_12_mon: int
    contacts_count_12_mon: int
    credit_limit: float
    total_revolving_bal: int
    avg_open_to_buy: float
    total_amt_chng_q4_q1: float
    total_trans_amt: int
    total_trans_ct: int
    total_ct_chng_q4_q1: float
    avg_utilization_ratio: float
    marketing_opt_out: bool = False
    last_contacted_at: UtcDatetime | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class CustomerFeatureSnapshotResponse(BaseModel):
    """분석 결과가 실제로 사용한 고객 입력 특성의 시점별 스냅샷입니다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    feature_sha256: str
    source_dataset_sha256: str | None
    as_of_date: date
    as_of_at: UtcDatetime
    customer_age: int
    gender: str
    dependent_count: int
    education_level: str
    marital_status: str
    income_category: str
    card_category: str
    months_on_book: int
    total_relationship_count: int
    months_inactive_12_mon: int
    contacts_count_12_mon: int
    credit_limit: float
    total_revolving_bal: int
    avg_open_to_buy: float
    total_amt_chng_q4_q1: float
    total_trans_amt: int
    total_trans_ct: int
    total_ct_chng_q4_q1: float
    avg_utilization_ratio: float


class CustomerInsightResponse(BaseModel):
    """고객별 최신 분석 결과 목록 항목입니다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    customer_snapshot_id: int | None = None
    scoring_batch_id: int | None = None
    as_of_date: date | None = None
    classification_run_id: int
    regression_run_id: int
    clustering_run_id: int
    churn_probability: float = Field(ge=0.0, le=1.0)
    risk_level: RiskLevel
    expected_transaction_count: float = Field(ge=0.0)
    activity_gap: float
    total_trans_amt: int = Field(ge=0)
    card_category: str
    contacts_count_12_mon: int = Field(ge=0)
    cluster_name: str
    cluster_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    recommended_action: str
    reason_codes: list[str] | dict[str, Any] | None
    scored_at: UtcDatetime


class CustomerInsightStats(BaseModel):
    """필터가 적용된 분석 결과의 대시보드 요약 통계입니다."""

    total: int
    average_churn_probability: float = Field(ge=0.0, le=1.0)
    risk_counts: dict[str, int]
    cluster_counts: dict[str, int]
    cluster_options: dict[str, int]


class CategoricalChurnRateItem(BaseModel):
    """범주형 변수 한 그룹의 이탈률 요약입니다."""

    group: str
    churn_rate: float = Field(ge=0.0, le=1.0)
    count: int


class CategoricalChurnRateResponse(BaseModel):
    """범주형 변수별 이탈률 분포입니다."""

    field: str
    items: list[CategoricalChurnRateItem]


class NumericDistributionBucket(BaseModel):
    """이탈 여부 한쪽 그룹의 수치형 변수 사분위 요약(박스플롯 입력)입니다."""

    min: float
    q1: float
    median: float
    q3: float
    max: float
    count: int


class NumericDistributionResponse(BaseModel):
    """이탈 여부(0/1)별 수치형 변수 분포입니다."""

    field: str
    by_target: dict[str, NumericDistributionBucket]


class FeatureCorrelationItem(BaseModel):
    """단일 변수와 이탈(Target) 간 상관계수입니다."""

    feature: str
    correlation: float = Field(ge=-1.0, le=1.0)


class FeatureCorrelationResponse(BaseModel):
    """수치형 변수와 이탈 간 상관계수 목록(절댓값 내림차순)입니다."""

    items: list[FeatureCorrelationItem]


class CustomerInsightHistoryResponse(BaseModel):
    """고객 한 명의 분석 스냅샷 이력입니다."""

    customer_id: int
    items: list[CustomerInsightResponse]


class ModelRunResponse(BaseModel):
    """모델 배치 실행 이력의 외부 응답 구조입니다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task: str
    model_name: str
    model_version: str
    artifact_sha256: str
    dataset_sha256: str | None
    scoring_batch_id: int | None
    decision_policy_sha256: str | None
    medium_threshold: float | None
    high_threshold: float | None
    activity_gap_quantile: float | None
    status: ModelRunStatus
    processed_rows: int | None
    started_at: UtcDatetime
    completed_at: UtcDatetime | None


class LatestBatchResponse(BaseModel):
    """가장 최근 성공한 전체 모델 배치의 요약입니다."""

    scoring_batch_id: int | None = None
    attempt_number: int | None = None
    as_of_date: date | None = None
    decision_policy_id: int | None = None
    decision_policy_sha256: str | None = None
    status: ModelRunStatus
    started_at: UtcDatetime
    completed_at: UtcDatetime | None
    processed_rows: int | None
    dataset_sha256: str | None
    runs: list[ModelRunResponse]


class CampaignStatsResponse(BaseModel):
    """캠페인 대상의 서버 집계 결과입니다."""

    total_targets: int
    unprocessed_targets: int
    contacted_targets: int
    converted_targets: int
    status_counts: dict[str, int]


class CampaignResponse(BaseModel):
    """캠페인 기본 정보와 대상 집계입니다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    channel: str | None
    segment_code: BulkTargetingSegment | None = None
    status: CampaignLifecycleStatus
    start_at: UtcDatetime | None
    end_at: UtcDatetime | None
    experiment_enabled: bool = False
    control_group_ratio: float = Field(ge=0.0, lt=1.0)
    experiment_policy_locked: bool = False
    experiment_assignment_version: str
    fixed_cost: float = Field(ge=0.0)
    cost_per_contact: float = Field(ge=0.0)
    revenue_per_conversion: float = Field(ge=0.0)
    retention_window_days: int = Field(ge=1, le=365)
    created_by_user_id: int | None
    created_by_display_name: str | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    stats: CampaignStatsResponse


class CampaignCreateRequest(BaseModel):
    """캠페인 기본 정보 생성 요청입니다."""

    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=4000)
    channel: str | None = Field(default=None, max_length=30)
    segment_code: BulkTargetingSegment | None = None
    status: CampaignLifecycleStatus = CampaignLifecycleStatus.DRAFT
    start_at: UtcDatetime | None = None
    end_at: UtcDatetime | None = None
    experiment_enabled: bool = False
    control_group_ratio: float = Field(default=0.2, ge=0.0, lt=1.0)
    fixed_cost: float = Field(default=0.0, ge=0.0)
    cost_per_contact: float = Field(default=0.0, ge=0.0)
    revenue_per_conversion: float = Field(default=0.0, ge=0.0)
    retention_window_days: int = Field(default=30, ge=1, le=365)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_experiment(self) -> "CampaignCreateRequest":
        if self.status != CampaignLifecycleStatus.DRAFT:
            raise ValueError("campaigns must be created with draft status")
        if self.experiment_enabled and self.control_group_ratio <= 0:
            raise ValueError("control_group_ratio must be greater than 0 for A/B tests")
        return self


class CampaignUpdateRequest(BaseModel):
    """캠페인 기본 정보와 생명주기 변경 요청입니다."""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=4000)
    channel: str | None = Field(default=None, max_length=30)
    segment_code: BulkTargetingSegment | None = None
    status: CampaignLifecycleStatus | None = None
    start_at: UtcDatetime | None = None
    end_at: UtcDatetime | None = None
    experiment_enabled: bool | None = None
    control_group_ratio: float | None = Field(default=None, ge=0.0, lt=1.0)
    fixed_cost: float | None = Field(default=None, ge=0.0)
    cost_per_contact: float | None = Field(default=None, ge=0.0)
    revenue_per_conversion: float | None = Field(default=None, ge=0.0)
    retention_window_days: int | None = Field(default=None, ge=1, le=365)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class CampaignListResponse(BaseModel):
    """캠페인 목록과 페이지네이션 응답입니다."""

    items: list[CampaignResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class CampaignTargetResponse(BaseModel):
    """캠페인 대상과 현재 업무 처리 상태입니다."""

    id: int
    customer_id: int
    customer_insight_id: int
    campaign_id: int | None = None
    campaign_name: str
    experiment_group: ExperimentGroup = ExperimentGroup.TREATMENT
    bulk_targeting_run_id: int | None = None
    campaign_status: CampaignLifecycleStatus | None = None
    assigned_to_user_id: int | None
    assigned_to_display_name: str | None = None
    status: CampaignStatus
    processed_at: UtcDatetime | None
    contacted_at: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None
    converted_at: UtcDatetime | None = None
    result: str | None
    result_notes: str | None
    result_code: CampaignResultCode | None = None
    converted: bool = False
    retained: bool | None = None
    retention_checked_at: UtcDatetime | None = None
    outcome_revenue: float | None = Field(default=None, ge=0.0)
    created_at: UtcDatetime
    updated_at: UtcDatetime


class CampaignTargetCreateRequest(BaseModel):
    """분석 결과를 캠페인 업무 대상으로 등록하는 요청입니다."""

    customer_insight_id: int = Field(ge=1)
    campaign_id: int | None = Field(default=None, ge=1)
    campaign_name: str | None = Field(default=None, min_length=1, max_length=150)
    assigned_to_user_id: int | None = Field(default=None, ge=1)

    @field_validator("campaign_name", mode="before")
    @classmethod
    def normalize_campaign_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_campaign_reference(self) -> "CampaignTargetCreateRequest":
        if self.campaign_id is None and self.campaign_name is None:
            raise ValueError("campaign_id or campaign_name is required")
        return self


class CampaignTargetUpdateRequest(BaseModel):
    """캠페인 대상의 담당자·상태·처리 결과를 변경하는 요청입니다."""

    status: CampaignStatus | None = None
    assigned_to_user_id: int | None = Field(default=None, ge=1)
    result: str | None = Field(default=None, max_length=100)
    result_notes: str | None = Field(default=None, max_length=2000)
    result_code: CampaignResultCode | None = None
    converted: bool | None = None
    retained: bool | None = None
    outcome_revenue: float | None = Field(default=None, ge=0.0)


class CampaignTargetListResponse(BaseModel):
    """캠페인 대상 목록과 페이지네이션 응답입니다."""

    items: list[CampaignTargetResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
    stats: CampaignStatsResponse | None = None


class CampaignEventResponse(BaseModel):
    """캠페인 대상의 상태·담당자·결과 변경 이력입니다."""

    id: int
    campaign_id: int
    campaign_target_id: int | None
    event_type: str
    from_status: str | None
    to_status: str | None
    actor_user_id: int | None
    actor_display_name: str | None = None
    note: str | None
    metadata_json: dict[str, Any] | None
    created_at: UtcDatetime


class CampaignEventListResponse(BaseModel):
    """캠페인 이벤트 이력의 페이지네이션 응답입니다."""

    items: list[CampaignEventResponse]
    page: int
    page_size: int
    total: int


class CampaignPerformanceMetrics(BaseModel):
    """캠페인 성과를 치료군·대조군과 재무 지표로 집계합니다."""

    target_count: int
    treatment_count: int
    control_count: int
    contacted_count: int
    converted_count: int
    retained_count: int
    retention_eligible_count: int
    retention_observed_count: int
    retention_observation_rate: float | None
    contact_rate: float
    conversion_rate: float
    retention_rate: float | None
    treatment_contact_rate: float | None
    control_contact_rate: float | None
    treatment_conversion_rate: float | None
    control_conversion_rate: float | None
    treatment_retention_rate: float | None
    control_retention_rate: float | None
    incremental_conversion_effect: float | None
    incremental_retention_effect: float | None
    incremental_conversions: float
    total_cost: float
    observed_revenue: float
    incremental_revenue: float
    total_revenue: float
    roi: float | None


class CampaignPerformanceBreakdown(CampaignPerformanceMetrics):
    """캠페인·세그먼트·담당자별 성과 비교 항목입니다."""

    key: str
    label: str
    campaign_count: int


class CampaignPerformanceResponse(BaseModel):
    """캠페인 성과 요약과 비교 차원별 집계입니다."""

    campaign_id: int | None
    segment_code: BulkTargetingSegment | None
    assigned_to_user_id: int | None
    summary: CampaignPerformanceMetrics
    by_campaign: list[CampaignPerformanceBreakdown]
    by_segment: list[CampaignPerformanceBreakdown]
    by_assignee: list[CampaignPerformanceBreakdown]
    generated_at: UtcDatetime


class BulkTargetingPreviewRequest(BaseModel):
    """세그먼트 일괄 타기팅 미리보기·실행에 사용할 정책입니다."""

    model_config = ConfigDict(extra="forbid")

    segment: BulkTargetingSegment
    campaign_name: str | None = Field(default=None, max_length=150)
    description: str | None = Field(default=None, max_length=4000)
    channel: str | None = Field(default=None, max_length=30)
    assigned_to_user_id: int | None = Field(default=None, ge=1)
    recent_contact_days: int = Field(default=30, ge=0, le=365)
    activity_gap_quantile: float = Field(default=0.2, gt=0.0, le=0.5)
    cluster_name: str | None = Field(default=None, max_length=100)
    max_targets: int = Field(default=1000, ge=1, le=10000)
    source_as_of_date: date | None = None
    scoring_batch_id: int | None = Field(default=None, ge=1)
    experiment_enabled: bool = False
    control_group_ratio: float = Field(default=0.2, ge=0.0, lt=1.0)
    fixed_cost: float = Field(default=0.0, ge=0.0)
    cost_per_contact: float = Field(default=0.0, ge=0.0)
    revenue_per_conversion: float = Field(default=0.0, ge=0.0)
    retention_window_days: int = Field(default=30, ge=1, le=365)

    @field_validator("campaign_name", "description", "channel", "cluster_name", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @model_validator(mode="after")
    def validate_experiment(self) -> "BulkTargetingPreviewRequest":
        if self.experiment_enabled and self.control_group_ratio <= 0:
            raise ValueError("control_group_ratio must be greater than 0 for A/B tests")
        return self


class BulkTargetingRerunRequest(BaseModel):
    """기존 정책을 재사용하되 캠페인명·최대 대상 수만 조정하는 요청입니다."""

    campaign_name: str | None = Field(default=None, max_length=150)
    max_targets: int | None = Field(default=None, ge=1, le=10000)

    @field_validator("campaign_name", mode="before")
    @classmethod
    def normalize_campaign_name(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class BulkTargetingPreviewItem(BaseModel):
    """일괄 타기팅 미리보기의 개별 eligible 고객입니다."""

    customer_id: int
    customer_insight_id: int
    risk_level: RiskLevel
    cluster_name: str
    churn_probability: float = Field(ge=0.0, le=1.0)
    expected_transaction_count: float = Field(ge=0.0)
    activity_gap: float
    recommended_action: str
    as_of_date: date | None


class BulkTargetingRunResponse(BaseModel):
    """일괄 타기팅 배치 상태·제외 집계·미리보기 항목입니다."""

    id: int
    segment: BulkTargetingSegment
    status: BulkTargetingRunStatus
    campaign_id: int | None
    campaign_name: str
    campaign_status: CampaignLifecycleStatus | None = None
    requested_by_user_id: int | None
    rerun_of_id: int | None
    source_as_of_date: date | None
    scoring_batch_id: int | None
    rules: dict[str, Any]
    preview_count: int
    eligible_count: int
    created_count: int
    skipped_active_campaign_count: int
    skipped_recent_contact_count: int
    skipped_opt_out_count: int
    cancelled_target_count: int
    items: list[BulkTargetingPreviewItem]
    created_at: UtcDatetime
    executed_at: UtcDatetime | None
    cancelled_at: UtcDatetime | None


class BulkTargetingRunListResponse(BaseModel):
    """일괄 타기팅 배치 목록 응답입니다."""

    items: list[BulkTargetingRunResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class CustomerContactPreferenceRequest(BaseModel):
    """고객 마케팅 수신 거부 상태 변경 요청입니다."""

    marketing_opt_out: bool


class CustomerContactPreferenceResponse(BaseModel):
    """고객 마케팅 수신 거부 상태와 최근 접촉 시각입니다."""

    customer_id: int
    marketing_opt_out: bool
    last_contacted_at: UtcDatetime | None


class CustomerInsightListResponse(BaseModel):
    """고객 분석 결과 목록과 페이지네이션·요약 통계입니다."""

    items: list[CustomerInsightResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
    stats: CustomerInsightStats


class CustomerInsightDetailResponse(CustomerInsightResponse):
    """고객 특성을 포함한 분석 결과 상세 응답입니다."""

    customer: CustomerProfileResponse
    customer_snapshot: CustomerFeatureSnapshotResponse | None = None


USERNAME_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,49}$"


def normalize_username(value: object) -> object:
    """팀 계정 아이디의 앞뒤 공백을 제거하고 소문자로 통일합니다."""
    if isinstance(value, str):
        return value.strip().lower()
    return value


class SignupRequest(BaseModel):
    """팀 계정 회원가입 요청입니다."""

    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=USERNAME_PATTERN,
    )
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_signup_username(cls, value: object) -> object:
        return normalize_username(value)

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class LoginRequest(BaseModel):
    """팀 계정 로그인 요청입니다."""

    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=USERNAME_PATTERN,
    )
    password: str = Field(min_length=8, max_length=128)
    remember_me: bool = False

    @field_validator("username", mode="before")
    @classmethod
    def normalize_login_username(cls, value: object) -> object:
        return normalize_username(value)


class UserResponse(BaseModel):
    """외부에 공개할 수 있는 사용자 정보입니다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: UserRole
    created_at: UtcDatetime


class TeamMemberResponse(BaseModel):
    """관리자와 캠페인 담당자 선택 화면에서 사용하는 팀 계정 정보입니다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: UtcDatetime


class UserAdminUpdateRequest(BaseModel):
    """관리자가 팀 계정의 역할과 활성 상태를 변경하는 요청입니다."""

    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_update_value(self) -> "UserAdminUpdateRequest":
        if self.role is None and self.is_active is None:
            raise ValueError("At least one user property must be provided.")
        return self


class AuthResponse(BaseModel):
    """회원가입·로그인 성공 응답입니다."""

    user: UserResponse
