"""Auto-Insights interactive dashboard (Streamlit).

Run locally:   streamlit run app.py
Deploy:        share.streamlit.io -> New app -> this repo -> app.py
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import anthropic
import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from auto_insights.agent import InsightsAgent  # noqa: E402
from auto_insights.data_tools import AnalysisSession  # noqa: E402
from auto_insights.offline import PRESETS, run_offline  # noqa: E402
from auto_insights.report import build_all, make_figure  # noqa: E402

SAMPLE = ROOT / "data" / "wind_turbine_scada.csv"
SEV_ICON = {"high": "🔴", "medium": "🟠", "low": "🔵"}

st.set_page_config(page_title="Auto-Insights AI Agent", page_icon="📊", layout="wide")


# ---------------------------------------------------------------- data access
def _secret(name: str) -> str:
    try:
        value = st.secrets.get(name, "") or os.getenv(name, "")
    except Exception:  # no secrets.toml
        value = os.getenv(name, "")
    return str(value).strip().strip('"').strip("'").strip()


@st.cache_data(show_spinner="Downloading sample dataset...")
def load_sample() -> pd.DataFrame:
    if not SAMPLE.exists():
        sys.path.insert(0, str(ROOT / "data"))
        from download_data import main as download
        download()
    return pd.read_csv(SAMPLE, encoding="utf-8-sig")


@st.cache_resource(show_spinner="Analysing the sample dataset...")
def sample_offline_run():
    session = AnalysisSession(raw=load_sample(), name="wind_turbine_scada")
    narrative = run_offline(session, PRESETS["wind_turbine"])
    return session, narrative, []


def run_analysis(raw: pd.DataFrame, name: str, use_agent: bool, api_key: str, goal: str,
                 preset: str | None, provider: str = "gemini"):
    session = AnalysisSession(raw=raw.copy(), name=name)
    if not use_agent:
        return session, run_offline(session, PRESETS.get(preset) if preset else None), []
    log = lambda msg: st.write(f"`{msg.strip()}`")  # noqa: E731
    label = "Gemini" if provider == "gemini" else "Claude"
    with st.status(f"{label} agent is analysing the data...", expanded=True) as status:
        if provider == "gemini":
            from auto_insights.agent_gemini import GeminiInsightsAgent
            agent = GeminiInsightsAgent(session, api_key=api_key, log=log)
        else:
            agent = InsightsAgent(session, client=anthropic.Anthropic(api_key=api_key), log=log)
        narrative = agent.run(goal=goal)
        status.update(label=f"Agent finished: {len(agent.tool_calls)} tool calls",
                      state="complete", expanded=False)
    return session, narrative, agent.tool_calls


# --------------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("📊 Auto-Insights")
    st.caption("LLM-powered agent that profiles, cleans and analyses a dataset end to end.")
    source = st.radio("Dataset", ["Sample: wind turbine SCADA (2018)", "Upload a CSV"])
    upload = st.file_uploader("CSV file", type="csv") if source == "Upload a CSV" else None

    mode = st.radio("Analysis mode", ["Fast (rule-based)", "AI agent"],
                    help="Fast mode runs the same tools in a fixed order. "
                         "Agent mode lets the LLM plan the analysis, write rules and SQL.")
    api_key, goal, provider = "", "", "gemini"
    if mode == "AI agent":
        provider = st.radio("LLM provider", ["gemini", "claude"], horizontal=True,
                            format_func={"gemini": "Google Gemini", "claude": "Anthropic Claude"}.get)
        secret_name = "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"
        owner_key, access_code = _secret(secret_name), _secret("AGENT_ACCESS_CODE")
        if owner_key and access_code:
            code = st.text_input("Access code", type="password",
                                 help="Ask the owner for the demo access code.")
            if code.strip() == access_code:
                api_key = owner_key
                st.caption(f"✅ Using the {secret_name} saved in the app's Secrets.")
            elif code:
                st.caption("❌ Access code does not match.")
        elif owner_key:
            api_key = owner_key
            st.caption(f"✅ Using the {secret_name} saved in the app's Secrets.")
        if not api_key:
            api_key = st.text_input(f"…or your own {'Gemini' if provider == 'gemini' else 'Anthropic'} "
                                    "API key", type="password").strip()
            if not owner_key:
                st.caption(f"No {secret_name} found in the app's Secrets.")
        goal = st.text_input("Business question (optional)",
                             placeholder="Where is this turbine losing energy?")
    run = st.button("Run analysis", type="primary", use_container_width=True)
    st.divider()
    st.markdown("[Source code on GitHub](https://github.com/malihashaik68/auto-insights-ai-agent)")

# ----------------------------------------------------------------------- run
if run:
    try:
        if source == "Upload a CSV":
            if upload is None:
                st.sidebar.error("Upload a CSV first.")
                st.stop()
            raw, name, preset = pd.read_csv(upload, encoding="utf-8-sig"), Path(upload.name).stem, None
        else:
            raw, name, preset = load_sample(), "wind_turbine_scada", "wind_turbine"
        use_agent = mode == "AI agent"
        if use_agent and not api_key:
            st.sidebar.error("Agent mode needs an access code or an API key.")
            st.stop()
        st.session_state.result = run_analysis(raw, name, use_agent, api_key, goal, preset,
                                               provider)
    except anthropic.AuthenticationError:
        st.error("That API key was rejected by Anthropic.")
        st.stop()
    except Exception as exc:
        text = str(exc)
        if "API_KEY_INVALID" in text or "API key not valid" in text:
            st.error("Google rejected the Gemini API key. Create a new key at "
                     "aistudio.google.com/apikey, copy it with the copy button, and paste it "
                     "again (in Secrets: `GEMINI_API_KEY = \"AIza...\"`).")
            if api_key:
                st.caption(f"The key the app sent starts with `{api_key[:4]}` and is "
                           f"{len(api_key)} characters long (Gemini keys start with `AIza` "
                           f"and are 39 characters).")
        else:
            st.error(f"Analysis failed: {text[:500]}")
        st.stop()

session, narrative, tool_calls = st.session_state.get("result") or sample_offline_run()

# ---------------------------------------------------------------------- page
st.title(narrative.title)
st.caption(f"{len(session.df):,} rows · {len(session.findings)} data-quality findings · "
           f"narrative: {narrative.generated_by}")

k = session.kpis
cards = [("Total energy", k.get("total_energy"), k.get("energy_unit", "")),
         ("Capacity factor", k.get("capacity_factor_pct"), "%"),
         ("Performance ratio", k.get("performance_ratio_pct"), "%"),
         ("Availability", k.get("availability_pct"), "%"),
         ("Data coverage", k.get("data_coverage_pct"), "%")]
cards = [c for c in cards if c[1] is not None]
if cards and cards[0][0] == "Total energy" and cards[0][2] == "kWh" and cards[0][1] >= 1e5:
    cards[0] = ("Total energy", cards[0][1] / 1000, "MWh")
for col, (label, value, unit) in zip(st.columns(max(len(cards), 1)), cards):
    if unit == "%":
        col.metric(label, f"{value:.1f}%")
    else:
        col.metric(f"{label} ({unit})" if unit else label, f"{value:,.0f}")

tabs = st.tabs(["Overview", "Data quality", "Charts", "SQL", "Agent trace", "Data & downloads"])

with tabs[0]:
    st.subheader("Executive summary")
    st.write(narrative.executive_summary)
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Key findings")
        for f in narrative.key_findings:
            st.markdown(f"- {f}")
    with c2:
        st.subheader("Recommendations")
        for r in narrative.recommendations:
            st.markdown(f"- {r}")

with tabs[1]:
    st.subheader("Findings")
    if session.findings:
        st.dataframe(pd.DataFrame([{"severity": f"{SEV_ICON.get(f.severity, '')} {f.severity}",
                                    "check": f.check, "rows": f.rows_affected,
                                    "detail": f.description} for f in session.findings]),
                     hide_index=True, use_container_width=True)
    else:
        st.info("No data-quality issues found.")
    st.subheader("Cleaning steps applied")
    for step in session.cleaning_log or ["None"]:
        st.markdown(f"- {step}")

with tabs[2]:
    cols = st.columns(2)
    for i, spec in enumerate(session.charts):
        with cols[i % 2]:
            st.plotly_chart(make_figure(session, spec), use_container_width=True, theme=None)
    if not session.charts:
        st.info("No charts were generated.")

with tabs[3]:
    for r in session.sql_results:
        st.subheader(r["title"])
        st.code(r["query"], language="sql")
        st.dataframe(r["result"], hide_index=True, use_container_width=True)
    st.subheader("Try your own SQL")
    schema = session.sql_schema()
    st.caption(f"Table `{schema['table']}` · columns: " + ", ".join(f"`{c}`" for c in schema["columns"]))
    default = f"SELECT * FROM {schema['table']} LIMIT 10"
    query = st.text_area("Read-only SELECT query", value=default, height=140,
                         key=f"sql_{session.name}")
    if st.button("Run SQL"):
        try:
            st.dataframe(session._warehouse().query(query).head(1000), hide_index=True,
                         use_container_width=True)
        except ValueError as exc:
            st.error(str(exc))

with tabs[4]:
    if tool_calls:
        st.write(f"Claude made **{len(tool_calls)} tool calls**, in this order:")
        st.dataframe(pd.DataFrame([{"step": i + 1, "tool": c["tool"], "input": str(c["input"])}
                                   for i, c in enumerate(tool_calls)]),
                     hide_index=True, use_container_width=True)
    else:
        st.info("This result came from fast (rule-based) mode. Choose **AI agent** in the "
                "sidebar to see the LLM plan the analysis step by step.")

with tabs[5]:
    st.dataframe(session.df.head(500), use_container_width=True)
    out = ROOT / "outputs" / "app"
    paths = build_all(session, narrative, out)
    c1, c2, c3, c4 = st.columns(4)
    c1.download_button("Dashboard (HTML)", Path(paths["dashboard"]).read_bytes(), "dashboard.html")
    c2.download_button("Report (Markdown)", Path(paths["report"]).read_bytes(), "report.md")
    c3.download_button("Excel / Power BI", Path(paths["excel"]).read_bytes(), "insights.xlsx")
    buf = io.StringIO()
    session.df.to_csv(buf, index=False)
    c4.download_button("Cleaned data (CSV)", buf.getvalue(), "cleaned_data.csv")
