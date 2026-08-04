"""세그먼트 일괄 타기팅의 제외 규칙과 실행 생명주기를 검증합니다."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.enums import BulkTargetingSegment, UserRole
from backend.app.migration_runner import upgrade_database
from backend.app.models import (
    BulkTargetingCandidateSnapshot,
    Campaign,
    CampaignTarget,
    Customer,
    CustomerInsight,
    DecisionPolicy,
    ModelRun,
    ScoringBatch,
    User,
)
from backend.app.schemas import BulkTargetingPreviewRequest
from backend.app.services.bulk_targeting_service import (
    cancel_bulk_targeting,
    execute_bulk_targeting,
    preview_bulk_targeting,
    rerun_bulk_targeting,
)


def _customer(customer_id: int, *, opt_out: bool = False) -> Customer:
    return Customer(
        customer_id=customer_id,
        customer_age=40,
        gender="F",
        dependent_count=1,
        education_level="Graduate",
        marital_status="Married",
        income_category="$40K - $60K",
        card_category="Blue",
        months_on_book=20,
        total_relationship_count=2,
        months_inactive_12_mon=1,
        contacts_count_12_mon=1,
        credit_limit=1000,
        total_revolving_bal=100,
        avg_open_to_buy=900,
        total_amt_chng_q4_q1=1,
        total_trans_amt=100,
        total_trans_ct=10,
        total_ct_chng_q4_q1=1,
        avg_utilization_ratio=0.1,
        marketing_opt_out=opt_out,
    )


def _seed_database(session: Session) -> tuple[User, list[CustomerInsight]]:
    actor = User(
        username="bulk_admin",
        display_name="일괄 타기팅 관리자",
        password_hash="test-hash",
        role=UserRole.ADMIN.value,
        is_active=True,
    )
    session.add(actor)
    customers = [_customer(1), _customer(2, opt_out=True), _customer(3)]
    session.add_all(customers)
    policy = DecisionPolicy(
        version="bulk-test",
        policy_sha256="a" * 64,
        medium_threshold=0.5,
        high_threshold=0.8,
        activity_gap_quantile=0.2,
    )
    session.add(policy)
    session.flush()
    batch = ScoringBatch(
        batch_key_sha256="b" * 64,
        reuse_key_sha256="b" * 64,
        as_of_date=date(2026, 8, 1),
        decision_policy_id=policy.id,
        status="succeeded",
    )
    session.add(batch)
    session.flush()
    runs = [
        ModelRun(
            task=task,
            model_name=task,
            model_version="test-v1",
            artifact_path="test.joblib",
            artifact_sha256=character * 64,
            status="succeeded",
        )
        for task, character in (
            ("classification", "c"),
            ("regression", "d"),
            ("clustering", "e"),
        )
    ]
    session.add_all(runs)
    session.flush()
    insights = [
        CustomerInsight(
            customer_id=1,
            scoring_batch_id=batch.id,
            as_of_date=batch.as_of_date,
            classification_run_id=runs[0].id,
            regression_run_id=runs[1].id,
            clustering_run_id=runs[2].id,
            churn_probability=0.95,
            risk_level="high",
            expected_transaction_count=20,
            activity_gap=-10,
            cluster_name="우선케어(거래 감소)",
            recommended_action="리텐션 우선 상담",
        ),
        CustomerInsight(
            customer_id=2,
            scoring_batch_id=batch.id,
            as_of_date=batch.as_of_date,
            classification_run_id=runs[0].id,
            regression_run_id=runs[1].id,
            clustering_run_id=runs[2].id,
            churn_probability=0.94,
            risk_level="high",
            expected_transaction_count=20,
            activity_gap=-9,
            cluster_name="우선케어(거래 감소)",
            recommended_action="리텐션 우선 상담",
        ),
        CustomerInsight(
            customer_id=3,
            scoring_batch_id=batch.id,
            as_of_date=batch.as_of_date,
            classification_run_id=runs[0].id,
            regression_run_id=runs[1].id,
            clustering_run_id=runs[2].id,
            churn_probability=0.93,
            risk_level="high",
            expected_transaction_count=20,
            activity_gap=-8,
            cluster_name="우선케어(거래 감소)",
            recommended_action="리텐션 우선 상담",
        ),
    ]
    session.add_all(insights)
    session.flush()
    active_campaign = Campaign(
        name="기존 활성 캠페인",
        status="active",
        created_by_user_id=actor.id,
    )
    session.add(active_campaign)
    session.flush()
    session.add(
        CampaignTarget(
            customer_id=3,
            customer_insight_id=insights[2].id,
            campaign_id=active_campaign.id,
            campaign_name=active_campaign.name,
            status="pending",
        )
    )
    session.commit()
    return actor, insights


def test_bulk_targeting_preview_execute_cancel_and_rerun(tmp_path: Path) -> None:
    """수신 거부·활성 캠페인을 제외하고 실행·취소·재실행을 추적합니다."""
    database_url = f"sqlite:///{tmp_path / 'bulk-targeting.sqlite3'}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            actor, insights = _seed_database(session)
            run, scan = preview_bulk_targeting(
                session,
                payload=BulkTargetingPreviewRequest(
                    segment=BulkTargetingSegment.HIGH_RISK_RETENTION,
                    campaign_name="고위험 일괄 리텐션",
                    experiment_enabled=True,
                    control_group_ratio=0.5,
                ),
                actor=actor,
            )

            assert run.eligible_count == 1
            assert run.preview_count == 1
            assert run.skipped_opt_out_count == 1
            assert run.skipped_active_campaign_count == 1
            assert [item.insight.id for item in scan.candidates] == [insights[0].id]
            assert run.scoring_batch_id == insights[0].scoring_batch_id
            snapshots = session.scalars(
                select(BulkTargetingCandidateSnapshot)
                .where(BulkTargetingCandidateSnapshot.run_id == run.id)
                .order_by(BulkTargetingCandidateSnapshot.rank)
            ).all()
            assert len(snapshots) == 3
            assert [snapshot.customer_id for snapshot in snapshots if snapshot.selected] == [1]

            executed = execute_bulk_targeting(session, run=run, actor=actor)
            assert executed.created_count == 1
            assert execute_bulk_targeting(session, run=executed, actor=actor).campaign_id == executed.campaign_id
            target = session.scalar(
                select(CampaignTarget).where(
                    CampaignTarget.bulk_targeting_run_id == executed.id
                )
            )
            assert target is not None

            cancelled = cancel_bulk_targeting(session, run=executed, actor=actor)
            assert cancelled.cancelled_target_count == 1
            assert target.status == "cancelled"

            rerun, rerun_scan = rerun_bulk_targeting(
                session,
                run=cancelled,
                actor=actor,
            )
            assert rerun.rerun_of_id == cancelled.id
            assert rerun.status == "previewed"
            assert rerun.scoring_batch_id == cancelled.scoring_batch_id
            assert rerun.rules_json["experiment_seed"] == cancelled.rules_json["experiment_seed"]
            assert len(rerun_scan.candidates) == 1
            rerun_executed = execute_bulk_targeting(session, run=rerun, actor=actor)
            rerun_target = session.scalar(
                select(CampaignTarget).where(
                    CampaignTarget.bulk_targeting_run_id == rerun_executed.id
                )
            )
            assert rerun_target is not None
            assert rerun_target.experiment_group == target.experiment_group
    finally:
        engine.dispose()


def test_every_segment_is_fully_configured() -> None:
    """세그먼트를 추가할 때 설정 dict를 빠뜨리면 런타임에 KeyError가 납니다.

    이름·설명·우선순위는 서로 다른 dict 3개에 흩어져 있고, 우선순위는 일괄
    타기팅과 개별 등록 서비스에 각각 존재합니다. 하나라도 누락되면 미리보기나
    중복 접촉 방지가 조용히 어긋나므로 여기서 한 번에 고정합니다.
    """
    from backend.app.services.bulk_targeting_service import (
        DEFAULT_CAMPAIGN_DESCRIPTIONS,
        DEFAULT_CAMPAIGN_NAMES,
        SEGMENT_TARGETING_PRIORITIES,
    )
    from backend.app.services.campaign_service import SEGMENT_PRIORITIES

    segments = {segment.value for segment in BulkTargetingSegment}

    assert segments <= set(DEFAULT_CAMPAIGN_NAMES)
    assert segments <= set(DEFAULT_CAMPAIGN_DESCRIPTIONS)
    assert segments <= set(SEGMENT_TARGETING_PRIORITIES)
    assert segments <= set(SEGMENT_PRIORITIES)

    # 두 우선순위 맵이 어긋나면 "일괄 타기팅은 제외했는데 개별 등록은 허용"
    # 같은 모순이 생깁니다.
    for segment in segments:
        assert SEGMENT_TARGETING_PRIORITIES[segment] == SEGMENT_PRIORITIES[segment], (
            f"{segment}의 우선순위가 일괄 타기팅과 개별 등록에서 다릅니다."
        )


def test_segment_check_constraint_allows_every_enum_value(tmp_path: Path) -> None:
    """DB의 CHECK 제약이 enum과 어긋나면 미리보기 저장이 실패합니다.

    세그먼트를 코드에만 추가하고 마이그레이션을 빠뜨리면 화면에서는 선택되는데
    저장 시점에 500이 납니다(실제로 발생했던 문제). 모든 enum 값이 DB에
    들어갈 수 있는지 직접 확인합니다.
    """
    from backend.app.models import BulkTargetingRun

    database_url = f"sqlite:///{tmp_path / 'segments.sqlite3'}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            for segment in BulkTargetingSegment:
                session.add(
                    BulkTargetingRun(
                        segment_code=segment.value,
                        status="previewed",
                        rules_json={"segment": segment.value},
                        preview_count=0,
                        eligible_count=0,
                        created_count=0,
                        skipped_active_campaign_count=0,
                        skipped_recent_contact_count=0,
                        skipped_opt_out_count=0,
                        cancelled_target_count=0,
                    )
                )
            session.commit()

            stored = set(session.scalars(select(BulkTargetingRun.segment_code)).all())
            assert stored == {segment.value for segment in BulkTargetingSegment}
    finally:
        engine.dispose()
