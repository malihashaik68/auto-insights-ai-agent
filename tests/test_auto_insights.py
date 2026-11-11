from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from auto_insights.agent import InsightsAgent
from auto_insights.data_tools import AnalysisSession
from auto_insights.offline import run_offline
from auto_insights.report import build_all


@pytest.fixture
def session() -> AnalysisSession:
    ts = pd.date_range("2024-01-01", periods=200, freq="10min")
    rng = np.random.default_rng(0)
    wind = rng.uniform(0, 15, len(ts))
    power = np.clip((wind - 3) * 300, 0, 3000)
    df = pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%d %H:%M"), "wind": wind,
                       "power": power, "expected": power})
    df.loc[10, ["power", "wind"]] = [-5, 2]  # negative reading in calm wind
    df.loc[20:24, "power"] = 0        # downtime while windy
    df.loc[20:24, "wind"] = 10
    df = df.drop(index=[50, 51, 120])  # gaps in the time series
    return AnalysisSession(raw=df, name="test")


def test_detects_time_column_and_interval(session):
    profile = session.profile_dataset()
    assert profile["time_column"] == "timestamp"
    assert profile["sampling_interval"] == "10-minute"
    assert profile["rows"] == 197


def test_missing_timestamps_found_and_filled(session):
    missing = session.analyze_missing_values()
    assert missing["missing_timestamps"] == 3
    result = session.handle_missing_values("interpolate", max_gap=3)
    assert result["timestamps_added"] == 3
    assert result["cells_still_missing"] == 0
    assert len(session.df) == 200


def test_long_gaps_are_not_interpolated(session):
    result = session.handle_missing_values("interpolate", max_gap=1)
    assert result["cells_still_missing"] > 0  # the 2-interval gap stays empty


def test_check_rule_counts_violations(session):
    r = session.check_rule("zero_output_in_wind", "`power` <= 0 and `wind` >= 4",
                           "No output in wind", "high")
    assert r["rows_violating"] == 5
    assert session.findings[0].severity == "high"


@pytest.mark.parametrize("bad", ["__import__('os')", "`power`.sum() > 0", "@x > 1", "abs(`power`) > 1"])
def test_check_rule_rejects_code(session, bad):
    with pytest.raises(ValueError):
        session.check_rule("bad", bad, "should fail")


def test_kpis(session):
    k = session.compute_kpis("power", "expected", rated_capacity=3000, unit="kW")
    assert 0 < k["capacity_factor_pct"] < 100
    assert k["energy_unit"] == "kWh"
    assert k["performance_ratio_pct"] <= 100


def test_offline_pipeline_writes_outputs(session, tmp_path):
    narrative = run_offline(session)
    paths = build_all(session, narrative, tmp_path)
    for p in paths.values():
        assert (tmp_path / p.split("/")[-1]).exists()
    assert "Executive summary" in (tmp_path / "report.md").read_text()


# ---------------------------------------------------------------- agent loop
def _tool_use(id_, name, args):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


class FakeClient:
    """Replays a scripted conversation instead of calling the API."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        content = self.turns.pop(0)
        return SimpleNamespace(content=content, stop_reason="tool_use", model="fake-model")


def test_agent_runs_tools_and_returns_narrative(session):
    client = FakeClient([
        [_tool_use("1", "profile_dataset", {}), _tool_use("2", "analyze_missing_values", {})],
        [_tool_use("3", "check_rule", {"name": "neg", "condition": "`power` < 0",
                                        "description": "negative power"}),
         _tool_use("4", "check_rule", {"name": "bad", "condition": "`nope` > 1",
                                        "description": "unknown column"})],
        [_tool_use("5", "write_report", {"title": "T", "executive_summary": "S",
                                          "key_findings": ["f"], "recommendations": ["r"]})],
    ])
    narrative = InsightsAgent(session, client=client, log=lambda _: None).run()
    assert narrative.title == "T"
    assert {f.check for f in session.findings} == {"missing_timestamps", "neg"}
    # Both results of a turn go back in one user message; errors are flagged, not raised.
    messages = client.requests[2]["messages"]
    results = next(m["content"] for m in reversed(messages) if m["role"] == "user")
    assert len(results) == 2
    assert results[1]["is_error"] is True
