"""Command-line entry point.

    python -m auto_insights data/wind_turbine_scada.csv --preset wind_turbine
    python -m auto_insights my_data.csv --goal "Find why sales dropped in Q3"
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from dotenv import load_dotenv

from .data_tools import AnalysisSession
from .offline import PRESETS, run_offline
from .report import build_all


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="auto_insights", description="LLM-powered analysis agent")
    p.add_argument("csv", help="Path to a CSV file")
    p.add_argument("--out", default="outputs", help="Output folder")
    p.add_argument("--goal", default="", help="Business question for the agent")
    p.add_argument("--context", default="", help="Extra domain context for the agent")
    p.add_argument("--preset", choices=sorted(PRESETS), help="Known dataset type (offline rules)")
    p.add_argument("--time-col", default=None, help="Timestamp column (auto-detected if omitted)")
    p.add_argument("--offline", action="store_true", help="Skip the LLM; rule-based pipeline")
    args = p.parse_args(argv)

    start = time.time()
    session = AnalysisSession.from_csv(args.csv, time_col=args.time_col)
    print(f"Loaded {args.csv}: {len(session.df):,} rows, time column = {session.time_col}")

    use_llm = not args.offline and (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
    if use_llm:
        from .agent import InsightsAgent
        print("Running Claude agent...")
        narrative = InsightsAgent(session).run(goal=args.goal, context=args.context)
    else:
        if not args.offline:
            print("No ANTHROPIC_API_KEY found - running offline (rule-based) mode.")
        narrative = run_offline(session, PRESETS.get(args.preset) if args.preset else None)

    paths = build_all(session, narrative, args.out)
    print(f"\nDone in {time.time() - start:.1f}s. "
          f"{len(session.findings)} data-quality findings.")
    for kind, path in paths.items():
        print(f"  {kind:13s} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
