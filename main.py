"""
Entrypoint for the trading agent worker.
Parses --asset from goal.yaml (override with --asset flag). Starts the loop.
schema_version: 1
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

from loop import run_loop


def main():
    parser = argparse.ArgumentParser(
        description="Self-improving trading agent worker"
    )
    parser.add_argument(
        "--asset",
        action="append",
        dest="assets",
        help="Asset to trade (repeatable). Overrides goal.yaml.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one iteration of the loop and exit.",
    )
    args = parser.parse_args()

    # Determine assets
    goal_path = Path(os.environ.get("GOAL_PATH", Path(__file__).parent / "goal.yaml"))
    with open(goal_path) as f:
        goal = yaml.safe_load(f)

    assets = args.assets if args.assets else goal.get("assets", ["BTC/USDT"])
    mode = goal.get("mode", "paper")

    print(f"[main] Assets: {assets}")
    print(f"[main] Mode: {mode}")
    print(f"[main] PID: {os.getpid()}")

    max_iter = 1 if args.once else None
    asyncio.run(run_loop(assets, max_iterations=max_iter))


if __name__ == "__main__":
    main()
