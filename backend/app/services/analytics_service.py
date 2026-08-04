"""
EDA 원천 데이터셋 기반 분석팀용 통계를 계산합니다.

"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT


DATASET_PATH = PROJECT_ROOT / "data" / "processed" / "bankchurners_clean.csv"

CATEGORICAL_FIELDS = (
    "Gender",
    "Card_Category",
    "Income_Category",
    "Education_Level",
    "Marital_Status",
)
NUMERIC_DISTRIBUTION_FIELDS = (
    "Total_Trans_Ct",
    "Total_Trans_Amt",
    "Avg_Utilization_Ratio",
    "Months_Inactive_12_mon",
    "Contacts_Count_12_mon",
    "Total_Relationship_Count",
)


@lru_cache(maxsize=1)
def _load_dataset(mtime_ns: int) -> pd.DataFrame:
    """수정 시각을 캐시 키로 써서, 파일이 바뀌면 다음 호출에서 자동으로 재로딩합니다."""
    del mtime_ns  # 캐시 키 용도로만 사용합니다.
    return pd.read_csv(DATASET_PATH)


def _dataset() -> pd.DataFrame:
    if not DATASET_PATH.is_file():
        raise FileNotFoundError(f"EDA dataset not found: {DATASET_PATH}")
    return _load_dataset(DATASET_PATH.stat().st_mtime_ns)


def categorical_churn_rate(field: str) -> list[dict[str, object]]:
    """범주형 변수 그룹별 이탈률(%)과 표본 수를 이탈률 내림차순으로 반환합니다."""
    if field not in CATEGORICAL_FIELDS:
        raise ValueError(f"Unsupported categorical field: {field}")
    df = _dataset()
    grouped = (
        df.groupby(field)["Target"]
        .agg(["mean", "count"])
        .sort_values("mean", ascending=False)
    )
    return [
        {
            "group": str(index),
            "churn_rate": float(row["mean"]),
            "count": int(row["count"]),
        }
        for index, row in grouped.iterrows()
    ]


def numeric_distribution(field: str) -> dict[str, dict[str, float]]:
    """이탈 여부(0/1)별 수치형 변수의 사분위 요약(박스플롯 입력)을 반환합니다."""
    if field not in NUMERIC_DISTRIBUTION_FIELDS:
        raise ValueError(f"Unsupported numeric field: {field}")
    df = _dataset()
    result: dict[str, dict[str, float]] = {}
    for target_value, group in df.groupby("Target")[field]:
        quartiles = group.quantile([0.0, 0.25, 0.5, 0.75, 1.0])
        result[str(int(target_value))] = {
            "min": float(quartiles.loc[0.0]),
            "q1": float(quartiles.loc[0.25]),
            "median": float(quartiles.loc[0.5]),
            "q3": float(quartiles.loc[0.75]),
            "max": float(quartiles.loc[1.0]),
            "count": int(group.count()),
        }
    return result


def feature_correlation() -> list[dict[str, object]]:
    """수치형 변수와 이탈(Target)의 상관계수를 절댓값 내림차순으로 반환합니다."""
    df = _dataset()
    corr = df.select_dtypes(include="number").corr()["Target"].drop("Target")
    ordered = corr.reindex(corr.abs().sort_values(ascending=False).index)
    return [
        {"feature": str(feature), "correlation": float(value)}
        for feature, value in ordered.items()
        if np.isfinite(value)
    ]
