"""분석 배치의 입력 변환과 운영 규칙을 검증합니다."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.analysis_batch import (
    BatchSummary,
    DECISION_POLICY_VERSION,
    _batch_key_sha256,
    _decision_policy_sha256,
    _next_batch_attempt,
    _recommended_action,
    _risk_level,
    build_regression_input,
)
from backend.app.migration_runner import upgrade_database
from backend.app.models import DecisionPolicy, ScoringBatch


def make_raw_row() -> dict[str, object]:
    """회귀 입력 변환에 필요한 원본 특성 한 행을 만듭니다."""
    return {
        "Customer_Age": 45,
        "Gender": "M",
        "Dependent_count": 2,
        "Education_Level": "Graduate",
        "Marital_Status": "Married",
        "Income_Category": "$60K - $80K",
        "Card_Category": "Blue",
        "Months_on_book": 36,
        "Total_Relationship_Count": 3,
        "Months_Inactive_12_mon": 2,
        "Contacts_Count_12_mon": 2,
        "Credit_Limit": 5000.0,
        "Total_Revolving_Bal": 1000,
        "Avg_Open_To_Buy": 4000.0,
        "Total_Amt_Chng_Q4_Q1": 0.7,
        "Total_Trans_Amt": 2000,
        "Total_Trans_Ct": 50,
        "Total_Ct_Chng_Q4_Q1": 0.8,
        "Avg_Utilization_Ratio": 0.2,
    }


def test_regression_input_matches_final_feature_contract() -> None:
    """배치 회귀 입력이 타겟·누수·금액 컬럼을 제외하는지 확인합니다."""
    result = build_regression_input(pd.DataFrame([make_raw_row()]))

    assert "Total_Trans_Ct" not in result.columns
    assert "Total_Trans_Amt" not in result.columns
    assert "Total_Ct_Chng_Q4_Q1" not in result.columns
    assert {"리볼빙_한도_비율", "상품당_관계밀도", "문의_대비_보유기간", "연령대"}.issubset(
        result.columns
    )


def test_risk_and_action_policy() -> None:
    """위험도 기준과 활동성 갭 기반 액션 문구가 일관되게 적용되는지 확인합니다."""
    # 활동성 갭이 임계값보다 높으면(=급감하지 않았으면) 확률만으로 구간이 정해집니다.
    healthy_gap = {"activity_gap": 0.0, "activity_gap_priority_threshold": -5.0}
    assert _risk_level(0.9, 0.5, 0.85, **healthy_gap) == "high"
    assert _risk_level(0.6, 0.5, 0.85, **healthy_gap) == "medium"
    assert _risk_level(0.2, 0.5, 0.85, **healthy_gap) == "low"
    # 확률이 낮아도 활동성 갭이 하위 분위수 이하로 급감하면 medium으로 올립니다
    # (확률만 쓰면 medium 구간이 거의 비어 재활성화 세그먼트가 작동하지 않던 문제).
    assert _risk_level(
        0.2, 0.5, 0.85, activity_gap=-6.0, activity_gap_priority_threshold=-5.0
    ) == "medium"
    assert _recommended_action("high", -3.0, "우선케어(거래 감소)") == (
        "이탈 위험 우선 상담 및 거래 활성화 혜택"
    )
    assert _recommended_action(
        "low",
        -3.0,
        "일반관리(유지)",
        activity_gap_priority_threshold=-5.0,
    ) == "일반 유지 관리"
    assert _recommended_action(
        "low",
        -5.0,
        "일반관리(유지)",
        activity_gap_priority_threshold=-5.0,
    ) == "저활동 고객 재활성화 캠페인"


def test_decision_policy_hash_is_stable_and_changes_with_policy() -> None:
    """정책 버전과 기준값이 같을 때만 동일 배치 재사용 해시가 나옵니다."""
    base = _decision_policy_sha256(
        medium_threshold=0.5,
        high_threshold=0.85,
        activity_gap_quantile=0.2,
    )

    assert DECISION_POLICY_VERSION == "activity-gap-v3"
    assert base == _decision_policy_sha256(
        medium_threshold=0.5,
        high_threshold=0.85,
        activity_gap_quantile=0.2,
    )
    assert base != _decision_policy_sha256(
        medium_threshold=0.6,
        high_threshold=0.85,
        activity_gap_quantile=0.2,
    )


def test_batch_key_and_summary_preserve_as_of_date() -> None:
    """분석 기준일이 달라지면 다른 배치가 생성되고 CLI JSON은 ISO 날짜를 사용합니다."""
    specs = [
        {"task": "classification", "artifact_sha256": "a" * 64},
        {"task": "regression", "artifact_sha256": "b" * 64},
        {"task": "clustering", "artifact_sha256": "c" * 64},
    ]
    first_key = _batch_key_sha256(
        as_of_date=date(2026, 8, 1),
        dataset_sha256="d" * 64,
        decision_policy_sha256="e" * 64,
        run_specs=specs,
    )
    next_day_key = _batch_key_sha256(
        as_of_date=date(2026, 8, 2),
        dataset_sha256="d" * 64,
        decision_policy_sha256="e" * 64,
        run_specs=specs,
    )
    summary = BatchSummary(
        processed_rows=1,
        classification_run_id=1,
        regression_run_id=2,
        clustering_run_id=3,
        scoring_batch_id=4,
        decision_policy_id=5,
        as_of_date=date(2026, 8, 1),
        reused_existing_snapshot=False,
        decision_policy_sha256="e" * 64,
        risk_counts={"low": 1},
        cluster_counts={"일반관리(유지)": 1},
    )

    assert first_key != next_day_key
    assert summary.to_dict()["as_of_date"] == "2026-08-01"


def test_failed_scoring_batch_gets_a_new_attempt(tmp_path: Path) -> None:
    """실패 이력을 보존하면서 같은 논리 배치를 두 번째 실행으로 재시도합니다."""
    database_url = f"sqlite:///{tmp_path / 'batch-retry.sqlite3'}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            policy = DecisionPolicy(
                version="retry-test",
                policy_sha256="a" * 64,
                medium_threshold=0.5,
                high_threshold=0.85,
                activity_gap_quantile=0.2,
            )
            session.add(policy)
            session.flush()
            reuse_key = "b" * 64
            session.add(
                ScoringBatch(
                    batch_key_sha256=reuse_key,
                    reuse_key_sha256=reuse_key,
                    attempt_number=1,
                    as_of_date=date(2026, 8, 1),
                    decision_policy_id=policy.id,
                    status="failed",
                )
            )
            session.commit()

            attempt_number, execution_key = _next_batch_attempt(session, reuse_key)

            assert attempt_number == 2
            assert execution_key != reuse_key
    finally:
        engine.dispose()
