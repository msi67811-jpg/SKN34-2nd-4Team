"""Alembic migration, 기존 사용자 보존과 고객 데이터 적재를 검증합니다."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from alembic import command
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from backend.app.customer_import import load_customer_rows, upsert_customers
from backend.app.migration_runner import build_alembic_config, upgrade_database
from backend.app.enums import CampaignStatus, ModelRunStatus, RiskLevel, UserRole
from backend.app.models import (
    CampaignTarget,
    Customer,
    CustomerFeatureSnapshot,
    CustomerInsight,
    DecisionPolicy,
    ModelRun,
    ScoringBatch,
    User,
)
from backend.app.schemas import PREDICTION_FIELD_MAP


EXPECTED_TABLES = {
    "alembic_version",
    "users",
    "customers",
    "customer_feature_snapshots",
    "decision_policies",
    "scoring_batches",
    "model_runs",
    "customer_insights",
    "campaign_targets",
    "campaigns",
    "campaign_events",
    "bulk_targeting_runs",
    "bulk_targeting_candidates",
    "auth_events",
}


def test_migrations_create_complete_schema(tmp_path: Path) -> None:
    """빈 DB를 head까지 올리면 업무 테이블과 users.role이 모두 생성됩니다."""
    database_url = f"sqlite:///{tmp_path / 'fresh.sqlite3'}"

    revision = upgrade_database(database_url)

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert EXPECTED_TABLES <= set(inspector.get_table_names())
        assert "role" in {
            column["name"] for column in inspector.get_columns("users")
        }
        converted_column = next(
            column
            for column in inspector.get_columns("campaign_targets")
            if column["name"] == "converted"
        )
        assert converted_column["nullable"] is False
        customer_columns = {
            column["name"] for column in inspector.get_columns("customers")
        }
        assert {"marketing_opt_out", "last_contacted_at"} <= customer_columns
        target_columns = {
            column["name"]
            for column in inspector.get_columns("campaign_targets")
        }
        assert "bulk_targeting_run_id" in target_columns
        assert {
            "experiment_group",
            "contacted_at",
            "completed_at",
            "converted_at",
            "retained",
            "retention_checked_at",
            "outcome_revenue",
        } <= target_columns
        campaign_columns = {
            column["name"]
            for column in inspector.get_columns("campaigns")
        }
        assert {
            "segment_code",
            "experiment_enabled",
            "control_group_ratio",
            "experiment_seed",
            "experiment_assignment_version",
            "fixed_cost",
            "cost_per_contact",
            "revenue_per_conversion",
            "retention_window_days",
        } <= campaign_columns
        policy_columns = {
            column["name"]
            for column in inspector.get_columns("decision_policies")
        }
        assert "policy_json" in policy_columns
        bulk_run_columns = {
            column["name"]
            for column in inspector.get_columns("bulk_targeting_runs")
        }
        assert "scoring_batch_id" in bulk_run_columns
        scoring_batch_columns = {
            column["name"]
            for column in inspector.get_columns("scoring_batches")
        }
        assert {"reuse_key_sha256", "attempt_number"} <= scoring_batch_columns
        assert revision == "20260804_0011"
    finally:
        engine.dispose()


def test_duplicate_campaign_customers_fail_before_0009_schema_changes(
    tmp_path: Path,
) -> None:
    """중복 대상이 있으면 0009가 다른 컬럼을 추가하기 전에 안전하게 중단됩니다."""
    database_url = f"sqlite:///{tmp_path / 'duplicate-targets.sqlite3'}"
    config = build_alembic_config(database_url)
    command.upgrade(config, "20260801_0008")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO campaign_targets (
                        customer_id, customer_insight_id, campaign_id,
                        campaign_name, status, converted, experiment_group
                    ) VALUES
                        (1001, 2001, 3001, 'duplicate-test', 'pending', 0, 'treatment'),
                        (1001, 2002, 3001, 'duplicate-test', 'assigned', 0, 'treatment')
                    """
                )
            )

        with pytest.raises(RuntimeError, match="duplicate campaign_targets"):
            upgrade_database(database_url, bootstrap_existing=False)

        inspector = inspect(engine)
        policy_columns = {
            column["name"]
            for column in inspector.get_columns("decision_policies")
        }
        assert "policy_json" not in policy_columns
    finally:
        engine.dispose()


def test_existing_user_is_preserved_when_alembic_is_introduced(
    tmp_path: Path,
) -> None:
    """create_all 방식의 기존 users를 stamp한 뒤에도 회원 데이터가 유지됩니다."""
    database_url = f"sqlite:///{tmp_path / 'legacy.sqlite3'}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username VARCHAR(50) NOT NULL UNIQUE,
                        display_name VARCHAR(100) NOT NULL,
                        password_hash VARCHAR(255) NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT 1,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO users (username, display_name, password_hash)
                    VALUES ('existing_user', '기존 사용자', 'existing-hash')
                    """
                )
            )
    finally:
        engine.dispose()

    upgrade_database(database_url)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT username, display_name, role FROM users "
                    "WHERE username = 'existing_user'"
                )
            ).one()
        assert tuple(row) == ("existing_user", "기존 사용자", "operations")
    finally:
        engine.dispose()


def test_customer_csv_can_be_imported_idempotently(tmp_path: Path) -> None:
    """CLIENTNUM과 19개 특성이 적재되고 재실행 시 중복되지 않습니다."""
    project_root = Path(__file__).resolve().parents[2]
    rows = load_customer_rows(project_root / "data" / "raw" / "BankChurners.csv")
    assert len(rows) == 10_127
    assert len(rows[0]) == 20

    database_url = f"sqlite:///{tmp_path / 'import.sqlite3'}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            first_summary = upsert_customers(session, rows[:3])
            session.commit()

        changed_rows = [dict(row) for row in rows[:3]]
        changed_rows[0]["customer_age"] = 46
        with Session(engine) as session:
            second_summary = upsert_customers(session, changed_rows)
            session.commit()

        with Session(engine) as session:
            stored_customer = session.scalar(
                select(Customer).where(
                    Customer.customer_id == changed_rows[0]["customer_id"]
                )
            )
            total_customers = len(session.scalars(select(Customer)).all())

        assert first_summary.inserted == 3
        assert second_summary.inserted == 0
        assert second_summary.updated == 3
        assert total_customers == 3
        assert stored_customer is not None
        assert stored_customer.customer_age == 46
    finally:
        engine.dispose()


def test_analysis_and_campaign_records_preserve_model_lineage(
    tmp_path: Path,
) -> None:
    """통합 인사이트가 세 모델 실행과 캠페인 담당자를 올바르게 참조합니다."""
    project_root = Path(__file__).resolve().parents[2]
    customer_row = load_customer_rows(
        project_root / "data" / "raw" / "BankChurners.csv"
    )[0]
    database_url = f"sqlite:///{tmp_path / 'lineage.sqlite3'}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            customer = Customer(**customer_row)
            snapshot = CustomerFeatureSnapshot(
                customer_id=customer.customer_id,
                feature_sha256="d" * 64,
                as_of_date=date(2026, 8, 1),
                **{
                    field: customer_row[field]
                    for field in PREDICTION_FIELD_MAP
                },
            )
            assignee = User(
                username="operations_user",
                display_name="운영 담당자",
                password_hash="argon2-test-hash",
                role=UserRole.OPERATIONS.value,
            )
            policy = DecisionPolicy(
                version="activity-gap-v2",
                policy_sha256="e" * 64,
                medium_threshold=0.5,
                high_threshold=0.85,
                activity_gap_quantile=0.2,
            )
            session.add(policy)
            session.flush()
            batch = ScoringBatch(
                batch_key_sha256="f" * 64,
                reuse_key_sha256="f" * 64,
                as_of_date=date(2026, 8, 1),
                dataset_sha256="c" * 64,
                decision_policy_id=policy.id,
                status=ModelRunStatus.SUCCEEDED.value,
                processed_rows=1,
            )
            session.add(batch)
            session.flush()
            runs = [
                ModelRun(
                    task=task,
                    model_name=model_name,
                    model_version="test-v1",
                    artifact_path=f"outputs/models/{model_name}.joblib",
                    artifact_sha256=character * 64,
                    scoring_batch_id=batch.id,
                    decision_policy_sha256=policy.policy_sha256,
                    status=ModelRunStatus.SUCCEEDED.value,
                    processed_rows=1,
                )
                for task, model_name, character in [
                    ("classification", "xgboost", "a"),
                    ("regression", "voting", "b"),
                    ("clustering", "activity-gap-gmm", "c"),
                ]
            ]
            session.add_all([customer, snapshot, assignee, *runs])
            session.flush()

            insight = CustomerInsight(
                customer_id=customer.customer_id,
                customer_snapshot_id=snapshot.id,
                scoring_batch_id=batch.id,
                as_of_date=batch.as_of_date,
                classification_run_id=runs[0].id,
                regression_run_id=runs[1].id,
                clustering_run_id=runs[2].id,
                churn_probability=0.91,
                risk_level=RiskLevel.HIGH.value,
                expected_transaction_count=70.5,
                activity_gap=-28.5,
                cluster_name="우선케어(거래 감소)",
                cluster_confidence=0.87,
                recommended_action="리텐션 우선 상담",
                reason_codes=["low_transaction_count", "long_inactivity"],
            )
            session.add(insight)
            session.flush()

            target = CampaignTarget(
                customer_id=customer.customer_id,
                customer_insight_id=insight.id,
                campaign_name="이탈 위험 리텐션",
                assigned_to_user_id=assignee.id,
                status=CampaignStatus.ASSIGNED.value,
            )
            session.add(target)
            session.commit()

        with Session(engine) as session:
            stored_target = session.scalar(select(CampaignTarget))
            assert stored_target is not None
            assert stored_target.customer_insight.risk_level == RiskLevel.HIGH.value
            assert stored_target.customer_insight.customer_snapshot is not None
            assert stored_target.customer_insight.customer_snapshot.feature_sha256 == (
                "d" * 64
            )
            assert stored_target.customer_insight.scoring_batch is not None
            assert stored_target.customer_insight.scoring_batch.decision_policy.version == (
                "activity-gap-v2"
            )
            assert stored_target.customer_insight.classification_run.model_name == "xgboost"
            assert stored_target.customer_insight.regression_run.model_name == "voting"
            assert (
                stored_target.customer_insight.clustering_run.model_name
                == "activity-gap-gmm"
            )
            assert stored_target.assignee is not None
            assert stored_target.assignee.role == UserRole.OPERATIONS.value
    finally:
        engine.dispose()
