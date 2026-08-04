"""고객 이탈 여부를 예측하는 최종 분류 모델(LightGBM). 학습·평가·저장을 담당한다.

베이스라인·피처엔지니어링·하이퍼파라미터 비교는 이 파일이 하지 않는다 — 전부
`hyperparameter/classification_search.py`가 독립적으로 실행하고 저장한다
(`python src/hyperparameter/classification_search.py`). 이 파일은 그 탐색에서 나온
결론(`BEST_PARAMS`, `classification_search.py`에 값으로 굳혀둔 상수)을 그대로 가져다
최종 LightGBM을 재학습하고, Test는 한 번만 평가하며, 저장한다.


역할 분담:
  - `feature_engine/classification_features.py` — 피처 정의(파생변수 포함 여부) + 전처리 재노출
  - `hyperparameter/classification_search.py`   — 베이스라인·피처엔지니어링·하이퍼파라미터 비교(독립 실행)
  - 이 파일                                       — 튜닝 LightGBM 학습 + 앙상블 비교(선정 근거 기록) + Test 1회 평가 + 저장
  - `final/classification_business.py`           — SHAP·Lift/Gain·CLV·비용임계값·세그먼트 (저장된 모델 재사용)

전처리(`build_preprocessor`)는 항상 `Pipeline(prep, model)`로 묶어 Train에만 fit하고
Validation/Test는 transform만 한다 — 저장되는 모델도 전처리가 포함된 완성형이라
새 원본 데이터에 바로 `predict()`를 호출할 수 있다.

실행: python src/final/classification_final.py
(하이퍼파라미터를 다시 탐색해 BEST_PARAMS를 갱신하려면 그 전에
 python src/hyperparameter/classification_search.py 를 먼저 실행)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from typing import Any

import joblib
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.ensemble import RandomForestClassifier, StackingClassifier, VotingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier

from common import config
from common.data import load_raw_data
from common.evaluate import evaluate_classification, make_classification_predictions
from feature_engine.classification_features import CHURN_TASK, ClassificationTaskSpec, build_preprocessor
from final.update_classification_manifest import update_manifest
from hyperparameter.classification_search import get_best_params

MODEL_NAME = "LightGBM_Tuned"
TASK = CHURN_TASK  # 이 파이프라인이 다루는 유일한 분류 태스크 (원본 피처, 파생변수 미채택)


def _split(task: ClassificationTaskSpec, df: pd.DataFrame):
    """태스크의 raw X/y를 60/20/20(Train/Val/Test)으로, stratify 유지하며 나눈다.
    """
    X, y = task.to_X_y(df)
    Xtr, Xtemp, ytr, ytemp = train_test_split(X, y, test_size=0.4, random_state=config.RANDOM_STATE, stratify=y)
    Xva, Xte, yva, yte = train_test_split(Xtemp, ytemp, test_size=0.5, random_state=config.RANDOM_STATE, stratify=ytemp)
    return Xtr, Xva, Xte, ytr, yva, yte


def _make_lgbm(best_params: dict, random_state: int = config.RANDOM_STATE) -> LGBMClassifier:
    return LGBMClassifier(**best_params, class_weight="balanced", random_state=random_state, verbose=-1)


def _make_ensemble_members(Xtr_enc, ytr: pd.Series, best_params: dict) -> list[tuple[str, Any]]:
    """전처리된 X로 미리 학습해둔 RF/XGB/LightGBM(튜닝) 3개를 만든다 (Voting/Stacking 공용)."""
    neg, pos = (ytr == 0).sum(), (ytr == 1).sum()
    rf = RandomForestClassifier(n_estimators=200, class_weight="balanced", random_state=config.RANDOM_STATE).fit(
        Xtr_enc, ytr
    )
    xgb = XGBClassifier(
        n_estimators=200, eval_metric="logloss", scale_pos_weight=neg / pos, random_state=config.RANDOM_STATE
    ).fit(Xtr_enc, ytr)
    lgbm = _make_lgbm(best_params).fit(Xtr_enc, ytr)
    return [("rf", rf), ("xgb", xgb), ("lgbm", lgbm)]


def build_ensemble_comparison(Xtr: pd.DataFrame, ytr: pd.Series, best_params: dict) -> dict[str, Pipeline]:
    """튜닝 LightGBM 단독 vs Voting vs Stacking, 3개 후보를 모두 학습해 반환한다.
    """
    preprocessor = build_preprocessor(Xtr)
    Xtr_enc = preprocessor.fit_transform(Xtr)

    lgbm_tuned = _make_lgbm(best_params).fit(Xtr_enc, ytr)
    members = _make_ensemble_members(Xtr_enc, ytr, best_params)

    voting = VotingClassifier(members, voting="soft").fit(Xtr_enc, ytr)
    stacking = StackingClassifier(members, final_estimator=LogisticRegression(), cv=5).fit(Xtr_enc, ytr)

    return {
        "LightGBM (Tuned)": Pipeline([("prep", preprocessor), ("model", lgbm_tuned)]),
        "Voting": Pipeline([("prep", preprocessor), ("model", voting)]),
        "Stacking": Pipeline([("prep", preprocessor), ("model", stacking)]),
    }


def main() -> dict[str, Any]:
    """튜닝 LightGBM 단독 vs Voting vs Stacking 비교 → 최종모델 선정 → Test 1회 평가 → 저장.
    """
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)

    df = load_raw_data()
    best_params = get_best_params()

    Xtr, Xva, Xte, ytr, yva, yte = _split(TASK, df)

    candidates = build_ensemble_comparison(Xtr, ytr, best_params)
    board = (
        pd.DataFrame([evaluate_classification(name, "Validation", pipe, Xva, yva) for name, pipe in candidates.items()])
        .sort_values("F1", ascending=False)
        .reset_index(drop=True)
    )
    print("[튜닝 LightGBM vs Voting vs Stacking] Validation 비교 (최종모델 선정 근거):")
    print(board.round(4).to_string(index=False))

    final_model = candidates["LightGBM (Tuned)"]  # 노트북 결론: 앙상블보다 단독이 우수
   
    test_row = evaluate_classification(MODEL_NAME, "Test", final_model, Xte, yte)
    print(f"\n[{MODEL_NAME}] Test 최종 평가 (처음이자 마지막):")
    print(pd.DataFrame([test_row]).round(4).to_string(index=False))

    # 진단용: 인코딩된 컬럼(num__/cat__ 접두어) 기준 Feature Importance.
    importance = pd.Series(
        final_model.named_steps["model"].feature_importances_,
        index=final_model.named_steps["prep"].get_feature_names_out(),
    ).sort_values(ascending=False)
    print("\n예측 근거 Top10 (Feature Importance):")
    print(importance.head(10).round(4).to_string())

    predictions = make_classification_predictions(MODEL_NAME, final_model, Xte, yte)

    joblib.dump(final_model, config.MODEL_DIR / "classification_lightgbm_final.joblib")
    board.to_csv(config.REPORT_DIR / "classification_ensemble_comparison.csv", index=False)
    pd.DataFrame([test_row]).to_csv(config.REPORT_DIR / "classification_final_metrics.csv", index=False)
    importance.to_frame("importance").to_csv(config.REPORT_DIR / "classification_feature_importance.csv")
    predictions.to_csv(config.REPORT_DIR / "classification_test_predictions.csv", index=False)
    update_manifest()

    print(f"\n저장 완료: {config.MODEL_DIR / 'classification_lightgbm_final.joblib'}")
    print(f"저장 완료: {config.REPORT_DIR / 'classification_ensemble_comparison.csv'}")
    print(f"저장 완료: {config.REPORT_DIR / 'classification_final_metrics.csv'}")
    print(f"저장 완료: {config.REPORT_DIR / 'classification_feature_importance.csv'}")
    print(
        f"저장 완료: {config.REPORT_DIR / 'classification_test_predictions.csv'} "
        "(Test 전용, classification_business.py가 읽음)"
    )

    return {
        "model": final_model,
        "board": board,
        "test_metrics": test_row,
        "importance": importance,
        "predictions": predictions,
    }


if __name__ == "__main__":
    main()
