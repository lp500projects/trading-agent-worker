"""
Async trading loop. Every minute: pull data, evaluate strategy, paper-trade, log.
schema_version: 1
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from adapters.market import MarketAdapter, OHLCV, TickerSnapshot
from score import TradeRecord, score_trades

STATE_DIR = Path(os.environ.get("STATE_DIR", Path(__file__).parent / "state"))
STRATEGY_PATH = STATE_DIR / "strategy.yaml"
TRADES_PATH = STATE_DIR / "trades.jsonl"
HEARTBEAT_PATH = STATE_DIR / "heartbeat.json"
GOAL_PATH = Path(os.environ.get("GOAL_PATH", Path(__file__).parent / "goal.yaml"))


def load_strategy() -> dict:
    with open(STRATEGY_PATH) as f:
        return yaml.safe_load(f)


def load_goal() -> dict:
    with open(GOAL_PATH) as f:
        return yaml.safe_load(f)


def save_trade(record: dict):
    with open(TRADES_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_trades() -> list[dict]:
    if not TRADES_PATH.exists():
        return []
    trades = []
    with open(TRADES_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                trades.append(json.loads(line))
    return trades


def write_heartbeat(loop_count: int, last_action: str):
    heartbeat = {
        "schema_version": 1,
        "loop_count": loop_count,
        "last_action": last_action,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    with open(HEARTBEAT_PATH, "w") as f:
        json.dump(heartbeat, f, indent=2)


class PaperTrader:
    """Tracks open positions and evaluates entry/exit conditions."""

    def __init__(self, strategy: dict):
        self.strategy = strategy
        self.open_positions: dict[str, dict] = {}  # asset -> position
        self._adapter = MarketAdapter()

    async def evaluate(self, asset: str) -> str:
        """Evaluate asset and return action: 'entry', 'exit', 'hold'."""
        candles = await self._adapter.fetch_ohlcv(asset, timeframe="1m", limit=100)

        if len(candles) < 50:
            return "hold"

        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]
        current_price = closes[-1]
        current_volume = volumes[-1]

        # Trend filter: simple SMA
        sma = sum(closes[-50:]) / 50
        trend_up = current_price > sma

        # RSI (14-period)
        rsi = compute_rsi(closes, 14)

        # Volume spike detection
        avg_vol_20 = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else current_volume
        volume_spike = current_volume > avg_vol_20 * self.strategy["entry"]["volume_spike_factor"]

        # Entry conditions
        entry = self.strategy["entry"]
        if rsi is not None and rsi < entry["rsi_threshold"]:
            # Check min volume filter
            if current_volume * current_price >= self.strategy["filter"]["min_volume_usd"]:
                # Check max concurrent
                if len(self.open_positions) < self.strategy["position"]["max_concurrent"]:
                    return "entry"

        # Exit conditions for open positions
        if asset in self.open_positions:
            pos = self.open_positions[asset]
            entry_price = pos["entry_price"]
            pnl_pct = (current_price - entry_price) / entry_price * 100
            hold_hours = (time.time() - pos["entry_time"]) / 3600

            exit_cfg = self.strategy["exit"]
            if pnl_pct >= exit_cfg["take_profit_pct"]:
                return "exit"
            if pnl_pct <= -exit_cfg["stop_loss_pct"]:
                return "exit"
            if hold_hours >= exit_cfg["max_hold_hours"]:
                return "exit"

        # Debug: print evaluation state every cycle
        rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
        print(f"[eval] {asset} price={current_price:.2f} rsi={rsi_str} vol_spike={volume_spike} trend_up={trend_up} sma={sma:.2f}")
        return "hold"


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Compute RSI for the given period."""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(-period, 0):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(delta))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


async def run_loop(assets: list[str], max_iterations: int | None = None):
    """Main loop: every 60s, pull data, evaluate, paper-trade, log."""
    strategy = load_strategy()
    trader = PaperTrader(strategy)
    loop_count = 0

    print(f"[loop] Starting paper trading on {assets}")
    print(f"[loop] Strategy v{strategy['version']} — RSI<{strategy['entry']['rsi_threshold']}, "
          f"TP={strategy['exit']['take_profit_pct']}%, SL={strategy['exit']['stop_loss_pct']}%")

    while max_iterations is None or loop_count < max_iterations:
        loop_count += 1
        now = datetime.now(timezone.utc)
        action_taken = "none"

        for asset in assets:
            try:
                action = await trader.evaluate(asset)

                if action == "entry":
                    ticker = await trader._adapter.fetch_ticker(asset)
                    price = ticker.last if ticker else 0.0
                    if price <= 0:
                        continue

                    trader.open_positions[asset] = {
                        "entry_price": price,
                        "entry_time": time.time(),
                        "entry_time_utc": now.isoformat(),
                    }
                    trade_record = {
                        "schema_version": 1,
                        "asset": asset,
                        "action": "entry",
                        "price": price,
                        "time_utc": now.isoformat(),
                        "loop": loop_count,
                    }
                    save_trade(trade_record)
                    action_taken = f"entry {asset} @ {price:.2f}"
                    print(f"[loop] {action_taken}")

                elif action == "exit" and asset in trader.open_positions:
                    pos = trader.open_positions.pop(asset)
                    ticker = await trader._adapter.fetch_ticker(asset)
                    exit_price = ticker.last if ticker else 0.0
                    if exit_price <= 0:
                        trader.open_positions[asset] = pos
                        continue

                    pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
                    hold_hours = (time.time() - pos["entry_time"]) / 3600

                    trade_record = {
                        "schema_version": 1,
                        "asset": asset,
                        "action": "exit",
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "pnl_pct": round(pnl_pct, 4),
                        "hold_hours": round(hold_hours, 2),
                        "entry_time_utc": pos["entry_time_utc"],
                        "exit_time_utc": now.isoformat(),
                        "loop": loop_count,
                    }
                    save_trade(trade_record)
                    action_taken = f"exit {asset} @ {exit_price:.2f} ({pnl_pct:+.2f}%)"
                    print(f"[loop] {action_taken}")

            except Exception as e:
                print(f"[loop] Error evaluating {asset}: {e}")

        write_heartbeat(loop_count, action_taken)
        # Status line every cycle so we can see the worker is alive
        open_count = len(trader.open_positions)
        print(f"[loop] cycle={loop_count} action={action_taken} open_positions={open_count} exchange={trader._adapter.exchange_name}")
        await asyncio.sleep(60)


if __name__ == "__main__":
    import sys
    assets = sys.argv[1:] if len(sys.argv) > 1 else None
    if assets is None:
        goal = load_goal()
        assets = goal["assets"]
    asyncio.run(run_loop(assets))