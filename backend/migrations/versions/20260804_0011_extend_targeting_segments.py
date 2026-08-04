"""Allow the four balance-based targeting segments.

bulk_targeting_runs.segment_code에는 허용 세그먼트를 고정한 CHECK 제약이 있어,
신규 세그먼트로 미리보기를 만들면 저장이 거부됩니다. enum 확장에 맞춰 제약을
다시 만듭니다.

Revision ID: 20260804_0011
Revises: 20260802_0010
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260804_0011"
down_revision: str | None = "20260802_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONSTRAINT_NAME = "ck_bulk_targeting_runs_segment"
TABLE_NAME = "bulk_targeting_runs"

LEGACY_SEGMENTS = (
    "high_risk_retention",
    "medium_reactivation",
    "low_risk_upsell",
)
CURRENT_SEGMENTS = LEGACY_SEGMENTS + (
    "small_balance_decline",
    "dormant_full_payer",
    "active_full_payer",
    "stable_prime",
)


def _condition(segments: tuple[str, ...]) -> str:
    values = ", ".join(f"'{segment}'" for segment in segments)
    return f"segment_code IN ({values})"


def _replace_constraint(segments: tuple[str, ...]) -> None:
    """CHECK 제약을 새 목록으로 교체합니다.

    SQLite는 제약을 직접 바꿀 수 없어 batch_alter_table로 테이블을 재생성합니다.
    """
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(TABLE_NAME) as batch_op:
            batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
            batch_op.create_check_constraint(CONSTRAINT_NAME, _condition(segments))
        return

    op.drop_constraint(CONSTRAINT_NAME, TABLE_NAME, type_="check")
    op.create_check_constraint(CONSTRAINT_NAME, TABLE_NAME, _condition(segments))


def upgrade() -> None:
    """잔액 기반 세그먼트 4종을 허용 목록에 추가합니다."""
    _replace_constraint(CURRENT_SEGMENTS)


def downgrade() -> None:
    """기존 3종만 허용하도록 되돌립니다."""
    op.execute(
        f"DELETE FROM {TABLE_NAME} WHERE segment_code NOT IN "
        f"({', '.join(chr(39) + s + chr(39) for s in LEGACY_SEGMENTS)})"
    )
    _replace_constraint(LEGACY_SEGMENTS)
