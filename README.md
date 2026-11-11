# Auto-Insights AI Agent

![tests](https://github.com/malihashaik68/auto-insights-ai-agent/actions/workflows/tests.yml/badge.svg)

An LLM-powered agent that analyses a dataset end to end without a human in the loop. It profiles the data, handles missing values, flags inconsistencies, runs exploratory data analysis, and produces a KPI dashboard and report.

The demo runs on **real wind turbine SCADA data**: one year of 10-minute readings from a 3.6 MW turbine. This is the kind of operational data used to monitor wind fleets.

![Dashboard](docs/dashboard.png)

## What it does

| Step | What happens |
|---|---|
| **Profile** | Detects column types, the timestamp column, the sampling interval and the time range |
| **Missing values** | Finds empty cells *and* missing timestamps. It fills short gaps by time interpolation and leaves long outages empty, so no data is invented |
| **Inconsistencies** | Runs generic checks (duplicates, negatives, z-score outliers, flat-lined sensors), then the agent writes **domain rules** such as "no output although wind is above cut-in speed" |
| **EDA** | Distributions, skew, IQR outliers, strongest correlations |
| **KPIs** | Energy, capacity factor, performance ratio vs. expected output, availability, under-performing intervals |
| **Trends** | Monthly, weekly or daily aggregation with best/worst periods and the linear trend |
| **Deliverables** | Interactive HTML dashboard, Markdown report, Excel workbook (KPIs, data quality, trends; ready for Power BI or Excel), cleaned CSV |

## How the agent works

```
           ┌──────────────────────────────────────────────┐
 dataset → │ Claude (claude-opus-5-5) decides the next step │ ← system prompt: "act like a senior analyst"
           └───────────────┬──────────────────────────────┘
                           │ tool call (JSON)
                           ▼
           ┌──────────────────────────────────────────────┐
           │ Python tools (pandas): profile, missing values,│
           │ check_rule, KPIs, trends, charts ...           │
           └───────────────┬──────────────────────────────┘
                           │ tool result (JSON numbers)
                           └──────── back to Claude ─── repeat until write_report
```

* **The LLM decides, pandas calculates.** Claude chooses which tools to run, writes the domain rules and writes the narrative. Every number comes from deterministic Python code, so the model cannot make up statistics.
* **Hand-written tool-use loop** (`auto_insights/agent.py`), with no framework, so each step is visible and testable.
* **Safe rule language:** rules Claude writes are restricted to column names, numbers and comparison operators. Function calls and code are rejected (see the tests).
* **Error recovery:** if a tool fails (for example, a wrong column name), the error goes back to Claude as a tool result and it corrects itself.
* **Offline mode:** without an API key, the same tools run in a fixed order with a rule-based narrative. The project always runs, and the tests need no key.

## Results on the wind turbine dataset

From `docs/sample_report.md` (offline mode, 2018 data):

| KPI | Value |
|---|---|
| Energy produced | **11,017 MWh** |
| Capacity factor | **36.3 %** |
| Performance ratio (actual vs. theoretical power curve) | **87.6 %** (12.4 % of expected output lost) |
| Availability (producing when wind allowed) | **95.6 %** |
| Data coverage | **96.3 %** (2,030 missing 10-minute readings) |

Data-quality findings:
* **1,805 intervals with zero output even though wind was ≥ 4 m/s.** These look like downtime or curtailment, and they are the biggest source of lost energy.
* 728 intervals producing less than half of the theoretical power curve
* 57 negative power readings (the turbine drawing grid power while idle)
* July was the weakest month: average wind of 4.95 m/s and average output of 477 kW

## Quick start

```bash
git clone https://github.com/malihashaik68/auto-insights-ai-agent.git
cd auto-insights-ai-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python data/download_data.py
```

Offline (no API key needed):

```bash
python -m auto_insights data/wind_turbine_scada.csv --preset wind_turbine
```

With the Claude agent:

```bash
cp .env.example .env        # then paste your Anthropic API key into .env
python -m auto_insights data/wind_turbine_scada.csv --goal "Where is this turbine losing energy?"
```

It works on any CSV:

```bash
python -m auto_insights path/to/sales.csv --goal "Why did revenue drop in Q3?"
```

Outputs are written to `outputs/`: `dashboard.html`, `report.md`, `insights.xlsx`, `cleaned_data.csv` and `analysis.json`.

## Tests

```bash
pytest -q
```

The tests cover profiling, gap detection and filling, rule checks (including rejecting injected code), KPIs, the full offline pipeline, and the agent loop using a fake Claude client. GitHub Actions runs them on every push.

## Project structure

```
auto_insights/
  agent.py        Claude tool-use loop, tool definitions, system prompt
  data_tools.py   pandas analysis tools (the numbers)
  offline.py      rule-based pipeline + wind-turbine preset
  report.py       HTML dashboard, Markdown report, Excel workbook
  __main__.py     command-line interface
data/download_data.py   fetches the public dataset
tests/                  pytest suite
```

## Tech stack

Python · Anthropic Claude API (tool use) · pandas · NumPy · Plotly · openpyxl (Excel / Power BI export) · pytest · GitHub Actions

## Data source

"Wind Turbine Scada Dataset": a turbine in Turkey, 2018, 10-minute SCADA readings (active power, wind speed, theoretical power curve, wind direction). It was originally published on Kaggle; this project downloads a public GitHub mirror.
