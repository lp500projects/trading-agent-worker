"""
Reflection cycle — analyses closed trades and proposes ONE variable change.
Two modes:
  --deterministic : Rule-based (Phase 5, pre-Hermes)
  --hermes         : Hermes-powered (Phase 7, production)
schema_version: 1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from score import TradeRecord, score_trades

STATE_DIR = Path(os.environ.get("STATE_DIR", Path(__file__).parent / "state"))
STRATEGY_PATH = STATE_DIR / "strategy.yaml"
TRADES_PATH = STATE_DIR / "trades.jsonl"
HYPOTHESES_PATH = STATE_DIR / "hypotheses.jsonl"
GOAL_PATH = Path(os.environ.get("GOAL_PATH", Path(__file__).parent / "goal.yaml"))


# ----- Variables the reflector is allowed to change (exactly ONE per cycle) -----
TUNABLE_VARIABLES = [
    ("entry.rsi_threshold", "lower = more entries, higher = fewer", (20, 45)),
    ("entry.volume_spike_factor", "lower = more entries, higher = fewer", (1.2, 4.0)),
    ("entry.trend_window", "shorter = faster signals, longer = slower", (20, 100)),
    ("exit.take_profit_pct", "lower = faster exits, higher = greedier", (1.0, 8.0)),
    ("exit.stop_loss_pct", "tighter = less risk, looser = more room", (0.5, 5.0)),
    ("exit.max_hold_hours", "shorter = faster turnover, longer = patience", (6, 120)),
    ("position.size_pct", "smaller = less risk per trade", (2.0, 25.0)),
    ("position.max_concurrent", "fewer = less exposure", (1, 6)),
]


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def save_yaml(path: Path, data: dict):
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def load_trade_records() -> list[TradeRecord]:
    if not TRADES_PATH.exists():
        return []
    records = []
    with open(TRADES_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("action") == "exit":
                records.append(TradeRecord(
                    asset=d.get("asset", ""),
                    entry_price=d.get("entry_price", 0),
                    exit_price=d.get("exit_price", 0),
                    pnl_pct=d.get("pnl_pct", 0),
                    hold_hours=d.get("hold_hours", 0),
                    entry_time=d.get("entry_time_utc", ""),
                    exit_time=d.get("exit_time_utc", ""),
                ))
    return records


def get_nested(d: dict, path: str):
    """Get nested dict value by dot-separated path, e.g. 'entry.rsi_threshold'."""
    keys = path.split(".")
    for k in keys:
        d = d[k]
    return d


def set_nested(d: dict, path: str, value):
    """Set nested dict value by dot-separated path."""
    keys = path.split(".")
    for k in keys[:-1]:
        d = d[k]
    d[keys[-1]] = value


def deterministic_reflection() -> dict:
    """Rule-based reflection. Changes exactly ONE variable based on score."""
    trades = load_trade_records()
    goal = load_yaml(GOAL_PATH)
    strategy = load_yaml(STRATEGY_PATH)

    if len(trades) < 5:
        print("[reflect] Not enough trades for reflection (need >= 5, have {})".format(len(trades)))
        return {"action": "skip", "reason": "insufficient_trades", "trade_count": len(trades)}

    # Score the current set
    result = score_trades(
        trades,
        target_return_30d_pct=goal["success"]["target_return_30d_pct"],
        max_drawdown_limit_pct=goal["failure"]["max_drawdown_pct"],
        min_sharpe=goal["success"]["min_sharpe"],
    )

    print(f"[reflect] Composite score: {result.composite_score}/100 ({result.grade})")
    print(f"[reflect] Return: {result.realised_return_30d_pct}% / target {result.target_return_30d_pct}%")
    print(f"[reflect] Drawdown: {result.max_drawdown_pct}% / limit {result.max_drawdown_limit_pct}%")
    print(f"[reflect] Sharpe: {result.sharpe_ratio} / min {result.min_sharpe}")

    # Deterministic rules: pick ONE variable to change
    chosen_var = None
    chosen_direction = None
    reasoning = ""

    if result.realised_return_30d_pct < result.target_return_30d_pct:
        # Underperforming — loosen entry to capture more trades
        chosen_var = "entry.rsi_threshold"
        old_val = get_nested(strategy, chosen_var)
        new_val = min(old_val + 2, 45)
        chosen_direction = "loosen"
        reasoning = (
            f"Realised return ({result.realised_return_30d_pct}%) below target "
            f"({result.target_return_30d_pct}%). Loosening RSI threshold from "
            f"{old_val} to {new_val} to capture more entries."
        )
    elif result.max_drawdown_pct > result.max_drawdown_limit_pct * 0.7:
        # Approaching drawdown limit — tighten
        chosen_var = "exit.stop_loss_pct"
        old_val = get_nested(strategy, chosen_var)
        new_val = max(old_val - 0.2, 0.5)
        chosen_direction = "tighten"
        reasoning = (
            f"Drawdown ({result.max_drawdown_pct}%) approaching limit "
            f"({result.max_drawdown_limit_pct}%). Tightening stop-loss from "
            f"{old_val}% to {new_val}%."
        )
    elif result.sharpe_ratio < result.min_sharpe * 0.8:
        # Low Sharpe — reduce position size
        chosen_var = "position.size_pct"
        old_val = get_nested(strategy, chosen_var)
        new_val = max(old_val - 1.0, 2.0)
        chosen_direction = "tighten"
        reasoning = (
            f"Sharpe ({result.sharpe_ratio}) well below min ({result.min_sharpe}). "
            f"Reducing position size from {old_val}% to {new_val}%."
        )
    else:
        # On track — small optimisation
        chosen_var = "exit.take_profit_pct"
        old_val = get_nested(strategy, chosen_var)
        if result.composite_score >= 70:
            new_val = old_val + 0.5
            chosen_direction = "loosen"
        else:
            new_val = old_val - 0.5
            chosen_direction = "tighten"
        reasoning = (
            f"Score {result.composite_score}/100. Adjusting take-profit from "
            f"{old_val}% to {new_val}% for fine-tuning."
        )

    # Apply the change
    old_version = strategy["version"]
    new_version = old_version + 1
    set_nested(strategy, chosen_var, new_val)
    strategy["version"] = new_version
    strategy["updated"] = datetime.now(timezone.utc).isoformat()

    # Save prior version
    prior_path = STATE_DIR / f"strategy_v{old_version:02d}.yaml"
    prior_strategy = load_yaml(STRATEGY_PATH)
    prior_strategy["version"] = old_version
    save_yaml(prior_path, prior_strategy)

    # Write new strategy
    save_yaml(STRATEGY_PATH, strategy)

    # Log hypothesis
    hypothesis = {
        "schema_version": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "version_from": old_version,
        "version_to": new_version,
        "variable_changed": chosen_var,
        "old_value": old_val,
        "new_value": new_val,
        "direction": chosen_direction,
        "mode": "deterministic",
        "score": result.composite_score,
        "grade": result.grade,
        "reasoning": reasoning,
        "trade_count": len(trades),
    }
    with open(HYPOTHESES_PATH, "a") as f:
        f.write(json.dumps(hypothesis) + "\n")

    print(f"[reflect] Strategy v{old_version} → v{new_version}")
    print(f"[reflect] Changed: {chosen_var} {old_val} → {new_val} ({chosen_direction})")
    print(f"[reflect] Reasoning: {reasoning}")
    print(f"[reflect] Prior saved to state/strategy_v{old_version:02d}.yaml")

    return hypothesis


def hermes_reflection() -> dict:
    """Hermes-powered reflection. Formats prompt, calls Hermes CLI, parses hypothesis."""
    trades = load_trade_records()
    goal = load_yaml(GOAL_PATH)
    strategy = load_yaml(STRATEGY_PATH)

    if len(trades) < 5:
        print("[reflect:hermes] Not enough trades (need >= 5, have {})".format(len(trades)))
        return {"action": "skip", "reason": "insufficient_trades", "trade_count": len(trades)}

    # Take latest 25 trades for the prompt
    recent_trades = trades[-25:]

    # Build the prompt for Hermes
    trade_summary = "\n".join(
        f"- {t.asset}: entry={t.entry_price:.2f} exit={t.exit_price:.2f} "
        f"pnl={t.pnl_pct:+.2f}% hold={t.hold_hours:.1f}h"
        for t in recent_trades
    )

    tunable_list = "\n".join(
        f"- `{path}` ({desc}, range {low}-{high})"
        for path, desc, (low, high) in TUNABLE_VARIABLES
    )

    prompt = f"""You are the reflection engine for a self-improving trading agent.

## Current Strategy (YAML)
```yaml
{yaml.dump(strategy, default_flow_style=False)}
```

## Goals
- Target return: {goal['success']['target_return_30d_pct']}% per 30 days
- Max drawdown: {goal['failure']['max_drawdown_pct']}%
- Min Sharpe: {goal['success']['min_sharpe']}

## Recent Trades ({len(recent_trades)} closed)
{trade_summary}

## Tunable Variables (change EXACTLY ONE)
{tunable_list}

Analyse the recent trades against the goals. Identify the single variable most likely
to improve the composite score (balancing return, drawdown, and Sharpe).

Respond with EXACTLY this JSON structure:
{{
  "variable_changed": "entry.rsi_threshold",
  "new_value": <number>,
  "reasoning": "<2-3 sentence explanation of why this specific change>"
}}

Rules:
- Change ONLY ONE variable.
- Stay within the stated range for that variable.
- If performance is near targets, prefer small adjustments.
- If far from targets, larger adjustments are acceptable.
- Never suggest changes that would increase risk when drawdown is high.
"""

    try:
        result = subprocess.run(
            ["hermes", "--print", prompt],
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout.strip()
    except FileNotFoundError:
        print("[reflect:hermes] Hermes CLI not found. Falling back to deterministic.")
        return deterministic_reflection()
    except subprocess.TimeoutExpired:
        print("[reflect:hermes] Hermes timed out. Falling back to deterministic.")
        return deterministic_reflection()

    # Parse Hermes response
    try:
        # Try to extract JSON from the output
        import re
        json_match = re.search(r'\{[^{}]*"variable_changed"[^{}]*\}', output, re.DOTALL)
        if json_match:
            proposal = json.loads(json_match.group(0))
        else:
            proposal = json.loads(output)
    except json.JSONDecodeError:
        print(f"[reflect:hermes] Could not parse Hermes output: {output[:300]}")
        print("[reflect:hermes] Falling back to deterministic.")
        return deterministic_reflection()

    # Validate the proposed change
    var_path = proposal.get("variable_changed", "")
    new_val = proposal.get("new_value")
    reasoning = proposal.get("reasoning", "No reasoning provided")

    valid_var = None
    for path, desc, (low, high) in TUNABLE_VARIABLES:
        if path == var_path:
            valid_var = (path, desc, low, high)
            break

    if valid_var is None:
        print(f"[reflect:hermes] Invalid variable: {var_path}. Falling back to deterministic.")
        return deterministic_reflection()

    _, _, low, high = valid_var
    if not (low <= new_val <= high):
        print(f"[reflect:hermes] Value {new_val} out of range [{low}, {high}]. Clamping.")
        new_val = max(low, min(high, new_val))

    # Apply the change
    old_val = get_nested(strategy, var_path)
    old_version = strategy["version"]
    new_version = old_version + 1

    set_nested(strategy, var_path, new_val)
    strategy["version"] = new_version
    strategy["updated"] = datetime.now(timezone.utc).isoformat()

    # Save prior version
    prior_path = STATE_DIR / f"strategy_v{old_version:02d}.yaml"
    prior_strategy = load_yaml(STRATEGY_PATH)
    prior_strategy["version"] = old_version
    save_yaml(prior_path, prior_strategy)

    # Write new strategy
    save_yaml(STRATEGY_PATH, strategy)

    # Log hypothesis
    hypothesis = {
        "schema_version": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "version_from": old_version,
        "version_to": new_version,
        "variable_changed": var_path,
        "old_value": old_val,
        "new_value": new_val,
        "direction": "loosen" if new_val > old_val else "tighten",
        "mode": "hermes",
        "hermes_raw": output[:500],
        "score": None,
        "grade": None,
        "reasoning": reasoning,
        "trade_count": len(trades),
    }
    with open(HYPOTHESES_PATH, "a") as f:
        f.write(json.dumps(hypothesis) + "\n")

    print(f"[reflect:hermes] Strategy v{old_version} → v{new_version}")
    print(f"[reflect:hermes] Changed: {var_path} {old_val} → {new_val}")
    print(f"[reflect:hermes] Reasoning: {reasoning}")
    print(f"[reflect:hermes] Prior saved to state/strategy_v{old_version:02d}.yaml")

    return hypothesis


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reflection cycle for trading agent")
    parser.add_argument("--deterministic", action="store_true", help="Use rule-based reflection (Phase 5)")
    parser.add_argument("--hermes", action="store_true", help="Use Hermes-powered reflection (Phase 7)")
    args = parser.parse_args()

    if args.hermes:
        result = hermes_reflection()
    else:
        result = deterministic_reflection()

    print(json.dumps(result, indent=2))
