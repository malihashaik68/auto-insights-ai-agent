"""The LLM agent: Claude decides which analysis tools to run, in what order,
with which domain rules, and then writes the narrative for the report.

The loop is written by hand (no framework) so every step is visible:
  1. send the conversation + tool definitions to Claude
  2. if Claude asks for tools, run them with pandas and send results back
  3. repeat until Claude calls `write_report`
"""
from __future__ import annotations

import json
from typing import Any, Callable

import anthropic

from .data_tools import AnalysisSession
from .report import Narrative

MODEL = "claude-opus-5-5"
MAX_TURNS = 25

SYSTEM_PROMPT = """You are Auto-Insights, an autonomous data-analysis agent.
You receive a dataset you have never seen and must deliver a trustworthy analysis
without a human in the loop.

Work like a careful senior analyst:
1. Profile the dataset first and work out what each column means.
2. Measure missing data (including gaps in the time series) and choose a sensible
   handling strategy. Never invent data for long outages.
3. Run the generic inconsistency checks, then use your domain knowledge to write
   specific rules with check_rule (physically impossible values, sensor faults,
   readings that contradict each other). Flag suspicious rows with flag_rows
   instead of deleting them.
4. Run EDA, compute KPIs and analyse trends.
5. Load the data into SQL (sql_schema) and write 2-4 SQL queries that break the
   KPIs down (e.g. by month, by operating band, worst days). Save the useful ones
   to the report with save=true.
6. Queue 4-6 charts that a manager would actually look at.
7. Finish by calling write_report exactly once.

Rules: every number you report must come from a tool result. Use the exact column
names returned by profile_dataset, wrapped in backticks inside conditions.
Keep the report concise and specific: name periods, quantities and percentages."""

TOOLS: list[dict] = [
    {"name": "profile_dataset",
     "description": "Shape, column types, summary statistics, sampling interval and time range.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "analyze_missing_values",
     "description": "Missing cells per column and missing timestamps / longest gaps in a time series.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "handle_missing_values",
     "description": "Fill or remove missing data. 'interpolate' fills only short gaps "
                    "(<= max_gap intervals) and leaves longer outages empty.",
     "input_schema": {"type": "object", "properties": {
         "strategy": {"type": "string", "enum": ["interpolate", "drop", "median", "reindex_only"]},
         "max_gap": {"type": "integer", "description": "Longest gap (in intervals) to interpolate."}},
         "required": ["strategy"]}},
    {"name": "detect_inconsistencies",
     "description": "Generic checks: duplicate rows/timestamps, unexpected negatives, extreme "
                    "outliers (z-score) and flat-lined sensors.",
     "input_schema": {"type": "object", "properties": {
         "z_threshold": {"type": "number"}}}},
    {"name": "check_rule",
     "description": "Count rows that VIOLATE a domain rule. `condition` selects the bad rows, "
                    "using backticked column names, numbers, comparison/arithmetic operators and "
                    "and/or/not only. Example: \"`Power` <= 0 and `Wind Speed` > 4\".",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "snake_case rule id"},
         "condition": {"type": "string"},
         "description": {"type": "string", "description": "What the rule means in plain English."},
         "severity": {"type": "string", "enum": ["high", "medium", "low"]}},
         "required": ["name", "condition", "description"]}},
    {"name": "flag_rows",
     "description": "Add a boolean column marking rows that match a condition (rows are kept).",
     "input_schema": {"type": "object", "properties": {
         "flag_name": {"type": "string", "description": "e.g. flag_downtime"},
         "condition": {"type": "string"}},
         "required": ["flag_name", "condition"]}},
    {"name": "run_eda",
     "description": "Distribution stats, skew, IQR outliers and strongest correlations.",
     "input_schema": {"type": "object", "properties": {
         "columns": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "compute_kpis",
     "description": "KPIs for an output column, optionally against an expected/target column: "
                    "energy, capacity factor, performance ratio, availability, under-performance.",
     "input_schema": {"type": "object", "properties": {
         "actual_col": {"type": "string"},
         "expected_col": {"type": "string"},
         "rated_capacity": {"type": "number"},
         "unit": {"type": "string", "description": "kW, MW or any other unit"}},
         "required": ["actual_col"]}},
    {"name": "analyze_trends",
     "description": "Aggregate columns by period (h, D, W, ME=month, QE=quarter); best/worst "
                    "periods and linear trend.",
     "input_schema": {"type": "object", "properties": {
         "value_cols": {"type": "array", "items": {"type": "string"}},
         "freq": {"type": "string", "enum": ["h", "D", "W", "ME", "QE"]}},
         "required": ["value_cols"]}},
    {"name": "sql_schema",
     "description": "Show the SQLite table `readings` (snake_case column names) built from the "
                    "current cleaned data, including flag columns.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_sql",
     "description": "Run one read-only SQLite SELECT against table `readings`. Returns up to 50 "
                    "rows. Set save=true with a title to show the result as a table in the report.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"},
         "title": {"type": "string"},
         "save": {"type": "boolean"}},
         "required": ["query"]}},
    {"name": "add_chart",
     "description": "Queue a chart for the dashboard. For time-series lines pass freq "
                    "(e.g. 'D' or 'W') to resample.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["line", "scatter", "histogram", "bar"]},
         "x": {"type": "string"}, "y": {"type": "string"},
         "title": {"type": "string"}, "color": {"type": "string"},
         "freq": {"type": "string", "enum": ["h", "D", "W", "ME"]}},
         "required": ["kind", "x", "title"]}},
    {"name": "write_report",
     "description": "Final step. Provide the narrative for the dashboard and report.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"},
         "executive_summary": {"type": "string", "description": "3-5 sentences for a manager."},
         "key_findings": {"type": "array", "items": {"type": "string"}},
         "recommendations": {"type": "array", "items": {"type": "string"}}},
         "required": ["title", "executive_summary", "key_findings", "recommendations"]}},
]


def _dispatch(session: AnalysisSession, name: str, args: dict) -> dict:
    handlers: dict[str, Callable[..., dict]] = {
        "profile_dataset": session.profile_dataset,
        "analyze_missing_values": session.analyze_missing_values,
        "handle_missing_values": session.handle_missing_values,
        "detect_inconsistencies": session.detect_inconsistencies,
        "check_rule": session.check_rule,
        "flag_rows": session.flag_rows,
        "run_eda": session.run_eda,
        "compute_kpis": session.compute_kpis,
        "analyze_trends": session.analyze_trends,
        "add_chart": session.add_chart,
        "sql_schema": session.sql_schema,
        "run_sql": session.run_sql,
    }
    if name not in handlers:
        raise ValueError(f"Unknown tool {name}")
    return handlers[name](**args)


def user_prompt(session: AnalysisSession, goal: str = "", context: str = "") -> str:
    return (f"Analyse the dataset '{session.name}' end to end and produce the report."
            + (f"\n\nBusiness goal: {goal}" if goal else "")
            + (f"\n\nContext from the user: {context}" if context else ""))


def narrative_from(args: dict, generated_by: str) -> Narrative:
    """Build the report narrative from the write_report tool arguments."""
    return Narrative(
        title=args.get("title", "Auto-Insights Report"),
        executive_summary=args.get("executive_summary", ""),
        key_findings=[str(x) for x in args.get("key_findings", [])],
        recommendations=[str(x) for x in args.get("recommendations", [])],
        generated_by=generated_by,
    )


class InsightsAgent:
    """Agent backed by Anthropic Claude."""

    def __init__(self, session: AnalysisSession, client: anthropic.Anthropic | None = None,
                 model: str = MODEL, log: Callable[[str], None] = print):
        self.session = session
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.log = log
        self.tool_calls: list[dict] = []

    def run(self, goal: str = "", context: str = "") -> Narrative:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_prompt(self.session, goal, context)}]

        for turn in range(1, MAX_TURNS + 1):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": "medium"},
                # If the model declines a request, the API retries it on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                raise RuntimeError("The model declined the request.")
            # Keep the full content (including thinking blocks) in the history.
            messages.append({"role": "assistant", "content": response.content})

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    raise RuntimeError("Response hit max_tokens before finishing.")
                messages.append({"role": "user", "content":
                                 "Please finish by calling write_report."})
                continue

            results = []
            for block in tool_uses:
                args = dict(block.input)
                self.tool_calls.append({"tool": block.name, "input": args})
                self.log(f"  [turn {turn}] {block.name}({_short(args)})")
                if block.name == "write_report":
                    return narrative_from(
                        args, f"{response.model} (agent, {len(self.tool_calls)} tool calls)")
                try:
                    output = _dispatch(self.session, block.name, args)
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": json.dumps(output, default=str)[:20000]})
                except Exception as exc:  # report the error to the model so it can recover
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": f"Error: {exc}", "is_error": True})
            messages.append({"role": "user", "content": results})

        raise RuntimeError(f"Agent did not finish within {MAX_TURNS} turns.")


def _short(args: dict, limit: int = 110) -> str:
    text = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return text if len(text) <= limit else text[:limit] + "..."
