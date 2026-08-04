"""원본 CSV의 고객 ID와 19개 특성을 customers 테이블에 적재합니다."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .models import (
    BulkTargetingCandidateSnapshot,
    CampaignEvent,
    CampaignTarget,
    Customer,
    CustomerFeatureSnapshot,
    CustomerInsight,
)


RAW_FIELD_MAP = {
    "CLIENTNUM": "customer_id",
    "Customer_Age": "customer_age",
    "Gender": "gender",
    "Dependent_count": "dependent_count",
    "Education_Level": "education_level",
    "Marital_Status": "marital_status",
    "Income_Category": "income_category",
    "Card_Category": "card_category",
    "Months_on_book": "months_on_book",
    "Total_Relationship_Count": "total_relationship_count",
    "Months_Inactive_12_mon": "months_inactive_12_mon",
    "Contacts_Count_12_mon": "contacts_count_12_mon",
    "Credit_Limit": "credit_limit",
    "Total_Revolving_Bal": "total_revolving_bal",
    "Avg_Open_To_Buy": "avg_open_to_buy",
    "Total_Amt_Chng_Q4_Q1": "total_amt_chng_q4_q1",
    "Total_Trans_Amt": "total_trans_amt",
    "Total_Trans_Ct": "total_trans_ct",
    "Total_Ct_Chng_Q4_Q1": "total_ct_chng_q4_q1",
    "Avg_Utilization_Ratio": "avg_utilization_ratio",
}

INTEGER_ATTRIBUTES = {
    "customer_id",
    "customer_age",
    "dependent_count",
    "months_on_book",
    "total_relationship_count",
    "months_inactive_12_mon",
    "contacts_count_12_mon",
    "total_revolving_bal",
    "total_trans_amt",
    "total_trans_ct",
}
FLOAT_ATTRIBUTES = {
    "credit_limit",
    "avg_open_to_buy",
    "total_amt_chng_q4_q1",
    "total_ct_chng_q4_q1",
    "avg_utilization_ratio",
}
UPSERT_ATTRIBUTES = tuple(
    attribute for attribute in RAW_FIELD_MAP.values() if attribute != "customer_id"
)


@dataclass(frozen=True)
class CustomerImportSummary:
    """고객 적재 결과입니다."""

    processed: int
    inserted: int
    updated: int
    total_customers: int


def _convert_value(attribute: str, raw_value: str, row_number: int) -> Any:
    value = raw_value.strip()
    if value == "":
        raise ValueError(f"Row {row_number}: {attribute} is empty.")

    try:
        if attribute in INTEGER_ATTRIBUTES:
            return int(value)
        if attribute in FLOAT_ATTRIBUTES:
            return float(value)
    except ValueError as exc:
        raise ValueError(
            f"Row {row_number}: {attribute} has an invalid numeric value."
        ) from exc
    return value


def load_customer_rows(data_path: Path) -> list[dict[str, Any]]:
    """BankChurners 원본 CSV를 고객 테이블 레코드로 변환합니다."""
    with data_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        headers = set(reader.fieldnames or [])
        missing_headers = sorted(set(RAW_FIELD_MAP) - headers)
        if missing_headers:
            names = ", ".join(missing_headers)
            raise ValueError(f"Customer CSV is missing required columns: {names}.")

        rows: list[dict[str, Any]] = []
        customer_ids: set[int] = set()
        for row_number, source_row in enumerate(reader, start=2):
            record = {
                attribute: _convert_value(
                    attribute,
                    source_row[source_field],
                    row_number,
                )
                for source_field, attribute in RAW_FIELD_MAP.items()
            }
            customer_id = int(record["customer_id"])
            if customer_id in customer_ids:
                raise ValueError(
                    f"Row {row_number}: duplicate CLIENTNUM {customer_id}."
                )
            customer_ids.add(customer_id)
            rows.append(record)

    if not rows:
        raise ValueError("Customer CSV does not contain any data rows.")
    return rows


def _chunks(
    rows: list[dict[str, Any]],
    chunk_size: int,
) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), chunk_size):
        yield rows[start : start + chunk_size]


def _execute_upsert(session: Session, rows: list[dict[str, Any]]) -> None:
    table = Customer.__table__
    dialect_name = session.get_bind().dialect.name

    if dialect_name == "mysql":
        statement = mysql_insert(table).values(rows)
        update_values = {
            attribute: getattr(statement.inserted, attribute)
            for attribute in UPSERT_ATTRIBUTES
        }
        update_values["updated_at"] = func.now()
        session.execute(statement.on_duplicate_key_update(**update_values))
        return

    if dialect_name == "sqlite":
        statement = sqlite_insert(table).values(rows)
        update_values = {
            attribute: getattr(statement.excluded, attribute)
            for attribute in UPSERT_ATTRIBUTES
        }
        update_values["updated_at"] = func.now()
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.customer_id],
                set_=update_values,
            )
        )
        return

    for row in rows:
        session.merge(Customer(**row))


def delete_all_customers(session: Session) -> None:
    """customers와 FK로 연결된 모든 데이터를 삭제합니다(운영 데이터 전체 교체용).

    customer_insights/campaign_targets/bulk_targeting_candidates 등은 일부가
    RESTRICT 관계로 얽혀 있어, DB의 FK cascade 설정과 무관하게 안전하도록
    자식 → 부모 순서로 명시적으로 지웁니다.
    """
    session.execute(delete(BulkTargetingCandidateSnapshot))
    session.execute(delete(CampaignEvent).where(CampaignEvent.campaign_target_id.is_not(None)))
    session.execute(delete(CampaignTarget))
    session.execute(delete(CustomerInsight))
    session.execute(delete(CustomerFeatureSnapshot))
    session.execute(delete(Customer))


def upsert_customers(
    session: Session,
    rows: list[dict[str, Any]],
    *,
    chunk_size: int = 500,
) -> CustomerImportSummary:
    """고객을 ID 기준으로 추가·갱신하며 여러 번 실행해도 중복시키지 않습니다."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero.")

    before_count = int(
        session.scalar(select(func.count()).select_from(Customer)) or 0
    )
    for batch in _chunks(rows, chunk_size):
        _execute_upsert(session, batch)
    session.flush()

    total_count = int(
        session.scalar(select(func.count()).select_from(Customer)) or 0
    )
    inserted = max(total_count - before_count, 0)
    return CustomerImportSummary(
        processed=len(rows),
        inserted=inserted,
        updated=len(rows) - inserted,
        total_customers=total_count,
    )

