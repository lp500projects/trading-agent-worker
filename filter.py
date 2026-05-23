"""
Whale + Funding Filter for trading-agent-worker.
Drop-in module — add to pyproject.toml deps: aiohttp.

Usage in loop.py:
  from filter import should_enter
  ...
  if action == "entry":
      approved, reason = await should_enter(asset, "long")
      if not approved:
          print(f"[filter] BLOCKED {asset}: {reason}")
          continue  # skip this entry
"""
from __future__ import annotations

import json
import urllib.request
from typing import Literal

API_BASE = "https://api.hyperliquid.xyz"
MAX_FUNDING_APR = 30.0  # don't enter if funding costs >30% APR


def _post(payload: dict) -> dict:
    """Synchronous POST to Hyperliquid info endpoint."""
    req = urllib.request.Request(
        f"{API_BASE}/info",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def get_funding_rate(coin: str) -> dict | None:
    """Get current funding rate for a coin. Returns None if not found."""
    try:
        data = _post({"type": "metaAndAssetCtxs"})
    except Exception:
        return None

    if not isinstance(data, list) or len(data) < 2:
        return None

    meta = data[0]
    ctxs = data[1]
    universe = meta.get("universe", [])

    # Normalize coin name (BTC/USD → BTC, BTC-USDT → BTC)
    coin_clean = coin.split("/")[0].split("-")[0].upper()

    for i, asset in enumerate(universe):
        if asset["name"].upper() == coin_clean and i < len(ctxs):
            ctx = ctxs[i]
            funding_raw = float(ctx.get("funding") or 0)
            annual = abs(funding_raw) * 3 * 365 * 100
            return {
                "coin": coin_clean,
                "funding_rate": funding_raw,
                "annualized_apr": round(annual, 1),
                "mark_price": float(ctx.get("markPx") or 0),
            }

    return None


async def should_enter(
    coin: str,
    direction: Literal["long", "short"],
    check_funding: bool = True,
) -> tuple[bool, str]:
    """
    Should the bot enter this trade?

    Returns (approved, reason).
    Currently checks funding rates only (whale data lives on the local machine).
    """
    reasons = []

    # === FUNDING CHECK ===
    if check_funding:
        funding = get_funding_rate(coin)
        if funding is None:
            reasons.append(f"[funding] No data for {coin} — allowing")
        else:
            apr = funding["annualized_apr"]

            if direction == "long" and funding["funding_rate"] > 0:
                if apr > MAX_FUNDING_APR:
                    reasons.append(
                        f"[funding] BLOCK long {coin}: {apr:.0f}% APR cost "
                        f"(you'd pay shorts every 8h)"
                    )
                    return False, " | ".join(reasons)
                else:
                    reasons.append(
                        f"[funding] long {coin}: {apr:.0f}% APR cost (under threshold)"
                    )

            elif direction == "short" and funding["funding_rate"] < 0:
                if apr > MAX_FUNDING_APR:
                    reasons.append(
                        f"[funding] BLOCK short {coin}: {apr:.0f}% APR cost "
                        f"(you'd pay longs every 8h)"
                    )
                    return False, " | ".join(reasons)
                else:
                    reasons.append(
                        f"[funding] short {coin}: {apr:.0f}% APR cost (under threshold)"
                    )

            else:
                if apr < 1:
                    reasons.append(f"[funding] {coin}: neutral ({apr:.1f}% APR)")
                else:
                    who = "YOU collect" if (
                        (direction == "long" and funding["funding_rate"] < 0) or
                        (direction == "short" and funding["funding_rate"] > 0)
                    ) else "you pay"
                    reasons.append(f"[funding] {coin}: {apr:.1f}% APR ({who})")

    return True, " | ".join(reasons) if reasons else "no checks performed"
