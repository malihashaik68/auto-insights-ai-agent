"""Offline pipeline: same tools, fixed order, rule-based narrative.

Used when no ANTHROPIC_API_KEY is set (and in tests), so the project always runs.
The agent mode replaces the fixed order and templated text with Claude's choices.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .data_tools import AnalysisSession
from .report import Narrative


@dataclass
class Preset:
    """Known column roles and domain rules for a type of dataset."""
    actual_col: str | None = None
    expected_col: str | None = None
    rated_capacity: float | None = None
    unit: str = ""
    trend_cols: list[str] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    charts: list[dict] = field(default_factory=list)


P, W, T, D = ("`LV ActivePower (kW)`", "`Wind Speed (m/s)`",
              "`Theoretical_Power_Curve (KWh)`", "`Wind Direction (°)`")

PRESETS: dict[str, Preset] = {
    "wind_turbine": Preset(
        actual_col="LV ActivePower (kW)",
        expected_col="Theoretical_Power_Curve (KWh)",
        rated_capacity=3600.0,
        unit="kW",
        trend_cols=["LV ActivePower (kW)", "Wind Speed (m/s)"],
        rules=[
            {"name": "negative_power", "severity": "low",
             "condition": f"{P} < 0",
             "description": "Negative active power (turbine consuming grid power while idle)"},
            {"name": "zero_output_in_wind", "severity": "high",
             "condition": f"{P} <= 0 and {W} >= 4",
             "description": "No output although wind is above cut-in speed (downtime or curtailment)"},
            {"name": "severe_underperformance", "severity": "medium",
             "condition": f"{P} > 0 and {P} < 0.5 * {T} and {T} > 500",
             "description": "Producing less than half of the theoretical power curve"},
            {"name": "power_above_rated", "severity": "low",
             "condition": f"{P} > 3700",
             "description": "Output above rated capacity (check sensor calibration)"},
            {"name": "invalid_wind_direction", "severity": "high",
             "condition": f"{D} < 0 or {D} > 360",
             "description": "Wind direction outside 0-360 degrees"},
        ],
        flags=[{"flag_name": "flag_downtime", "condition": f"{P} <= 0 and {W} >= 4"},
               {"flag_name": "flag_underperformance",
                "condition": f"{P} > 0 and {P} < 0.5 * {T} and {T} > 500"}],
        charts=[
            {"kind": "line", "x": "Date/Time", "y": "LV ActivePower (kW)", "freq": "D",
             "title": "Daily average active power (kW)"},
            {"kind": "scatter", "x": "Wind Speed (m/s)", "y": "LV ActivePower (kW)",
             "color": "flag_downtime", "title": "Power curve: actual output vs wind speed"},
            {"kind": "line", "x": "Date/Time", "y": "Wind Speed (m/s)", "freq": "W",
             "title": "Weekly average wind speed (m/s)"},
            {"kind": "histogram", "x": "Wind Speed (m/s)", "title": "Wind speed distribution"},
        ],
    ),
}


def run_offline(session: AnalysisSession, preset: Preset | None = None) -> Narrative:
    preset = preset or Preset()
    profile = session.profile_dataset()
    missing = session.analyze_missing_values()
    session.handle_missing_values("interpolate", max_gap=3)
    session.detect_inconsistencies()
    for rule in preset.rules:
        session.check_rule(**rule)
    for flag in preset.flags:
        session.flag_rows(**flag)
    session.run_eda()

    actual = preset.actual_col or (session.numeric_cols[0] if session.numeric_cols else None)
    if actual:
        session.compute_kpis(actual, preset.expected_col, preset.rated_capacity,
                             preset.unit or "units")
    trend_cols = preset.trend_cols or session.numeric_cols[:2]
    if session.time_col and trend_cols:
        session.analyze_trends(trend_cols, "ME")
    for chart in preset.charts:
        session.add_chart(**chart)
    if not preset.charts and actual:
        if session.time_col:
            session.add_chart("line", session.time_col, actual, f"{actual} over time", freq="D")
        session.add_chart("histogram", actual, title=f"{actual} distribution")
    return _narrative(session, profile, missing, actual)


def _narrative(session: AnalysisSession, profile: dict, missing: dict, actual: str | None) -> Narrative:
    k = session.kpis
    summary = [f"The dataset has {profile['rows']:,} rows and {profile['columns']} columns"
               + (f" covering {profile['time_range'][0][:10]} to {profile['time_range'][1][:10]}."
                  if "time_range" in profile else ".")]
    if "missing_timestamps" in missing:
        summary.append(f"{missing['missing_timestamps']:,} expected readings "
                       f"({missing['missing_timestamp_pct']}%) are missing.")
    if "capacity_factor_pct" in k:
        summary.append(f"Capacity factor was {k['capacity_factor_pct']}%"
                       + (f" and the asset delivered {k['performance_ratio_pct']}% of its "
                          f"expected output." if "performance_ratio_pct" in k else "."))
    findings = [f.description for f in sorted(session.findings,
                key=lambda f: {"high": 0, "medium": 1, "low": 2}[f.severity])]
    for col, info in session.trends.get("series", {}).items():
        findings.append(f"{col}: best month {info['best_period'][:7]} ({info['best_value']}), "
                        f"worst month {info['worst_period'][:7]} ({info['worst_value']}).")
    for pair in session.eda.get("top_correlations", [])[:2]:
        findings.append(f"{pair['a']} and {pair['b']} correlate at r = {pair['pearson_r']}.")
    recs = ["Investigate the high-severity findings first; they represent lost output."]
    if missing.get("missing_timestamps"):
        recs.append("Fix data logging for the longest gaps so KPIs are not understated.")
    if k.get("underperforming_intervals"):
        recs.append("Review under-performing intervals against maintenance logs.")
    return Narrative(title=f"Auto-Insights Report: {session.name}",
                     executive_summary=" ".join(summary), key_findings=findings,
                     recommendations=recs, generated_by="rule-based (offline mode)")
