"""Machine learning: a normal-behaviour model plus residual-based anomaly detection.

Idea (common in wind-turbine condition monitoring): learn what the asset
*normally* produces from its operating conditions, using only healthy data.
Then compare every reading with that expectation. Long stretches where the
asset produces much less than the model expects are flagged as anomalies:
early signs of faults, degradation or unreported stoppages.

1. Training data: rows without missing values and without any flag_* column
   set (known downtime / under-performance are excluded, so the model learns
   healthy behaviour only).
2. Model: gradient-boosted trees (HistGradientBoostingRegressor) with an
   absolute-error loss, which is robust to the outliers that slip through.
3. Validation: chronological split. The model trains on the first 75 % of the
   period and is scored on the last 25 %, which it has never seen. No shuffling,
   so there is no leakage from the future. Compared against a baseline when one
   exists (e.g. the manufacturer's theoretical power curve).
4. Anomalies: residual = actual - predicted. The threshold is k robust standard
   deviations (MAD) of the training residuals. An event must last at least
   `min_duration` consecutive readings, so single noisy readings are ignored.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

MIN_TRAIN_ROWS = 200


def _features(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Numeric features; angle columns (e.g. wind direction) become sin/cos."""
    out = pd.DataFrame(index=df.index)
    for c in cols:
        if re.search(r"direction|angle|heading|bearing|deg|°", c, re.I):
            rad = np.deg2rad(df[c])
            out[f"{c}_sin"], out[f"{c}_cos"] = np.sin(rad), np.cos(rad)
        else:
            out[c] = df[c]
    return out


def train_and_detect(df: pd.DataFrame, target: str, features: list[str],
                     time_col: str | None, interval_hours: float | None,
                     baseline_col: str | None = None, threshold_sigma: float = 3.0,
                     min_duration: int = 6) -> tuple[dict, pd.DataFrame, pd.Series]:
    """Returns (summary for the agent/report, anomaly events, predictions)."""
    flags = [c for c in df.columns if c.startswith("flag_")]
    usable = df[[target, *features]].notna().all(axis=1)
    healthy = usable.copy()
    for f in flags:
        healthy &= ~df[f].fillna(False).astype(bool)

    order = df[time_col] if time_col else pd.Series(np.arange(len(df)), index=df.index)
    cutoff = order[healthy].quantile(0.75)
    train = healthy & (order <= cutoff)
    test = healthy & (order > cutoff)
    if train.sum() < MIN_TRAIN_ROWS or test.sum() < 20:
        raise ValueError(f"Not enough clean rows to train ({int(train.sum())} train, "
                         f"{int(test.sum())} test).")

    X = _features(df, features)
    model = HistGradientBoostingRegressor(loss="absolute_error", max_iter=300,
                                          learning_rate=0.08, random_state=0)
    model.fit(X[train], df.loc[train, target])

    pred = pd.Series(np.nan, index=df.index)
    pred[usable] = model.predict(X[usable])
    y_test, p_test = df.loc[test, target], pred[test]
    metrics: dict[str, Any] = {
        "mae": round(float(mean_absolute_error(y_test, p_test)), 2),
        "rmse": round(float(np.sqrt(mean_squared_error(y_test, p_test))), 2),
        "r2": round(float(r2_score(y_test, p_test)), 4),
    }
    if baseline_col:
        b = df.loc[test, baseline_col]
        ok = b.notna()
        metrics["baseline_column"] = baseline_col
        metrics["baseline_mae"] = round(float(mean_absolute_error(y_test[ok], b[ok])), 2)
        metrics["improvement_vs_baseline_pct"] = round(
            100 * (1 - metrics["mae"] / metrics["baseline_mae"]), 1)

    # Threshold from training residuals, using a robust spread (MAD).
    train_res = df.loc[train, target] - pred[train]
    sigma = float(1.4826 * (train_res - train_res.median()).abs().median()) or float(train_res.std())
    residual = df[target] - pred
    # Ignore readings where little output is expected anyway (e.g. calm wind).
    meaningful = pred > 0.05 * df.loc[train, target].max()
    low = (residual < -threshold_sigma * sigma) & meaningful & usable

    events = _events(df, low, pred, target, time_col, interval_hours, min_duration)
    in_event = pd.Series(False, index=df.index)
    for _, e in events.iterrows():
        in_event.loc[e["_start_idx"]:e["_end_idx"]] = True
    events = events.drop(columns=["_start_idx", "_end_idx"])

    summary = {
        "model": "HistGradientBoostingRegressor (absolute-error loss)",
        "target": target,
        "features": list(X.columns),
        "training_rows": int(train.sum()),
        "test_rows": int(test.sum()),
        "excluded_flag_columns": flags,
        "validation": "chronological split: first 75% train, last 25% test",
        "test_metrics": metrics,
        "residual_sigma": round(sigma, 2),
        "threshold": f"actual < predicted - {threshold_sigma} x {round(sigma, 1)}",
        "min_duration_readings": min_duration,
        "anomaly_events": int(len(events)),
        "anomalous_readings": int(in_event.sum()),
    }
    if len(events):
        summary["events_with_output"] = int((events["type"] == "underperformance").sum())
        summary["events_stopped"] = int((events["type"] == "stoppage").sum())
        if "lost_energy" in events:
            summary["lost_energy_in_events"] = round(float(events["lost_energy"].sum()), 1)
        summary["largest_events"] = events.head(5).to_dict(orient="records")
    return summary, events, pred


def _events(df, low: pd.Series, pred: pd.Series, target: str, time_col: str | None,
            interval_hours: float | None, min_duration: int) -> pd.DataFrame:
    groups = (low != low.shift()).cumsum()
    rows = []
    for _, idx in low[low].groupby(groups[low]).groups.items():
        if len(idx) < min_duration:
            continue
        actual, expected = df.loc[idx, target], pred[idx]
        row: dict[str, Any] = {
            "_start_idx": idx[0], "_end_idx": idx[-1],
            "readings": len(idx),
            "type": "stoppage" if (actual <= 0).mean() > 0.5 else "underperformance",
            "avg_actual": round(float(actual.mean()), 1),
            "avg_expected": round(float(expected.mean()), 1),
            "shortfall_pct": round(float(100 * (1 - actual.clip(lower=0).sum() / expected.sum())), 1),
        }
        if time_col:
            row["start"] = str(df.loc[idx[0], time_col])
            row["end"] = str(df.loc[idx[-1], time_col])
        if interval_hours:
            row["duration_hours"] = round(len(idx) * interval_hours, 1)
            row["lost_energy"] = round(float((expected - actual.clip(lower=0)).sum() * interval_hours), 1)
        rows.append(row)
    events = pd.DataFrame(rows)
    if events.empty:
        return pd.DataFrame(columns=["_start_idx", "_end_idx", "readings", "type"])
    sort_col = "lost_energy" if "lost_energy" in events else "readings"
    front = [c for c in ["start", "end", "duration_hours", "type"] if c in events]
    rest = [c for c in events.columns if c not in front]
    return events.sort_values(sort_col, ascending=False)[front + rest].reset_index(drop=True)
