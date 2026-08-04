"""위험도(낮음/주의/높음)가 목표 비율(기본 20/60/20)로 나오는 합성 고객
2,000명을 생성해 BankChurners.csv와 같은 형식의 CSV 파일로 저장합니다.

이탈확률·위험도 값을 직접 지정하지 않습니다. 실제 이탈/유지 고객 그룹의 평균값
방향으로 위험군별 프로필을 만들어 모델 입력 19개 원본 피처를 합성하고, 기존
분류 모델(ModelRegistry)이 실제로 계산한 이탈확률로 버킷을 나눠 목표 비율만큼
선택합니다.

이 스크립트는 DB를 건드리지 않습니다. 생성된 CSV는 기존
`backend.scripts.import_customers --replace`로 적재합니다(customers와
연관 데이터를 모두 지우고 교체).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backend.app.config import get_model_dir
from backend.app.model_registry import ModelRegistry
from backend.app.schemas import PREDICTION_FIELD_MAP

RISK_LEVEL_ORDER = ("low", "medium", "high")
# 원본 BankChurners.csv와 나란히 저장소의 data/ 밑에 남깁니다. compose.yaml이
# ./data/synthetic만 쓰기 가능하게 덮어써 두었으므로 컨테이너에서 실행해도
# 결과 CSV가 호스트 저장소에 그대로 남습니다(컨테이너를 다시 만들어도 유지).
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "synthetic" / "synthetic_customers.csv"
)

# 모델(`PredictionRequest`, backend/app/schemas.py)이 허용하는 유효 범위입니다.
FEATURE_BOUNDS: dict[str, tuple[float, float]] = {
    "Customer_Age": (26, 73),
    "Dependent_count": (0, 5),
    "Months_on_book": (13, 56),
    "Total_Relationship_Count": (1, 6),
    "Months_Inactive_12_mon": (0, 6),
    "Contacts_Count_12_mon": (0, 6),
    "Credit_Limit": (1438.3, 34516.0),
    "Total_Revolving_Bal": (0, 2517),
    "Avg_Open_To_Buy": (3.0, 34516.0),
    "Total_Amt_Chng_Q4_Q1": (0.0, 3.397),
    "Total_Trans_Amt": (510, 18484),
    "Total_Trans_Ct": (10, 139),
    "Total_Ct_Chng_Q4_Q1": (0.0, 3.714),
    "Avg_Utilization_Ratio": (0.0, 0.999),
}

INTEGER_FEATURES = {
    "Customer_Age",
    "Dependent_count",
    "Months_on_book",
    "Total_Relationship_Count",
    "Months_Inactive_12_mon",
    "Contacts_Count_12_mon",
    "Total_Revolving_Bal",
    "Total_Trans_Amt",
    "Total_Trans_Ct",
}

# Avg_Open_To_Buy·Total_Trans_Amt는 Credit_Limit/Total_Revolving_Bal/Total_Trans_Ct에서
# 파생시키므로 위험군 앵커가 필요한 건 나머지 12개 numeric 피처뿐입니다.
RISK_ANCHORED_FEATURES = (
    "Customer_Age",
    "Dependent_count",
    "Months_on_book",
    "Total_Relationship_Count",
    "Months_Inactive_12_mon",
    "Contacts_Count_12_mon",
    "Credit_Limit",
    "Total_Revolving_Bal",
    "Total_Amt_Chng_Q4_Q1",
    "Total_Trans_Ct",
    "Total_Ct_Chng_Q4_Q1",
    "Avg_Utilization_Ratio",
)

# 실제 BankChurners 데이터의 유지(Existing)/이탈(Attrited) 그룹 평균·중앙값 방향으로
# 잡은 위험군 앵커입니다. 상관성이 약한 피처(나이·부양가족수·근속월수)는 위험군과
# 무관하게 동일한 값을 앵커로 둡니다.
LOW_RISK_MEANS: dict[str, float] = {
    "Customer_Age": 46,
    "Dependent_count": 2.3,
    "Months_on_book": 36,
    "Total_Relationship_Count": 3.9,
    "Months_Inactive_12_mon": 2.3,
    "Contacts_Count_12_mon": 2.3,
    "Credit_Limit": 8700.0,
    "Total_Revolving_Bal": 1300,
    "Total_Amt_Chng_Q4_Q1": 0.77,
    "Total_Trans_Ct": 75,
    "Total_Ct_Chng_Q4_Q1": 0.74,
    "Avg_Utilization_Ratio": 0.28,
}
HIGH_RISK_MEANS: dict[str, float] = {
    "Customer_Age": 46,
    "Dependent_count": 2.3,
    "Months_on_book": 36,
    "Total_Relationship_Count": 3.0,
    "Months_Inactive_12_mon": 2.9,
    "Contacts_Count_12_mon": 3.3,
    "Credit_Limit": 8100.0,
    "Total_Revolving_Bal": 80,
    "Total_Amt_Chng_Q4_Q1": 0.62,
    "Total_Trans_Ct": 40,
    "Total_Ct_Chng_Q4_Q1": 0.48,
    "Avg_Utilization_Ratio": 0.03,
}
# 범위 대비 표준편차 비율입니다. 프로필 내부에도 자연스러운 다양성을 줍니다.
STD_FRACTIONS: dict[str, float] = {
    "Customer_Age": 0.18,
    "Dependent_count": 0.35,
    "Months_on_book": 0.22,
    "Total_Relationship_Count": 0.28,
    "Months_Inactive_12_mon": 0.35,
    "Contacts_Count_12_mon": 0.35,
    "Credit_Limit": 0.28,
    "Total_Revolving_Bal": 0.35,
    "Total_Amt_Chng_Q4_Q1": 0.3,
    "Total_Trans_Ct": 0.22,
    "Total_Ct_Chng_Q4_Q1": 0.3,
    "Avg_Utilization_Ratio": 0.35,
}

# 범주형 피처는 위험도와 상관성이 약해 실제 데이터 전체 비율로 동일하게 샘플링합니다.
CATEGORICAL_CHOICES: dict[str, tuple[tuple[str, float], ...]] = {
    "Gender": (("F", 0.529), ("M", 0.471)),
    "Education_Level": (
        ("Graduate", 0.309),
        ("High School", 0.199),
        ("Unknown", 0.150),
        ("Uneducated", 0.147),
        ("College", 0.100),
        ("Post-Graduate", 0.051),
        ("Doctorate", 0.044),
    ),
    "Marital_Status": (
        ("Married", 0.463),
        ("Single", 0.389),
        ("Unknown", 0.074),
        ("Divorced", 0.074),
    ),
    "Income_Category": (
        ("Less than $40K", 0.351),
        ("$40K - $60K", 0.176),
        ("$80K - $120K", 0.148),
        ("$60K - $80K", 0.138),
        ("$120K +", 0.072),
        ("Unknown", 0.115),
    ),
    "Card_Category": (
        ("Blue", 0.932),
        ("Silver", 0.055),
        ("Gold", 0.011),
        ("Platinum", 0.002),
    ),
}


@dataclass
class RiskProfile:
    """위험군별 샘플링 프로필입니다. extremity는 재시도 시 앵커에서 더 밀어냅니다."""

    name: str
    extremity: float = 1.0


def _profile_mean(feature: str, profile: RiskProfile) -> float:
    low = LOW_RISK_MEANS[feature]
    high = HIGH_RISK_MEANS[feature]
    if profile.name == "medium":
        return (low + high) / 2
    anchor, other = (high, low) if profile.name == "high" else (low, high)
    return anchor + (anchor - other) * (profile.extremity - 1.0)


def _sample_numeric(
    feature: str, profile: RiskProfile, size: int, rng: np.random.Generator
) -> np.ndarray:
    lower, upper = FEATURE_BOUNDS[feature]
    mean = _profile_mean(feature, profile)
    std = (upper - lower) * STD_FRACTIONS[feature]
    values = np.clip(rng.normal(mean, std, size=size), lower, upper)
    return np.round(values) if feature in INTEGER_FEATURES else values


def _sample_categorical(feature: str, size: int, rng: np.random.Generator) -> np.ndarray:
    choices = CATEGORICAL_CHOICES[feature]
    labels = [label for label, _ in choices]
    weights = np.array([weight for _, weight in choices])
    return rng.choice(labels, size=size, p=weights / weights.sum())


def generate_candidates(profile: RiskProfile, size: int, rng: np.random.Generator) -> pd.DataFrame:
    """하나의 위험군 프로필에서 원본 19개 피처 후보를 생성합니다."""
    data: dict[str, np.ndarray] = {}
    for feature in RISK_ANCHORED_FEATURES:
        data[feature] = _sample_numeric(feature, profile, size, rng)
    for feature in CATEGORICAL_CHOICES:
        data[feature] = _sample_categorical(feature, size, rng)

    avg_txn_value = np.clip(rng.normal(65, 15, size=size), 25, 140)
    total_trans_amt = np.round(data["Total_Trans_Ct"] * avg_txn_value)
    data["Total_Trans_Amt"] = np.clip(total_trans_amt, *FEATURE_BOUNDS["Total_Trans_Amt"])

    avg_open_to_buy = data["Credit_Limit"] - data["Total_Revolving_Bal"]
    data["Avg_Open_To_Buy"] = np.clip(avg_open_to_buy, *FEATURE_BOUNDS["Avg_Open_To_Buy"])

    frame = pd.DataFrame(data)
    return frame[list(PREDICTION_FIELD_MAP.values())]


def _bucket(probability: float, medium_threshold: float, high_threshold: float) -> str:
    if probability >= high_threshold:
        return "high"
    if probability >= medium_threshold:
        return "medium"
    return "low"


def build_synthetic_customers(
    registry: ModelRegistry,
    *,
    count: int,
    low_share: float,
    high_share: float,
    medium_threshold: float,
    high_threshold: float,
    oversample_factor: int,
    seed: int,
    max_attempts: int = 3,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """오버샘플 → 실제 모델 채점 → 버킷팅 → 목표 비율만큼 선택합니다."""
    rng = np.random.default_rng(seed)
    target_counts = {
        "low": round(count * low_share),
        "high": round(count * high_share),
    }
    target_counts["medium"] = count - target_counts["low"] - target_counts["high"]

    profiles = {level: RiskProfile(level) for level in RISK_LEVEL_ORDER}
    selected: dict[str, pd.DataFrame] = {level: pd.DataFrame() for level in RISK_LEVEL_ORDER}
    # 목표 버킷을 채우지 못했을 때 총량을 맞추는 데 쓰는 미선택 후보입니다.
    leftovers: list[pd.DataFrame] = []

    for _attempt in range(1, max_attempts + 1):
        pools = []
        for level, profile in profiles.items():
            needed = target_counts[level] - len(selected[level])
            if needed <= 0:
                continue
            # medium은 목표 비중이 커서 후보 풀도 더 넉넉하게 준비합니다.
            pool_size = needed * oversample_factor * (2 if level == "medium" else 1)
            pools.append(generate_candidates(profile, pool_size, rng))
        if not pools:
            break

        pool = pd.concat(pools, ignore_index=True)
        scored = registry.predict_batch(pool)
        pool = pool.assign(churn_probability=scored["churn_probability"].to_numpy())
        pool["risk_level"] = [
            _bucket(probability, medium_threshold, high_threshold)
            for probability in pool["churn_probability"]
        ]

        taken_index: set[int] = set()
        for level in RISK_LEVEL_ORDER:
            needed = target_counts[level] - len(selected[level])
            if needed <= 0:
                continue
            available = pool[pool["risk_level"] == level]
            take_n = min(needed, len(available))
            if take_n == 0:
                continue
            seed_for_sample = int(rng.integers(0, 2**32 - 1))
            take = available.sample(n=take_n, random_state=seed_for_sample)
            taken_index.update(take.index.tolist())
            selected[level] = pd.concat([selected[level], take], ignore_index=True)
        leftovers.append(pool.drop(index=list(taken_index)))

        if all(len(selected[level]) >= target_counts[level] for level in RISK_LEVEL_ORDER):
            break
        for level, profile in profiles.items():
            if len(selected[level]) < target_counts[level]:
                profile.extremity += 0.5

    band_midpoints = {
        "low": medium_threshold / 2,
        "medium": (medium_threshold + high_threshold) / 2,
        "high": (high_threshold + 1.0) / 2,
    }
    fillers: list[pd.DataFrame] = []
    leftover_pool = (
        pd.concat(leftovers, ignore_index=True) if leftovers else pd.DataFrame()
    )
    for level in RISK_LEVEL_ORDER:
        shortfall = target_counts[level] - len(selected[level])
        if shortfall <= 0:
            continue
        print(
            f"[WARN] '{level}' 위험군 목표({target_counts[level]}건) 중 "
            f"{shortfall}건을 채우지 못했습니다."
        )
        # 요청한 총량(--count)은 맞춰야 하므로, 해당 구간에 가장 가까운
        # 미선택 후보로 채웁니다. 위험도는 실제 채점값을 그대로 유지하므로
        # 아래 분포 출력은 부풀려지지 않습니다.
        if leftover_pool.empty:
            continue
        distance = (leftover_pool["churn_probability"] - band_midpoints[level]).abs()
        take = leftover_pool.loc[distance.nsmallest(shortfall).index]
        fillers.append(take)
        leftover_pool = leftover_pool.drop(index=take.index)
        print(f"       총량을 맞추기 위해 가장 가까운 후보 {len(take)}건으로 채웠습니다.")

    final = pd.concat(
        [selected[level] for level in RISK_LEVEL_ORDER] + fillers, ignore_index=True
    )
    shuffle_seed = int(rng.integers(0, 2**32 - 1))
    final = final.sample(frac=1, random_state=shuffle_seed).reset_index(drop=True)
    return final, target_counts


def to_bankchurners_frame(dataset: pd.DataFrame, *, start_id: int) -> pd.DataFrame:
    """채점·선택이 끝난 데이터셋을 BankChurners.csv와 같은 헤더로 변환합니다."""
    frame = dataset[list(PREDICTION_FIELD_MAP.values())].copy()
    for column in INTEGER_FEATURES:
        frame[column] = frame[column].astype(int)
    frame.insert(0, "CLIENTNUM", range(start_id, start_id + len(frame)))
    return frame


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a synthetic BankChurners-format CSV whose risk_level "
            "distribution matches --low-share/--medium-share(remainder)/--high-share."
        )
    )
    parser.add_argument("--count", type=int, default=2_000, help="생성할 고객 수(기본값: 2000)")
    parser.add_argument("--seed", type=int, default=42, help="재현 가능한 난수 시드(기본값: 42)")
    parser.add_argument(
        "--low-share", type=float, default=0.2, help="낮음 위험군 목표 비율(기본값: 0.2)"
    )
    parser.add_argument(
        "--high-share", type=float, default=0.2, help="높음 위험군 목표 비율(기본값: 0.2, 나머지는 주의로 배정)"
    )
    parser.add_argument(
        "--medium-threshold", type=float, default=0.5, help="중위험 이탈 확률 기준(기본값: 0.5)"
    )
    parser.add_argument(
        "--high-threshold", type=float, default=0.85, help="고위험 이탈 확률 기준(기본값: 0.85)"
    )
    parser.add_argument(
        "--oversample-factor",
        type=int,
        default=4,
        help="목표 위험군별 후보를 몇 배 오버샘플링할지(기본값: 4)",
    )
    parser.add_argument(
        "--start-id",
        type=int,
        default=1,
        help="CLIENTNUM 시작값(기본값: 1)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"CSV 저장 경로(기본값: {DEFAULT_OUTPUT_PATH})",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.low_share < 0 or args.high_share < 0 or args.low_share + args.high_share > 1.0:
        raise ValueError("--low-share and --high-share must be >= 0 and sum to at most 1.0.")

    registry = ModelRegistry(get_model_dir())
    registry.load()

    dataset, target_counts = build_synthetic_customers(
        registry,
        count=args.count,
        low_share=args.low_share,
        high_share=args.high_share,
        medium_threshold=args.medium_threshold,
        high_threshold=args.high_threshold,
        oversample_factor=args.oversample_factor,
        seed=args.seed,
    )

    actual_counts = dataset["risk_level"].value_counts().to_dict()
    print("생성된 위험도 분포:")
    for level in RISK_LEVEL_ORDER:
        actual = int(actual_counts.get(level, 0))
        print(f"  {level}: {actual}건 (목표 {target_counts[level]}건)")

    frame = to_bankchurners_frame(dataset, start_id=args.start_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    print(f"CSV 저장 완료: {args.output} ({len(frame)}행)")
    print(
        "적재하려면: docker compose exec backend python -m backend.scripts.import_customers "
        f"--data-path {args.output} --replace"
    )


if __name__ == "__main__":
    main()
