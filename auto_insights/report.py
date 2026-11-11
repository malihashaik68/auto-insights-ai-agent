"""Turn an AnalysisSession into deliverables: HTML dashboard, Markdown report,
Excel workbook (Power BI / Excel ready) and the cleaned CSV."""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.io as pio

from .data_tools import AnalysisSession

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Narrative:
    """The written part of the report (from the LLM, or rule-based offline)."""
    title: str
    executive_summary: str
    key_findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    generated_by: str = "rule-based"


def _figure(session: AnalysisSession, spec: dict):
    df = session.df
    kind, x, y, color = spec["kind"], spec["x"], spec.get("y"), spec.get("color")
    freq = spec.get("freq")
    if freq and session.time_col == x and y:
        df = df.set_index(x)[[y]].resample(freq).mean().reset_index()
    elif kind == "scatter" and len(df) > 8000:
        df = df.sample(8000, random_state=0)  # keep the HTML file light
    if kind == "line":
        fig = px.line(df, x=x, y=y, color=color)
    elif kind == "scatter":
        fig = px.scatter(df, x=x, y=y, color=color, opacity=0.4)
    elif kind == "histogram":
        fig = px.histogram(df, x=x, color=color, nbins=60)
    else:
        fig = px.bar(df, x=x, y=y, color=color)
    fig.update_layout(title=spec["title"], template="plotly_white", height=380,
                      margin=dict(l=40, r=20, t=50, b=40))
    return fig


def _kpi_cards(kpis: dict) -> str:
    labels = [
        ("total_energy", "Total energy", kpis.get("energy_unit", "")),
        ("capacity_factor_pct", "Capacity factor", "%"),
        ("performance_ratio_pct", "Performance ratio", "%"),
        ("availability_pct", "Availability", "%"),
        ("data_coverage_pct", "Data coverage", "%"),
        ("underperforming_intervals", "Under-performing intervals", ""),
    ]
    kpis = dict(kpis)
    if kpis.get("energy_unit") == "kWh" and (kpis.get("total_energy") or 0) >= 1e5:
        kpis["total_energy"], kpis["energy_unit"] = kpis["total_energy"] / 1000, "MWh"
        labels[0] = ("total_energy", "Total energy", "MWh")
    cards = []
    for key, label, unit in labels:
        if key in kpis and kpis[key] is not None:
            val = kpis[key]
            val = f"{val:,.0f}" if isinstance(val, (int, float)) and abs(val) >= 1000 else val
            cards.append(f'<div class="kpi"><div class="v">{val}<span>{unit}</span></div>'
                         f'<div class="l">{label}</div></div>')
    return "".join(cards)


def build_dashboard(session: AnalysisSession, narrative: Narrative, out: Path) -> Path:
    findings = sorted(session.findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 3))
    # Embed plotly.js once (first chart) so the dashboard works offline.
    chart_divs = [pio.to_html(_figure(session, s), full_html=False, include_plotlyjs=(i == 0),
                              default_width="100%", config={"responsive": True})
                  for i, s in enumerate(session.charts)]
    esc = html.escape
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(narrative.title)}</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f5f7fa;color:#1d2733}}
header{{background:#0b3d3a;color:#fff;padding:24px 32px}}
header h1{{margin:0 0 6px;font-size:24px}} header p{{margin:0;opacity:.8}}
main{{max-width:1200px;margin:auto;padding:24px 16px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
.kpi{{background:#fff;border-radius:10px;padding:16px;box-shadow:0 1px 3px #0001}}
.kpi .v{{font-size:26px;font-weight:700;color:#0b6e4f}} .kpi .v span{{font-size:14px;margin-left:3px}}
.kpi .l{{font-size:13px;color:#5b6775}}
section{{background:#fff;border-radius:10px;padding:20px;margin-top:16px;box-shadow:0 1px 3px #0001}}
h2{{font-size:18px;margin-top:0}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{text-align:left;padding:8px;border-bottom:1px solid #e5e9ef}}
.sev{{padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}}
.high{{background:#fde2e1;color:#a4161a}} .medium{{background:#fff1d6;color:#8a5a00}}
.low{{background:#e3f1ff;color:#0b4f8a}}
.charts{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}
.charts>div{{min-width:0}}
@media(max-width:900px){{.charts{{grid-template-columns:1fr}}}}
footer{{text-align:center;color:#7a8592;font-size:12px;padding:24px}}
</style></head><body>
<header><h1>{esc(narrative.title)}</h1>
<p>Dataset: {esc(session.name)} · {len(session.df):,} rows · generated
{datetime.now():%Y-%m-%d %H:%M} · narrative: {esc(narrative.generated_by)}</p></header>
<main>
<div class="kpis">{_kpi_cards(session.kpis)}</div>
<section><h2>Executive summary</h2><p>{esc(narrative.executive_summary)}</p>
<h2>Key findings</h2><ul>{"".join(f"<li>{esc(x)}</li>" for x in narrative.key_findings)}</ul>
<h2>Recommendations</h2><ul>{"".join(f"<li>{esc(x)}</li>" for x in narrative.recommendations)}</ul>
</section>
<section><h2>Data quality findings</h2><table>
<tr><th>Severity</th><th>Check</th><th>Rows</th><th>Detail</th></tr>
{"".join(f'<tr><td><span class="sev {f.severity}">{f.severity}</span></td><td>{esc(f.check)}</td>'
         f'<td>{f.rows_affected:,}</td><td>{esc(f.description)}</td></tr>' for f in findings)}
</table>
<h2 style="margin-top:20px">Cleaning steps applied</h2>
<ul>{"".join(f"<li>{esc(x)}</li>" for x in session.cleaning_log) or "<li>None</li>"}</ul></section>
<section><h2>Charts</h2><div class="charts">{"".join(chart_divs)}</div></section>
</main><footer>Generated by Auto-Insights AI Agent</footer></body></html>"""
    path = out / "dashboard.html"
    path.write_text(page, encoding="utf-8")
    return path


def build_markdown(session: AnalysisSession, narrative: Narrative, out: Path) -> Path:
    lines = [f"# {narrative.title}", "",
             f"_Dataset: {session.name} · {len(session.df):,} rows · narrative: "
             f"{narrative.generated_by}_", "", "## Executive summary", "",
             narrative.executive_summary, "", "## KPIs", "", "| KPI | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in session.kpis.items() if not isinstance(v, str)]
    lines += ["", "## Key findings", ""] + [f"- {x}" for x in narrative.key_findings]
    lines += ["", "## Data quality findings", "", "| Severity | Check | Rows | Detail |",
              "|---|---|---|---|"]
    for f in sorted(session.findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 3)):
        lines.append(f"| {f.severity} | {f.check} | {f.rows_affected:,} | {f.description} |")
    lines += ["", "## Cleaning steps", ""] + [f"- {x}" for x in session.cleaning_log]
    lines += ["", "## Recommendations", ""] + [f"- {x}" for x in narrative.recommendations]
    path = out / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_excel(session: AnalysisSession, out: Path) -> Path:
    """Workbook with KPI, findings and monthly-trend sheets for Excel / Power BI."""
    path = out / "insights.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        pd.DataFrame([{"kpi": k, "value": v} for k, v in session.kpis.items()]) \
            .to_excel(xl, sheet_name="KPIs", index=False)
        pd.DataFrame([f.to_dict() for f in session.findings] or [{}]) \
            .to_excel(xl, sheet_name="DataQuality", index=False)
        for col, info in session.trends.get("series", {}).items():
            pd.Series(info["by_period"], name=col).rename_axis("period").reset_index() \
                .to_excel(xl, sheet_name=col[:31].replace("/", "-"), index=False)
    return path


def build_all(session: AnalysisSession, narrative: Narrative, out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    session.df.to_csv(out / "cleaned_data.csv", index=False)
    (out / "analysis.json").write_text(json.dumps(
        {**session.summary_state(), "trends": session.trends, "eda": session.eda},
        indent=2, default=str), encoding="utf-8")
    return {
        "dashboard": str(build_dashboard(session, narrative, out)),
        "report": str(build_markdown(session, narrative, out)),
        "excel": str(build_excel(session, out)),
        "cleaned_data": str(out / "cleaned_data.csv"),
    }
