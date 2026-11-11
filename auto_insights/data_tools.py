"""Deterministic data-analysis tools the agent can call.

Every public method on `AnalysisSession` returns a JSON-serialisable dict so the
result can be sent straight back to the LLM as a tool result. The LLM decides
*which* tools to run and *how to interpret* the results; the numbers themselves
always come from pandas, never from the model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Common timestamp layouts tried before falling back to pandas' own inference.
_DATE_FORMATS = ["%d %m %Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                 "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"]

# Allowed characters in a rule condition: column names in backticks, numbers,
# comparison/arithmetic operators and the words and/or/not. No function calls,
# attribute access or local-variable references (`@`), so a rule can only
# filter rows, never run code.
_SAFE_RULE = re.compile(r"^(`[^`]+`|[\d\.\s<>=!&|+\-*/()]|\band\b|\bor\b|\bnot\b)+$")


def _fmt_interval(td: pd.Timedelta) -> str:
    minutes = int(td / pd.Timedelta(minutes=1))
    if minutes % 1440 == 0:
        return f"{minutes // 1440}-day"
    if minutes % 60 == 0:
        return f"{minutes // 60}-hour"
    return f"{minutes}-minute"


def _round(value: Any, ndigits: int = 3) -> Any:
    if isinstance(value, (float, np.floating)):
        return None if np.isnan(value) else round(float(value), ndigits)
    if isinstance(value, np.integer):
        return int(value)
    return value


@dataclass
class Finding:
    """A data-quality issue found by the agent."""
    check: str
    severity: str  # "high" | "medium" | "low"
    rows_affected: int
    description: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class AnalysisSession:
    """Holds the dataset and everything the agent has learned about it."""
    raw: pd.DataFrame
    name: str = "dataset"
    time_col: str | None = None
    df: pd.DataFrame = field(init=False)
    findings: list[Finding] = field(default_factory=list)
    cleaning_log: list[str] = field(default_factory=list)
    kpis: dict = field(default_factory=dict)
    trends: dict = field(default_factory=dict)
    eda: dict = field(default_factory=dict)
    charts: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.raw.columns = [str(c).strip().lstrip("﻿") for c in self.raw.columns]
        self.df = self.raw.copy()
        if self.time_col is None:
            self.time_col = self._detect_time_column()
        if self.time_col:
            self.df[self.time_col] = self._parse_datetime(self.df[self.time_col])
            self.df = self.df.sort_values(self.time_col).reset_index(drop=True)

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_csv(cls, path: str | Path, time_col: str | None = None) -> "AnalysisSession":
        path = Path(path)
        raw = pd.read_csv(path, encoding="utf-8-sig")
        return cls(raw=raw, name=path.stem, time_col=time_col)

    def _detect_time_column(self) -> str | None:
        for col in self.df.columns:
            if pd.api.types.is_datetime64_any_dtype(self.df[col]):
                return col
            if self.df[col].dtype == object or pd.api.types.is_string_dtype(self.df[col]):
                if re.search(r"date|time|timestamp", col, re.I):
                    return col
        return None

    @staticmethod
    def _parse_datetime(series: pd.Series) -> pd.Series:
        if pd.api.types.is_datetime64_any_dtype(series):
            return series
        sample = series.dropna().astype(str).head(50)
        for fmt in _DATE_FORMATS:
            try:
                pd.to_datetime(sample, format=fmt)
                return pd.to_datetime(series, format=fmt, errors="coerce")
            except (ValueError, TypeError):
                continue
        return pd.to_datetime(series, errors="coerce")

    @property
    def numeric_cols(self) -> list[str]:
        return [c for c in self.df.select_dtypes(include="number").columns]

    def _require_columns(self, *cols: str | None) -> None:
        missing = [c for c in cols if c and c not in self.df.columns]
        if missing:
            raise ValueError(f"Unknown column(s): {missing}. Available: {list(self.df.columns)}")

    def _interval(self) -> pd.Timedelta | None:
        if not self.time_col:
            return None
        diffs = self.df[self.time_col].dropna().diff().dropna()
        return diffs.mode().iloc[0] if not diffs.empty else None

    # --------------------------------------------------------------- profiling
    def profile_dataset(self) -> dict:
        """Shape, column types, summary statistics and time coverage."""
        df = self.df
        columns = []
        for col in df.columns:
            s = df[col]
            info: dict[str, Any] = {
                "name": col,
                "dtype": str(s.dtype),
                "missing": int(s.isna().sum()),
                "unique": int(s.nunique()),
            }
            if pd.api.types.is_numeric_dtype(s):
                info.update({k: _round(v) for k, v in s.describe().items() if k != "count"})
                info["zeros"] = int((s == 0).sum())
                info["negatives"] = int((s < 0).sum())
            elif pd.api.types.is_datetime64_any_dtype(s):
                info.update({"min": str(s.min()), "max": str(s.max())})
            else:
                info["top_values"] = s.value_counts().head(5).to_dict()
            columns.append(info)

        result = {
            "dataset": self.name,
            "rows": len(df),
            "columns": len(df.columns),
            "duplicate_rows": int(df.duplicated().sum()),
            "time_column": self.time_col,
            "column_profiles": columns,
        }
        interval = self._interval()
        if interval is not None:
            result["sampling_interval"] = _fmt_interval(interval)
            result["time_range"] = [str(df[self.time_col].min()), str(df[self.time_col].max())]
        return result

    # ----------------------------------------------------------- missing values
    def analyze_missing_values(self) -> dict:
        """Missing cells per column plus gaps in the time series."""
        df = self.df
        per_col = {c: int(n) for c, n in df.isna().sum().items() if n}
        result: dict[str, Any] = {
            "missing_cells_per_column": per_col,
            "total_missing_cells": int(sum(per_col.values())),
        }
        interval = self._interval()
        if interval is not None:
            ts = df[self.time_col].dropna()
            expected = pd.date_range(ts.min(), ts.max(), freq=interval)
            missing_ts = expected.difference(ts)
            result["expected_timestamps"] = len(expected)
            result["missing_timestamps"] = len(missing_ts)
            result["missing_timestamp_pct"] = _round(100 * len(missing_ts) / len(expected), 2)
            if len(missing_ts):
                gaps = pd.Series(missing_ts).diff().ne(interval).cumsum()
                runs = pd.Series(missing_ts).groupby(gaps).agg(["min", "max", "count"])
                longest = runs.sort_values("count", ascending=False).head(5)
                result["longest_gaps"] = [
                    {"start": str(r["min"]), "end": str(r["max"]),
                     "missing_intervals": int(r["count"]),
                     "duration_hours": _round(r["count"] * interval / pd.Timedelta(hours=1), 1)}
                    for _, r in longest.iterrows()
                ]
                if len(missing_ts) / len(expected) > 0.01:
                    self._add_finding("missing_timestamps", "medium", len(missing_ts),
                                      f"{len(missing_ts):,} expected {_fmt_interval(interval)} readings are missing "
                                      f"({result['missing_timestamp_pct']}% of the period).")
        return result

    def handle_missing_values(self, strategy: str = "interpolate", max_gap: int = 3) -> dict:
        """Fill missing data.

        strategy: "interpolate" (time-based, only short gaps up to `max_gap`
        intervals; longer outages stay empty so they are not invented),
        "drop" (remove incomplete rows), "median" (fill with column median) or
        "reindex_only" (add the missing timestamps as empty rows).
        """
        if strategy not in {"interpolate", "drop", "median", "reindex_only"}:
            raise ValueError("strategy must be interpolate, drop, median or reindex_only")
        before = len(self.df)
        df = self.df
        interval = self._interval()
        added = 0
        if interval is not None and strategy != "drop":
            df = df.drop_duplicates(subset=self.time_col).set_index(self.time_col)
            full = pd.date_range(df.index.min(), df.index.max(), freq=interval)
            added = len(full) - len(df)
            df = df.reindex(full)
            df.index.name = self.time_col
        filled = 0
        num = [c for c in df.select_dtypes(include="number").columns]
        if strategy == "interpolate":
            missing_before = int(df[num].isna().sum().sum())
            method = "time" if isinstance(df.index, pd.DatetimeIndex) else "linear"
            df[num] = df[num].interpolate(method=method, limit=max_gap, limit_area="inside")
            filled = missing_before - int(df[num].isna().sum().sum())
        elif strategy == "median":
            missing_before = int(df[num].isna().sum().sum())
            df[num] = df[num].fillna(df[num].median())
            filled = missing_before
        elif strategy == "drop":
            df = df.dropna()
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
        self.df = df
        remaining = int(df[num].isna().sum().sum()) if num else 0
        msg = (f"Missing values: strategy={strategy}, timestamps added={added}, "
               f"cells filled={filled}, cells still empty={remaining}, rows {before}->{len(df)}")
        self.cleaning_log.append(msg)
        return {"strategy": strategy, "timestamps_added": added, "cells_filled": filled,
                "cells_still_missing": remaining, "rows_before": before, "rows_after": len(df),
                "note": "Gaps longer than max_gap are left empty on purpose (likely outages)."
                        if strategy == "interpolate" else ""}

    # --------------------------------------------------------- inconsistencies
    def detect_inconsistencies(self, z_threshold: float = 4.0) -> dict:
        """Generic checks: duplicates, negatives, extreme outliers, flat-lined sensors."""
        df = self.df
        checks: dict[str, Any] = {}
        if self.time_col:
            dup_ts = int(df[self.time_col].duplicated().sum())
            checks["duplicate_timestamps"] = dup_ts
            if dup_ts:
                self._add_finding("duplicate_timestamps", "high", dup_ts,
                                  f"{dup_ts} rows share a timestamp with another row.")
        dup = int(df.duplicated().sum())
        checks["duplicate_rows"] = dup
        if dup:
            self._add_finding("duplicate_rows", "medium", dup, f"{dup} fully duplicated rows.")

        per_col = {}
        for col in self.numeric_cols:
            s = df[col].dropna()
            if s.empty:
                continue
            info: dict[str, Any] = {}
            neg = int((s < 0).sum())
            if 0 < neg < 0.05 * len(s):  # mostly-positive column with a few negatives
                info["unexpected_negatives"] = neg
                info["min_value"] = _round(s.min())
            std = s.std()
            if std and std > 0:
                z = ((s - s.mean()) / std).abs()
                n_out = int((z > z_threshold).sum())
                if n_out:
                    info["extreme_outliers"] = n_out
            # Sensor stuck on the same value for a long run (ignoring zeros).
            runs = s.ne(s.shift()).cumsum()
            run_len = s.groupby(runs).transform("size")
            stuck = int(((run_len >= 18) & (s != 0)).sum())
            if stuck:
                info["flatlined_readings"] = stuck
            if info:
                per_col[col] = info
        checks["column_checks"] = per_col
        return {"checks": checks, "note": "Use check_rule to test domain-specific rules."}

    def check_rule(self, name: str, condition: str, description: str,
                   severity: str = "medium") -> dict:
        """Count rows that violate a domain rule written as a pandas query.

        `condition` describes the BAD rows, e.g.
        "`LV ActivePower (kW)` <= 0 and `Wind Speed (m/s)` > 4".
        """
        if severity not in {"high", "medium", "low"}:
            raise ValueError("severity must be high, medium or low")
        if not _SAFE_RULE.match(condition):
            raise ValueError("Condition may only use `backticked column names`, numbers, "
                             "comparison/arithmetic operators and and/or/not.")
        for col in re.findall(r"`([^`]+)`", condition):
            self._require_columns(col)
        bad = self.df.query(condition, engine="python")
        n = len(bad)
        result: dict[str, Any] = {"rule": name, "rows_violating": n,
                                  "pct_of_rows": _round(100 * n / max(len(self.df), 1), 2)}
        if n:
            self._add_finding(name, severity, n, f"{description} ({n:,} rows, "
                                                 f"{result['pct_of_rows']}%).")
            if self.time_col:
                result["first_examples"] = bad[self.time_col].astype(str).head(3).tolist()
        return result

    def flag_rows(self, flag_name: str, condition: str) -> dict:
        """Add a boolean column marking rows that match a condition (kept, not deleted)."""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", flag_name):
            raise ValueError("flag_name must be a simple identifier, e.g. flag_curtailment")
        if not _SAFE_RULE.match(condition):
            raise ValueError("Unsafe condition; see check_rule for the allowed syntax.")
        mask = self.df.eval(condition, engine="python").fillna(False).astype(bool)
        self.df[flag_name] = mask
        self.cleaning_log.append(f"Flagged {int(mask.sum())} rows as {flag_name}: {condition}")
        return {"flag": flag_name, "rows_flagged": int(mask.sum())}

    # ------------------------------------------------------------------- EDA
    def run_eda(self, columns: list[str] | None = None) -> dict:
        """Distributions, skew and the strongest correlations."""
        cols = columns or [c for c in self.numeric_cols]
        self._require_columns(*cols)
        num = self.df[cols].select_dtypes(include="number")
        dist = {}
        for c in num.columns:
            s = num[c].dropna()
            q1, q3 = s.quantile([0.25, 0.75])
            iqr = q3 - q1
            dist[c] = {
                "mean": _round(s.mean()), "median": _round(s.median()), "std": _round(s.std()),
                "skew": _round(s.skew()),
                "iqr_outliers": int(((s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)).sum()),
                "p05": _round(s.quantile(0.05)), "p95": _round(s.quantile(0.95)),
            }
        corr = num.corr()
        pairs = []
        for i, a in enumerate(corr.columns):
            for b in corr.columns[i + 1:]:
                v = corr.loc[a, b]
                if not np.isnan(v):
                    pairs.append({"a": a, "b": b, "pearson_r": _round(v)})
        pairs.sort(key=lambda p: abs(p["pearson_r"]), reverse=True)
        self.eda = {"distributions": dist, "top_correlations": pairs[:8]}
        return self.eda

    # ------------------------------------------------------------------ KPIs
    def compute_kpis(self, actual_col: str, expected_col: str | None = None,
                     rated_capacity: float | None = None, unit: str = "kW") -> dict:
        """Operational KPIs for an output measure, optionally vs. an expected value.

        Works for any "actual vs. target" signal: power vs. power curve, units
        produced vs. plan, etc. With a time column and a power unit (kW/MW)
        energy is integrated over the sampling interval.
        """
        self._require_columns(actual_col, expected_col)
        df = self.df
        actual = df[actual_col]
        valid = actual.notna()
        k: dict[str, Any] = {
            "actual_column": actual_col,
            "mean_output": _round(actual.mean(), 1),
            "peak_output": _round(actual.max(), 1),
            "data_coverage_pct": _round(100 * valid.mean(), 2),
            "producing_intervals_pct": _round(100 * (actual[valid] > 0).mean(), 2),
        }
        interval = self._interval()
        if interval is not None and unit.lower() in {"kw", "mw"}:
            hours = interval / pd.Timedelta(hours=1)
            energy_unit = "kWh" if unit.lower() == "kw" else "MWh"
            k["total_energy"] = _round(actual.clip(lower=0).sum() * hours, 0)
            k["energy_unit"] = energy_unit
            k["energy_note"] = "Integrated over recorded intervals only (gaps contribute nothing)."
        if rated_capacity:
            k["rated_capacity"] = rated_capacity
            k["capacity_factor_pct"] = _round(100 * actual.clip(lower=0).mean() / rated_capacity, 2)
        if expected_col:
            exp = df[expected_col]
            both = actual.notna() & exp.notna()
            exp_sum = exp[both].clip(lower=0).sum()
            k["expected_column"] = expected_col
            if exp_sum > 0:
                k["performance_ratio_pct"] = _round(
                    100 * actual[both].clip(lower=0).sum() / exp_sum, 2)
                k["lost_output_pct"] = _round(100 - k["performance_ratio_pct"], 2)
            # Availability: of the intervals where output was expected, how many produced?
            should_run = both & (exp > 0.05 * (rated_capacity or exp.max()))
            if should_run.any():
                k["availability_pct"] = _round(100 * (actual[should_run] > 0).mean(), 2)
            under = should_run & (actual < 0.5 * exp)
            k["underperforming_intervals"] = int(under.sum())
        self.kpis = k
        return k

    # ---------------------------------------------------------------- trends
    def analyze_trends(self, value_cols: list[str], freq: str = "ME") -> dict:
        """Aggregate columns by period (D, W, ME, QE) and describe the trend."""
        if not self.time_col:
            raise ValueError("No time column; trends need a timestamp.")
        if freq not in {"D", "W", "ME", "QE", "h"}:
            raise ValueError("freq must be one of D, W, ME, QE, h")
        self._require_columns(*value_cols)
        g = self.df.set_index(self.time_col)[value_cols].resample(freq).mean()
        out: dict[str, Any] = {"freq": freq, "series": {}}
        for c in value_cols:
            s = g[c].dropna()
            if len(s) < 2:
                continue
            x = np.arange(len(s))
            slope = np.polyfit(x, s.values, 1)[0]
            out["series"][c] = {
                "by_period": {str(i.date()): _round(v, 2) for i, v in s.items()},
                "best_period": str(s.idxmax().date()), "best_value": _round(s.max(), 2),
                "worst_period": str(s.idxmin().date()), "worst_value": _round(s.min(), 2),
                "linear_trend_per_period": _round(slope, 3),
                "variation_coefficient": _round(s.std() / s.mean(), 3) if s.mean() else None,
            }
        self.trends = out
        return out

    # ------------------------------------------------------------- charts
    def add_chart(self, kind: str, x: str, y: str | None = None, title: str = "",
                  color: str | None = None, freq: str | None = None) -> dict:
        """Queue a chart for the dashboard. kind: line, scatter, histogram, bar."""
        if kind not in {"line", "scatter", "histogram", "bar"}:
            raise ValueError("kind must be line, scatter, histogram or bar")
        self._require_columns(x, y, color)
        spec = {"kind": kind, "x": x, "y": y, "title": title or f"{y or x}", "color": color,
                "freq": freq}
        self.charts.append(spec)
        return {"queued": spec, "total_charts": len(self.charts)}

    # -------------------------------------------------------------- helpers
    def _add_finding(self, check: str, severity: str, rows: int, description: str) -> None:
        self.findings = [f for f in self.findings if f.check != check]
        self.findings.append(Finding(check, severity, rows, description))

    def summary_state(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "cleaning_log": self.cleaning_log,
            "kpis": self.kpis,
        }
