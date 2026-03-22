"""v2 ML Model — walk-forward training engine for regime classification.

Trains XGBoost, LightGBM, and LogisticRegression models with:
- Expanding window (primary) and rolling 2-year (challenger)
- Quarterly retraining (every 63 trading days)
- Two targets: coincident (same-day) and predictive (next-day)
"""

import logging
import os
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent.parent / "models" / "v2"
LABEL_MAP = {"Bearish": 0, "Neutral": 1, "Bullish": 2}
LABEL_INV = {v: k for k, v in LABEL_MAP.items()}

# Features to exclude from training (meta/target columns)
META_COLS = {
    "date", "coincident_truth", "predictive_truth",
    "availability_regime", "missing_count", "nifty_return",
    "breadth_ratio", "vix_change_pct", "next_day_return",
}

# Categorical features (label-encoded for XGBoost/LogReg, native for LightGBM)
CATEGORICAL_COLS = {"buildup_class"}

BUILDUP_MAP = {
    "long_buildup": 0, "short_covering": 1, "neutral": 2,
    "long_unwinding": 3, "short_buildup": 4,
}


def _get_feature_cols(df: pd.DataFrame) -> list[str]:
    """Get feature column names from a feature matrix DataFrame."""
    return [c for c in df.columns if c not in META_COLS]


def _prepare_features(df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """Prepare feature matrix for training — encode categoricals, keep nulls."""
    X = df[feature_cols].copy()
    if "buildup_class" in X.columns:
        X["buildup_class"] = X["buildup_class"].map(BUILDUP_MAP)
    return X


def _encode_target(series: pd.Series) -> np.ndarray:
    """Encode target labels to integers."""
    return series.map(LABEL_MAP).values


def train_xgboost(X_train, y_train, class_weights=None):
    """Train an XGBoost multiclass classifier."""
    import xgboost as xgb

    sample_weight = None
    if class_weights is not None:
        sample_weight = np.array([class_weights.get(y, 1.0) for y in y_train])

    model = xgb.XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        max_depth=5,
        n_estimators=200,
        learning_rate=0.08,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,
        eval_metric="mlogloss",
        random_state=42,
        verbosity=0,
    )
    model.fit(X_train, y_train, sample_weight=sample_weight)
    return model


def train_lightgbm(X_train, y_train, cat_features=None, class_weights=None):
    """Train a LightGBM multiclass classifier."""
    import lightgbm as lgb

    sample_weight = None
    if class_weights is not None:
        sample_weight = np.array([class_weights.get(y, 1.0) for y in y_train])

    model = lgb.LGBMClassifier(
        objective="multiclass",
        num_class=3,
        max_depth=5,
        n_estimators=200,
        learning_rate=0.08,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,
        random_state=42,
        verbose=-1,
    )
    cat_idx = "auto"
    if cat_features:
        cat_idx = [X_train.columns.get_loc(c) for c in cat_features if c in X_train.columns]

    model.fit(X_train, y_train, sample_weight=sample_weight,
              categorical_feature=cat_idx if cat_idx != "auto" and cat_idx else "auto")
    return model


def train_logreg(X_train, y_train, class_weights=None):
    """Train a LogisticRegression multiclass classifier (with imputation for nulls)."""
    X_filled = X_train.fillna(0)

    weight_dict = None
    if class_weights is not None:
        weight_dict = {k: v for k, v in class_weights.items()}

    model = LogisticRegression(
        max_iter=1000,
        solver="lbfgs",
        class_weight=weight_dict,
        random_state=42,
    )
    model.fit(X_filled, y_train)
    return model


def _compute_class_weights(y: np.ndarray) -> dict:
    """Compute inverse-frequency class weights."""
    classes, counts = np.unique(y, return_counts=True)
    total = len(y)
    return {c: total / (len(classes) * cnt) for c, cnt in zip(classes, counts)}


def walk_forward_train(
    df: pd.DataFrame,
    target_col: str = "coincident_truth",
    min_train_days: int = 252,
    retrain_every: int = 63,
    rolling_window: int = 504,
) -> dict:
    """Walk-forward training with expanding + rolling window.

    Returns dict with predictions for each test date and trained models.
    """
    feature_cols = _get_feature_cols(df)
    df = df.sort_values("date").reset_index(drop=True)

    # Filter rows where target is available
    valid_mask = df[target_col].notna()
    valid_df = df[valid_mask].reset_index(drop=True)

    if len(valid_df) < min_train_days + 10:
        logger.warning("Not enough data for walk-forward: %d rows, need %d+",
                       len(valid_df), min_train_days)
        return {"predictions": pd.DataFrame(), "models": {}}

    results = []
    models_cache = {}
    last_train_idx = -retrain_every  # force initial train

    for test_idx in range(min_train_days, len(valid_df)):
        # Check if we need to retrain
        need_retrain = (test_idx - last_train_idx) >= retrain_every

        if need_retrain:
            train_end = test_idx
            last_train_idx = test_idx

            # --- Expanding window (primary) ---
            train_df = valid_df.iloc[:train_end]
            X_train = _prepare_features(train_df, feature_cols)
            y_train = _encode_target(train_df[target_col])

            weights = _compute_class_weights(y_train)

            cat_feats = [c for c in CATEGORICAL_COLS if c in feature_cols]

            xgb_model = train_xgboost(X_train, y_train, class_weights=weights)
            lgb_model = train_lightgbm(X_train, y_train, cat_features=cat_feats, class_weights=weights)
            lr_model = train_logreg(X_train, y_train, class_weights=weights)

            models_cache["xgboost_expanding"] = xgb_model
            models_cache["lightgbm_expanding"] = lgb_model
            models_cache["logreg_expanding"] = lr_model

            # --- Rolling window (challenger) ---
            roll_start = max(0, train_end - rolling_window)
            roll_df = valid_df.iloc[roll_start:train_end]
            X_roll = _prepare_features(roll_df, feature_cols)
            y_roll = _encode_target(roll_df[target_col])

            if len(y_roll) >= min_train_days:
                roll_weights = _compute_class_weights(y_roll)
                xgb_roll = train_xgboost(X_roll, y_roll, class_weights=roll_weights)
                lgb_roll = train_lightgbm(X_roll, y_roll, cat_features=cat_feats, class_weights=roll_weights)
                lr_roll = train_logreg(X_roll, y_roll, class_weights=roll_weights)

                models_cache["xgboost_rolling"] = xgb_roll
                models_cache["lightgbm_rolling"] = lgb_roll
                models_cache["logreg_rolling"] = lr_roll

            logger.info("Retrained at idx %d (date=%s), train_size=%d",
                       test_idx, valid_df.iloc[test_idx]["date"], train_end)

        # --- Predict test date ---
        test_row = valid_df.iloc[[test_idx]]
        X_test = _prepare_features(test_row, feature_cols)

        row_result = {
            "date": valid_df.iloc[test_idx]["date"],
            "actual": valid_df.iloc[test_idx][target_col],
            "actual_encoded": _encode_target(pd.Series([valid_df.iloc[test_idx][target_col]]))[0],
        }

        for name, model in models_cache.items():
            try:
                if "logreg" in name:
                    X_pred = X_test.fillna(0)
                else:
                    X_pred = X_test

                pred = model.predict(X_pred)[0]
                row_result[f"{name}_pred"] = int(pred)
                row_result[f"{name}_label"] = LABEL_INV.get(int(pred), "Unknown")

                # Get probabilities
                if hasattr(model, "predict_proba"):
                    proba = model.predict_proba(X_pred)[0]
                    for cls_idx, cls_name in LABEL_INV.items():
                        if cls_idx < len(proba):
                            row_result[f"{name}_prob_{cls_name}"] = float(proba[cls_idx])
            except Exception as e:
                logger.warning("Prediction failed for %s on %s: %s",
                             name, row_result["date"], e)
                row_result[f"{name}_pred"] = None
                row_result[f"{name}_label"] = None

        results.append(row_result)

    predictions = pd.DataFrame(results)

    # Save final models
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for name, model in models_cache.items():
        path = MODELS_DIR / f"{name}_{target_col}.joblib"
        joblib.dump(model, path)
        logger.info("Saved model: %s", path)

    return {"predictions": predictions, "models": models_cache}


def run_full_training(feature_matrix_path: str) -> dict:
    """Run full walk-forward training for both coincident and predictive targets.

    Returns dict with predictions DataFrames for each target.
    """
    logger.info("Loading feature matrix from %s", feature_matrix_path)
    df = pd.read_csv(feature_matrix_path, parse_dates=["date"])
    df["date"] = pd.to_datetime(df["date"]).dt.date

    logger.info("Feature matrix: %d rows, %d columns", len(df), len(df.columns))

    results = {}

    # --- Coincident model ---
    if "coincident_truth" in df.columns:
        logger.info("=== Training COINCIDENT models ===")
        coincident = walk_forward_train(df, target_col="coincident_truth")
        results["coincident"] = coincident
        logger.info("Coincident predictions: %d rows", len(coincident["predictions"]))
    else:
        logger.warning("No coincident_truth column — skipping coincident models")

    # --- Predictive model ---
    if "predictive_truth" in df.columns:
        logger.info("=== Training PREDICTIVE models ===")
        predictive = walk_forward_train(df, target_col="predictive_truth")
        results["predictive"] = predictive
        logger.info("Predictive predictions: %d rows", len(predictive["predictions"]))
    else:
        logger.warning("No predictive_truth column — skipping predictive models")

    return results
