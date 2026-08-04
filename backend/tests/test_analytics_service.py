"""EDA 원천 데이터셋 기반 분석 통계 계산을 검증합니다."""

from __future__ import annotations

import pytest

from backend.app.services import analytics_service


def test_categorical_churn_rate_sums_to_full_population() -> None:
    """성별 그룹의 표본 수 합이 전체 고객 수와 일치하는지 확인합니다."""
    items = analytics_service.categorical_churn_rate("Gender")

    assert {item["group"] for item in items} == {"M", "F"}
    for item in items:
        assert 0.0 <= item["churn_rate"] <= 1.0
        assert item["count"] > 0
    assert sum(item["count"] for item in items) == 10127


def test_categorical_churn_rate_rejects_unknown_field() -> None:
    """지원하지 않는 필드는 명확히 거부합니다."""
    with pytest.raises(ValueError):
        analytics_service.categorical_churn_rate("Not_A_Real_Column")


def test_numeric_distribution_quartiles_are_ordered() -> None:
    """이탈(1)/유지(0) 각각 min<=q1<=median<=q3<=max 순서를 지키는지 확인합니다."""
    result = analytics_service.numeric_distribution("Total_Trans_Ct")

    assert set(result.keys()) == {"0", "1"}
    for bucket in result.values():
        assert bucket["min"] <= bucket["q1"] <= bucket["median"]
        assert bucket["median"] <= bucket["q3"] <= bucket["max"]
        assert bucket["count"] > 0


def test_feature_correlation_matches_known_eda_ranking() -> None:
    """노트북 EDA에서 확인된 상관관계 상위 변수·부호가 그대로 재현되는지 확인합니다."""
    items = analytics_service.feature_correlation()

    by_feature = {item["feature"]: item["correlation"] for item in items}
    assert by_feature["Total_Trans_Ct"] < 0
    assert by_feature["Contacts_Count_12_mon"] > 0
    assert items[0]["feature"] == "Total_Trans_Ct"

    magnitudes = [abs(item["correlation"]) for item in items]
    assert magnitudes == sorted(magnitudes, reverse=True)
