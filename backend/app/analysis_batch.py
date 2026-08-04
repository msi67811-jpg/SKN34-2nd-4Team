"""분류·회귀·군집 모델을 한 번 실행하고 고객 분석 결과를 저장합니다.

이 모듈은 온라인 예측 API와 별개인 오프라인 배치 경로입니다. 원본 고객
특성은 ``customers``에서 읽고, 세 모델의 결과를 하나의
``customer_insights`` 스냅샷으로 묶어 저장합니다. 각 스냅샷은 세 개의
``model_runs``를 참조하므로 어떤 artifact로 계산했는지 추적할 수 있습니다.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import desc, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import PROJECT_ROOT, get_model_dir
from .enums import ModelRunStatus, RiskLevel
from .model_registry import ModelRegistry
from .models import (
    Customer,
    CustomerFeatureSnapshot,
    CustomerInsight,
    DecisionPolicy,
    ModelRun,
    ScoringBatch,
)
from .schemas import PREDICTION_FIELD_MAP


CLASSIFICATION_TASK = "classification"
REGRESSION_TASK = "regression"
CLUSTERING_TASK = "clustering"

REGRESSION_DROP_COLUMNS = {
    "Total_Trans_Ct",
    "Total_Ct_Chng_Q4_Q1",
    "Total_Trans_Amt",
    "Target",
}
DECISION_POLICY_VERSION = "activity-gap-v3"
DEFAULT_ACTIVITY_GAP_QUANTILE = 0.2
SNAPSHOT_ATTRIBUTE_NAMES = tuple(PREDICTION_FIELD_MAP.keys())
DECISION_POLICY_RULES = {
    "cluster_label_strategy": "activity_gap_rank_v1",
    "reason_rules": {
        "transaction_count": "batch_median_or_lower",
        "transaction_decline_cap": 0.7,
        "long_inactivity_months": 3,
        "frequent_contacts_count": 3,
        "low_relationship_count": 2,
        "zero_revolving_balance": 0,
        "dormant_utilization_ratio": 0.05,
    },
    "recommended_actions": {
        "high_negative_gap": "이탈 위험 우선 상담 및 거래 활성화 혜택",
        "high": "이탈 위험 고객 상담 및 관계 유지",
        "priority_activity_gap": "저활동 고객 재활성화 캠페인",
        "frequent_contacts": "VOC 우선 검토 및 불만 원인 파악",
        "dormant_low_utilization": "동면 고객 재활성화 캠페인 (캐시백/한도 상향 오퍼)",
        "low_premium_cluster": "우량 고객 업셀링 검토",
        "default": "일반 유지 관리",
    },
    "regression_input_contract": "activity_gap_v1",
}


@dataclass(frozen=True)
class BatchSummary:
    """성공한 배치의 요약 정보입니다."""

    processed_rows: int
    classification_run_id: int
    regression_run_id: int
    clustering_run_id: int
    scoring_batch_id: int
    decision_policy_id: int
    as_of_date: date
    reused_existing_snapshot: bool
    decision_policy_sha256: str
    risk_counts: dict[str, int]
    cluster_counts: dict[str, int]
    attempt_number: int = 1

    def to_dict(self) -> dict[str, Any]:
        """CLI에서 출력할 수 있는 JSON 직렬화용 딕셔너리입니다."""
        payload = asdict(self)
        payload["as_of_date"] = self.as_of_date.isoformat()
        return payload


def sha256_file(path: Path) -> str:
    """파일을 메모리에 모두 올리지 않고 SHA-256을 계산합니다."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_path(model_dir: Path, filename: str) -> Path:
    """모델 디렉터리 안의 artifact 경로를 검증해 반환합니다."""
    path = (model_dir / filename).resolve()
    root = model_dir.resolve()
    if not path.is_relative_to(root) or path.name != filename:
        raise ValueError(f"Model artifact must be a file in MODEL_DIR: {filename}")
    if not path.is_file():
        raise FileNotFoundError(f"Required model artifact not found: {path}")
    return path


def _display_path(path: Path) -> str:
    """실행 환경과 무관하게 저장할 수 있는 artifact 상대 경로를 만듭니다."""
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def _dataset_hash() -> str | None:
    """현재 배치의 원천 파일 해시를 반환합니다."""
    candidates = [
        Path(os.environ["DATASET_PATH"])
        if os.environ.get("DATASET_PATH")
        else None,
        PROJECT_ROOT / "data" / "processed" / "bankchurners_clean.csv",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return sha256_file(candidate)
    return None


def _customer_feature_values(
    customer: Customer | CustomerFeatureSnapshot,
) -> dict[str, Any]:
    """고객 ORM 객체에서 모델 입력에 해당하는 snake_case 값을 추출합니다."""
    return {
        attribute: getattr(customer, attribute)
        for attribute in SNAPSHOT_ATTRIBUTE_NAMES
    }


def _canonical_json(value: dict[str, Any]) -> bytes:
    """정책·특성 해시가 실행 환경에 따라 달라지지 않게 직렬화합니다."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _feature_sha256(values: dict[str, Any]) -> str:
    """고객 입력 특성 한 행의 SHA-256을 반환합니다."""
    return hashlib.sha256(_canonical_json(values)).hexdigest()


def _customer_dataset_hash(
    customers: list[Customer | CustomerFeatureSnapshot],
) -> str:
    """현재 DB 고객 특성 전체의 정렬·정규화된 해시를 반환합니다."""
    digest = hashlib.sha256()
    for customer in customers:
        digest.update(str(customer.customer_id).encode("ascii"))
        digest.update(b"\0")
        digest.update(_canonical_json(_customer_feature_values(customer)))
        digest.update(b"\n")
    return digest.hexdigest()


def _decision_policy_payload(
    *,
    medium_threshold: float,
    high_threshold: float,
    activity_gap_quantile: float,
) -> dict[str, Any]:
    """결과에 영향을 주는 임계값·라벨·액션 규칙 전체를 정규화합니다."""
    return {
        "version": DECISION_POLICY_VERSION,
        "medium_threshold": medium_threshold,
        "high_threshold": high_threshold,
        "activity_gap_quantile": activity_gap_quantile,
        **DECISION_POLICY_RULES,
    }


def _decision_policy_sha256(
    *,
    medium_threshold: float,
    high_threshold: float,
    activity_gap_quantile: float,
) -> str:
    """결과에 영향을 주는 의사결정 정책 전체를 해시합니다."""
    return hashlib.sha256(
        _canonical_json(
            _decision_policy_payload(
                medium_threshold=medium_threshold,
                high_threshold=high_threshold,
                activity_gap_quantile=activity_gap_quantile,
            )
        )
    ).hexdigest()


def _batch_key_sha256(
    *,
    as_of_date: date,
    dataset_sha256: str | None,
    decision_policy_sha256: str,
    run_specs: list[dict[str, Any]],
    force_nonce: str | None = None,
) -> str:
    """배치 입력·artifact·정책·시점을 묶은 재사용 키를 생성합니다."""
    return hashlib.sha256(
        _canonical_json(
            {
                "as_of_date": as_of_date.isoformat(),
                "dataset_sha256": dataset_sha256,
                "decision_policy_sha256": decision_policy_sha256,
                "artifacts": [
                    {
                        "task": spec["task"],
                        "artifact_sha256": spec["artifact_sha256"],
                    }
                    for spec in run_specs
                ],
                "force_nonce": force_nonce,
            }
        )
    ).hexdigest()


def _next_batch_attempt(
    session: Session,
    reuse_key_sha256: str,
) -> tuple[int, str]:
    """논리적으로 같은 배치의 다음 시도 번호와 고유 실행 키를 생성합니다."""
    previous_attempt = int(
        session.scalar(
            select(func.max(ScoringBatch.attempt_number)).where(
                ScoringBatch.reuse_key_sha256 == reuse_key_sha256
            )
        )
        or 0
    )
    attempt_number = previous_attempt + 1
    execution_key = (
        reuse_key_sha256
        if attempt_number == 1
        else hashlib.sha256(
            f"{reuse_key_sha256}:{attempt_number}".encode("utf-8")
        ).hexdigest()
    )
    return attempt_number, execution_key


def _get_or_create_decision_policy(
    session: Session,
    *,
    policy_sha256: str,
    medium_threshold: float,
    high_threshold: float,
    activity_gap_quantile: float,
    policy_json: dict[str, Any],
) -> DecisionPolicy:
    """동일 정책은 하나의 immutable registry 행으로 재사용합니다."""
    policy = session.scalar(
        select(DecisionPolicy).where(
            DecisionPolicy.policy_sha256 == policy_sha256
        )
    )
    if policy is None:
        candidate = DecisionPolicy(
            version=DECISION_POLICY_VERSION,
            policy_sha256=policy_sha256,
            medium_threshold=medium_threshold,
            high_threshold=high_threshold,
            activity_gap_quantile=activity_gap_quantile,
            policy_json=policy_json,
        )
        try:
            with session.begin_nested():
                session.add(candidate)
                session.flush()
            policy = candidate
        except IntegrityError:
            policy = session.scalar(
                select(DecisionPolicy).where(
                    DecisionPolicy.policy_sha256 == policy_sha256
                )
            )
            if policy is None:
                raise
    return policy


def _ensure_feature_snapshots(
    session: Session,
    customers: list[Customer],
    *,
    source_dataset_sha256: str | None,
    as_of_at: datetime,
    as_of_date: date,
) -> list[CustomerFeatureSnapshot]:
    """현재 고객 특성을 재현 가능한 스냅샷으로 만들고 고객 순서를 보존합니다."""
    customer_ids = [customer.customer_id for customer in customers]
    existing = session.scalars(
        select(CustomerFeatureSnapshot).where(
            CustomerFeatureSnapshot.customer_id.in_(customer_ids)
        )
    ).all()
    existing_by_key = {
        (snapshot.customer_id, snapshot.feature_sha256, snapshot.as_of_date): snapshot
        for snapshot in existing
    }

    snapshots: list[CustomerFeatureSnapshot] = []
    for customer in customers:
        values = _customer_feature_values(customer)
        feature_sha256 = _feature_sha256(values)
        snapshot = existing_by_key.get(
            (customer.customer_id, feature_sha256, as_of_date)
        )
        if snapshot is None:
            snapshot = CustomerFeatureSnapshot(
                customer_id=customer.customer_id,
                feature_sha256=feature_sha256,
                source_dataset_sha256=source_dataset_sha256,
                as_of_at=as_of_at,
                as_of_date=as_of_date,
                **values,
            )
            session.add(snapshot)
        snapshots.append(snapshot)

    session.flush()
    return snapshots


def _load_historical_feature_snapshots(
    session: Session,
    *,
    as_of_date: date,
) -> list[CustomerFeatureSnapshot]:
    """요청 기준일에 실제로 보존된 고객 특성만 과거 재분석 입력으로 사용합니다."""
    ranked = (
        select(
            CustomerFeatureSnapshot.id.label("snapshot_id"),
            func.row_number()
            .over(
                partition_by=CustomerFeatureSnapshot.customer_id,
                order_by=(
                    desc(CustomerFeatureSnapshot.as_of_at),
                    desc(CustomerFeatureSnapshot.id),
                ),
            )
            .label("row_number"),
        )
        .where(CustomerFeatureSnapshot.as_of_date == as_of_date)
        .subquery()
    )
    snapshots = list(
        session.scalars(
            select(CustomerFeatureSnapshot)
            .join(ranked, ranked.c.snapshot_id == CustomerFeatureSnapshot.id)
            .where(ranked.c.row_number == 1)
            .order_by(CustomerFeatureSnapshot.customer_id)
        ).all()
    )
    if not snapshots:
        raise ValueError(
            "Historical scoring requires immutable customer feature snapshots "
            f"for as_of_date={as_of_date.isoformat()}."
        )
    return snapshots


def _customer_frame(
    customers: list[Customer | CustomerFeatureSnapshot],
) -> tuple[pd.DataFrame, pd.Series]:
    """ORM 고객 목록을 모델 원본 컬럼명을 가진 DataFrame으로 변환합니다."""
    records: list[dict[str, Any]] = []
    customer_ids: list[int] = []
    for customer in customers:
        customer_ids.append(int(customer.customer_id))
        records.append(
            {
                model_field: getattr(customer, request_field)
                for request_field, model_field in PREDICTION_FIELD_MAP.items()
            }
        )
    return pd.DataFrame(records), pd.Series(customer_ids, name="customer_id")


def build_regression_input(raw_features: pd.DataFrame) -> pd.DataFrame:
    """최종 회귀 artifact와 동일한 파생변수·누수 제거 규칙을 적용합니다."""
    data = raw_features.copy()
    data["리볼빙_한도_비율"] = (
        data["Total_Revolving_Bal"] / data["Credit_Limit"]
    )
    data["상품당_관계밀도"] = (
        data["Total_Relationship_Count"] / data["Months_on_book"]
    )
    data["문의_대비_보유기간"] = (
        data["Contacts_Count_12_mon"] / data["Months_on_book"]
    )
    data["연령대"] = pd.cut(
        data["Customer_Age"],
        bins=[0, 30, 40, 50, 60, 100],
        labels=["20대이하", "30대", "40대", "50대", "60대이상"],
    )
    return data.drop(
        columns=[column for column in REGRESSION_DROP_COLUMNS if column in data]
    )


def _cluster_label_map(artifact: dict[str, Any]) -> dict[int, str]:
    """활동성 갭 평균을 기준으로 GMM 군집에 업무 라벨을 붙입니다."""
    model = artifact["model"]
    scaler = artifact["scaler"]
    if hasattr(model, "means_"):
        means = scaler.inverse_transform(np.asarray(model.means_))
        gap_means = means[:, 0]
    elif hasattr(model, "cluster_centers_"):
        centers = scaler.inverse_transform(np.asarray(model.cluster_centers_))
        gap_means = centers[:, 0]
    else:
        return {}

    ordered = np.argsort(gap_means)
    labels = {int(ordered[0]): "우선케어(거래 감소)"}
    if len(ordered) > 1:
        labels[int(ordered[-1])] = "우량(예상이상)"
    for cluster_id in ordered[1:-1]:
        labels[int(cluster_id)] = "일반관리(유지)"
    return labels


def _risk_level(
    probability: float,
    medium_threshold: float,
    high_threshold: float,
    *,
    activity_gap: float,
    activity_gap_priority_threshold: float,
) -> str:
    """운영용 이탈 확률 구간을 반환합니다.

    이탈확률만으로는 medium 구간이 거의 비어(전체의 0.4% 미만) 조기 재활성화
    세그먼트가 사실상 작동하지 않았다. 확률로는 아직 high가 아니지만, 활동성
    갭이 하위 분위수(activity_gap_priority_threshold) 이하로 급감한 고객도
    medium으로 함께 분류해 "조짐이 보이는" 고객을 잡아낸다.
    """
    if probability >= high_threshold:
        return RiskLevel.HIGH.value
    if probability >= medium_threshold or activity_gap <= activity_gap_priority_threshold:
        return RiskLevel.MEDIUM.value
    return RiskLevel.LOW.value


def _reason_codes(
    row: pd.Series,
    *,
    transaction_count_median: float,
    count_change_median: float,
    activity_gap: float,
    activity_gap_priority_threshold: float,
) -> list[str]:
    """원본 특성 및 활동성 갭으로 설명 가능한 운영 사유 코드를 만듭니다."""
    reasons: list[str] = []
    if float(row["Total_Trans_Ct"]) <= transaction_count_median:
        reasons.append("low_transaction_activity")
    if float(row["Total_Ct_Chng_Q4_Q1"]) <= min(count_change_median, 0.7):
        reasons.append("transaction_decline")
    if float(row["Months_Inactive_12_mon"]) >= 3:
        reasons.append("long_inactivity")
    if float(row["Contacts_Count_12_mon"]) >= 3:
        reasons.append("frequent_contacts")
    if float(row["Total_Relationship_Count"]) <= 2:
        reasons.append("low_relationship_count")
    if float(row["Total_Revolving_Bal"]) <= 0:
        reasons.append("zero_revolving_balance")
    if float(row["Avg_Utilization_Ratio"]) < 0.05:
        reasons.append("dormant_low_utilization")
    if activity_gap <= activity_gap_priority_threshold:
        reasons.append("priority_activity_gap")
    elif activity_gap < 0:
        reasons.append("below_expected_activity")
    return reasons or ["stable_activity"]


def _recommended_action(
    risk_level: str,
    activity_gap: float,
    cluster_name: str,
    *,
    activity_gap_priority_threshold: float = 0.0,
    reasons: list[str] | None = None,
) -> str:
    """위험도(risk_level)를 최우선 기준으로 삼고, 그 안에서 활동성 갭·사유
    코드를 조합해 액션을 정합니다. 위험도별로 분기를 명시적으로 나눠서
    "위 단계에서 안 걸리면 낮은 위험도일 것" 같은 암묵적 소거 관계에
    의존하지 않도록 합니다.
    """
    reasons = reasons or []
    low_activity = activity_gap <= activity_gap_priority_threshold
    dormant_and_zero_balance = (
        "dormant_low_utilization" in reasons and "zero_revolving_balance" in reasons
    )

    if risk_level == RiskLevel.HIGH.value:
        if activity_gap < 0:
            return "이탈 위험 우선 상담 및 거래 활성화 혜택"
        return "이탈 위험 고객 상담 및 관계 유지"

    if risk_level == RiskLevel.MEDIUM.value:
        if low_activity:
            return "거래 활동 이상감지 — 사전 개입 필요"
        if "frequent_contacts" in reasons:
            return "VOC 우선 검토 및 불만 원인 파악"
        return "일반 유지 관리"

    # risk_level == RiskLevel.LOW.value
    if "frequent_contacts" in reasons:
        return "VOC 우선 검토 및 불만 원인 파악"
    if dormant_and_zero_balance and low_activity:
        return "동면 고객 재활성화 캠페인 (캐시백/한도 상향 오퍼)"
    if low_activity:
        return "저활동 고객 재활성화 캠페인"
    if cluster_name == "우량(예상이상)":
        return "우량 고객 업셀링 검토"
    return "일반 유지 관리"

def _reusable_snapshot(
    session: Session,
    *,
    processed_rows: int,
    reuse_key_sha256: str,
    run_specs: list[dict[str, Any]],
) -> BatchSummary | None:
    """동일 입력·정책·artifact로 이미 완성된 scoring batch를 재사용합니다."""
    batch = session.scalar(
        select(ScoringBatch)
        .where(
            ScoringBatch.reuse_key_sha256 == reuse_key_sha256,
            ScoringBatch.status == ModelRunStatus.SUCCEEDED.value,
        )
        .order_by(
            ScoringBatch.attempt_number.desc(),
            ScoringBatch.completed_at.desc(),
            ScoringBatch.id.desc(),
        )
        .limit(1)
    )
    if batch is None:
        return None

    runs = session.scalars(
        select(ModelRun).where(
            ModelRun.scoring_batch_id == batch.id,
            ModelRun.status == ModelRunStatus.SUCCEEDED.value,
        )
    ).all()
    runs_by_task = {run.task: run for run in runs}
    if any(spec["task"] not in runs_by_task for spec in run_specs):
        return None
    for spec in run_specs:
        run = runs_by_task[spec["task"]]
        if run.artifact_sha256 != spec["artifact_sha256"]:
            return None

    count = session.scalar(
        select(func.count())
        .select_from(CustomerInsight)
        .where(
            CustomerInsight.scoring_batch_id == batch.id,
        )
    )
    if int(count or 0) != processed_rows:
        return None

    risk_counts = dict(
        session.execute(
            select(CustomerInsight.risk_level, func.count())
            .where(
                CustomerInsight.scoring_batch_id == batch.id,
            )
            .group_by(CustomerInsight.risk_level)
        ).all()
    )
    cluster_counts = dict(
        session.execute(
            select(CustomerInsight.cluster_name, func.count())
            .where(
                CustomerInsight.scoring_batch_id == batch.id,
            )
            .group_by(CustomerInsight.cluster_name)
        ).all()
    )
    return BatchSummary(
        processed_rows=processed_rows,
        classification_run_id=runs_by_task[CLASSIFICATION_TASK].id,
        regression_run_id=runs_by_task[REGRESSION_TASK].id,
        clustering_run_id=runs_by_task[CLUSTERING_TASK].id,
        scoring_batch_id=batch.id,
        decision_policy_id=batch.decision_policy_id,
        as_of_date=batch.as_of_date,
        reused_existing_snapshot=True,
        decision_policy_sha256=batch.decision_policy.policy_sha256,
        risk_counts={str(key): int(value) for key, value in risk_counts.items()},
        cluster_counts={str(key): int(value) for key, value in cluster_counts.items()},
        attempt_number=batch.attempt_number,
    )


def run_batch(
    session: Session,
    *,
    model_dir: Path | None = None,
    medium_threshold: float = 0.4,
    high_threshold: float = 0.85,
    activity_gap_quantile: float = DEFAULT_ACTIVITY_GAP_QUANTILE,
    as_of_date: date | None = None,
    force: bool = False,
) -> BatchSummary:
    """세 모델을 실행하고 고객별 분석 스냅샷을 MySQL에 저장합니다."""
    if not 0.0 <= medium_threshold < high_threshold <= 1.0:
        raise ValueError("Thresholds must satisfy 0 <= medium < high <= 1.")
    if not 0.0 < activity_gap_quantile < 1.0:
        raise ValueError("activity_gap_quantile must satisfy 0 < quantile < 1.")
    current_date = datetime.now(timezone.utc).date()
    resolved_as_of_date = as_of_date or current_date
    if resolved_as_of_date > current_date:
        raise ValueError("as_of_date cannot be in the future.")

    uses_historical_snapshots = resolved_as_of_date < current_date
    if uses_historical_snapshots:
        customers: list[Customer | CustomerFeatureSnapshot] = list(
            _load_historical_feature_snapshots(
                session,
                as_of_date=resolved_as_of_date,
            )
        )
    else:
        customers = list(
            session.scalars(select(Customer).order_by(Customer.customer_id)).all()
        )
    if not customers:
        raise ValueError("No customers found. Import customers before scoring.")

    dataset_sha256 = _customer_dataset_hash(customers)
    if uses_historical_snapshots:
        historical_source_hashes = {
            snapshot.source_dataset_sha256
            for snapshot in customers
            if isinstance(snapshot, CustomerFeatureSnapshot)
            and snapshot.source_dataset_sha256 is not None
        }
        source_dataset_sha256 = (
            next(iter(historical_source_hashes))
            if len(historical_source_hashes) == 1
            else None
        )
    else:
        source_dataset_sha256 = _dataset_hash() or dataset_sha256
    decision_policy_json = _decision_policy_payload(
        medium_threshold=medium_threshold,
        high_threshold=high_threshold,
        activity_gap_quantile=activity_gap_quantile,
    )
    decision_policy_sha256 = _decision_policy_sha256(
        medium_threshold=medium_threshold,
        high_threshold=high_threshold,
        activity_gap_quantile=activity_gap_quantile,
    )

    model_root = (model_dir or get_model_dir()).resolve()
    raw_features, customer_ids = _customer_frame(customers)

    # 1) 분류: 온라인 API와 같은 manifest 검증·기본 모델을 사용합니다.
    registry = ModelRegistry(model_root)
    registry.load()
    classification_result = registry.predict_batch(raw_features)
    classification_artifact = _artifact_path(
        model_root, registry.default_model.artifact
    )
    assert registry.manifest is not None

    # 2) 회귀: 저장된 Voting pipeline으로 기대 거래건수를 계산합니다.
    regression_artifact = _artifact_path(model_root, "regression_model.joblib")
    regression_model = joblib.load(regression_artifact)
    regression_input = build_regression_input(raw_features)
    expected_transactions = np.maximum(
        np.asarray(regression_model.predict(regression_input), dtype="float64"),
        0.0,
    )
    actual_transactions = raw_features["Total_Trans_Ct"].to_numpy(dtype="float64")
    activity_gap = actual_transactions - expected_transactions

    # 3) 군집: regression_final과 함께 생성된 activity-gap GMM을 사용합니다.
    clustering_artifact = _artifact_path(
        model_root, "clustering_activity_gap.joblib"
    )
    clustering_bundle = joblib.load(clustering_artifact)
    cluster_features = list(clustering_bundle["features"])
    cluster_input = pd.DataFrame(
        {
            "예상_대비_거래_차이": activity_gap,
            "실제_거래건수": actual_transactions,
        }
    )
    if cluster_features != list(cluster_input.columns):
        raise ValueError(
            "Activity-gap clustering artifact features do not match the batch input."
        )
    scaled_cluster_input = clustering_bundle["scaler"].transform(
        cluster_input[cluster_features]
    )
    cluster_model = clustering_bundle["model"]
    cluster_ids = np.asarray(cluster_model.predict(scaled_cluster_input), dtype=int)
    if callable(getattr(cluster_model, "predict_proba", None)):
        cluster_confidence = np.asarray(
            cluster_model.predict_proba(scaled_cluster_input).max(axis=1),
            dtype="float64",
        )
    else:
        cluster_confidence = np.full(len(customers), np.nan, dtype="float64")
    cluster_labels = _cluster_label_map(clustering_bundle)
    cluster_names = [
        cluster_labels.get(int(cluster_id), f"군집-{int(cluster_id)}")
        for cluster_id in cluster_ids
    ]

    activity_gap_priority_threshold = float(
        np.quantile(activity_gap, activity_gap_quantile)
    )

    run_specs = [
        {
            "task": CLASSIFICATION_TASK,
            "model_name": registry.default_model.name,
            "model_version": registry.manifest.generated_at.isoformat(),
            "artifact_path": _display_path(classification_artifact),
            "artifact_sha256": registry.default_model.sha256,
            "decision_policy_sha256": decision_policy_sha256,
        },
        {
            "task": REGRESSION_TASK,
            "model_name": "Voting",
            "model_version": sha256_file(regression_artifact),
            "artifact_path": _display_path(regression_artifact),
            "artifact_sha256": sha256_file(regression_artifact),
            "decision_policy_sha256": decision_policy_sha256,
        },
        {
            "task": CLUSTERING_TASK,
            "model_name": "GMM(spherical) activity_gap",
            "model_version": sha256_file(clustering_artifact),
            "artifact_path": _display_path(clustering_artifact),
            "artifact_sha256": sha256_file(clustering_artifact),
            "decision_policy_sha256": decision_policy_sha256,
        },
    ]

    now = datetime.now(timezone.utc)
    reuse_key = _batch_key_sha256(
        as_of_date=resolved_as_of_date,
        dataset_sha256=dataset_sha256,
        decision_policy_sha256=decision_policy_sha256,
        run_specs=run_specs,
    )

    if not force:
        reusable = _reusable_snapshot(
            session,
            processed_rows=len(customers),
            reuse_key_sha256=reuse_key,
            run_specs=run_specs,
        )
        if reusable is not None:
            return reusable

    decision_policy = _get_or_create_decision_policy(
        session,
        policy_sha256=decision_policy_sha256,
        medium_threshold=medium_threshold,
        high_threshold=high_threshold,
        activity_gap_quantile=activity_gap_quantile,
        policy_json=decision_policy_json,
    )
    attempt_number, batch_key = _next_batch_attempt(session, reuse_key)
    scoring_batch = ScoringBatch(
        batch_key_sha256=batch_key,
        reuse_key_sha256=reuse_key,
        attempt_number=attempt_number,
        as_of_date=resolved_as_of_date,
        source_dataset_sha256=source_dataset_sha256,
        dataset_sha256=dataset_sha256,
        decision_policy_id=decision_policy.id,
        status=ModelRunStatus.RUNNING.value,
        started_at=now,
    )
    session.add(scoring_batch)
    session.flush()
    runs = [
        ModelRun(
            task=spec["task"],
            model_name=spec["model_name"],
            model_version=spec["model_version"],
            artifact_path=spec["artifact_path"],
            artifact_sha256=spec["artifact_sha256"],
            dataset_sha256=dataset_sha256,
            scoring_batch_id=scoring_batch.id,
            decision_policy_sha256=decision_policy_sha256,
            medium_threshold=medium_threshold,
            high_threshold=high_threshold,
            activity_gap_quantile=activity_gap_quantile,
            status=ModelRunStatus.RUNNING.value,
            processed_rows=None,
            started_at=now,
        )
        for spec in run_specs
    ]
    session.add_all(runs)
    session.flush()
    run_ids = [run.id for run in runs]
    scoring_batch_id = scoring_batch.id
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        reusable = _reusable_snapshot(
            session,
            processed_rows=len(customers),
            reuse_key_sha256=reuse_key,
            run_specs=run_specs,
        )
        if reusable is not None:
            return reusable
        raise RuntimeError(
            "An identical scoring batch is already running. Retry after it completes."
        ) from None

    try:
        snapshots = (
            [
                snapshot
                for snapshot in customers
                if isinstance(snapshot, CustomerFeatureSnapshot)
            ]
            if uses_historical_snapshots
            else _ensure_feature_snapshots(
                session,
                [customer for customer in customers if isinstance(customer, Customer)],
                source_dataset_sha256=source_dataset_sha256,
                as_of_at=now,
                as_of_date=resolved_as_of_date,
            )
        )

        transaction_count_median = float(raw_features["Total_Trans_Ct"].median())
        count_change_median = float(raw_features["Total_Ct_Chng_Q4_Q1"].median())
        insight_records: list[dict[str, Any]] = []
        for index, row in raw_features.iterrows():
            probability = float(
                classification_result.iloc[index]["churn_probability"]
            )
            gap = float(activity_gap[index])
            risk = _risk_level(
                probability,
                medium_threshold,
                high_threshold,
                activity_gap=gap,
                activity_gap_priority_threshold=activity_gap_priority_threshold,
            )
            cluster_name = cluster_names[index]
            reasons = _reason_codes(
                row,
                transaction_count_median=transaction_count_median,
                count_change_median=count_change_median,
                activity_gap=gap,
                activity_gap_priority_threshold=activity_gap_priority_threshold,
            )
            insight_records.append(
                {
                    "customer_id": int(customer_ids.iloc[index]),
                    "customer_snapshot_id": snapshots[index].id,
                    "scoring_batch_id": scoring_batch.id,
                    "as_of_date": resolved_as_of_date,
                    "classification_run_id": runs[0].id,
                    "regression_run_id": runs[1].id,
                    "clustering_run_id": runs[2].id,
                    "churn_probability": probability,
                    "risk_level": risk,
                    "expected_transaction_count": float(
                        expected_transactions[index]
                    ),
                    "activity_gap": gap,
                    "cluster_name": cluster_name,
                    "cluster_confidence": float(cluster_confidence[index])
                    if np.isfinite(cluster_confidence[index])
                    else None,
                    "recommended_action": _recommended_action(
                        risk,
                        gap,
                        cluster_name,
                        activity_gap_priority_threshold=activity_gap_priority_threshold,
                        reasons=reasons,
                    ),
                    "reason_codes": reasons,
                    "scored_at": now,
                }
            )

        session.execute(insert(CustomerInsight), insight_records)
        completed_at = datetime.now(timezone.utc)
        for run in runs:
            run.status = ModelRunStatus.SUCCEEDED.value
            run.processed_rows = len(customers)
            run.completed_at = completed_at
        scoring_batch.status = ModelRunStatus.SUCCEEDED.value
        scoring_batch.processed_rows = len(customers)
        scoring_batch.completed_at = completed_at
        session.commit()
    except Exception as exc:
        session.rollback()
        failed_runs = list(
            session.scalars(select(ModelRun).where(ModelRun.id.in_(run_ids))).all()
        )
        for run in failed_runs:
            run.status = ModelRunStatus.FAILED.value
            run.error_message = str(exc)[:4000]
            run.completed_at = datetime.now(timezone.utc)
        failed_batch = session.get(ScoringBatch, scoring_batch_id)
        if failed_batch is not None:
            failed_batch.status = ModelRunStatus.FAILED.value
            failed_batch.error_message = str(exc)[:4000]
            failed_batch.completed_at = datetime.now(timezone.utc)
        session.commit()
        raise

    return BatchSummary(
        processed_rows=len(customers),
        classification_run_id=runs[0].id,
        regression_run_id=runs[1].id,
        clustering_run_id=runs[2].id,
        scoring_batch_id=scoring_batch.id,
        decision_policy_id=decision_policy.id,
        as_of_date=resolved_as_of_date,
        reused_existing_snapshot=False,
        decision_policy_sha256=decision_policy_sha256,
        risk_counts=dict(Counter(record["risk_level"] for record in insight_records)),
        cluster_counts=dict(Counter(record["cluster_name"] for record in insight_records)),
        attempt_number=attempt_number,
    )
