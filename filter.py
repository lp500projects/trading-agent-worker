"""
Whale + Funding Filter — self-contained, zero external dependencies.
Reads whale position data from bundled whale_data.json (updated via cron).
Queries Hyperliquid API for live funding rates.

Works anywhere: Railway, local, Docker.

Usage:
  from filter import should_enter
  approved, reason = await should_enter("BTC/USD", "long")
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Literal

API_BASE = "https://api.hyperliquid.xyz"
MAX_FUNDING_APR = 30.0

# Path to whale data — updated periodically by cron
WHALE_DATA_PATH = Path(os.environ.get(
    "WHALE_DATA_PATH",
    Path(__file__).parent / "state" / "whale_data.json"
))


def _post(payload: dict) -> dict:
    """POST to Hyperliquid info endpoint."""
    req = urllib.request.Request(
        f"{API_BASE}/info",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def get_funding_rate(coin: str) -> dict | None:
    """Get funding rate for a coin."""
    try:
        data = _post({"type": "metaAndAssetCtxs"})
    except Exception:
        return None

    if not isinstance(data, list) or len(data) < 2:
        return None

    meta = data[0]
    ctxs = data[1]
    coin_clean = coin.split("/")[0].split("-")[0].upper()

    for i, asset in enumerate(meta.get("universe", [])):
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


def get_whale_divergence(coin: str) -> dict | None:
    """
    Analyze whale positions for a coin using bundled data.
    Separates profitable vs unprofitable wallets and checks divergence.
    """
    if not WHALE_DATA_PATH.exists():
        return None

    try:
        with open(WHALE_DATA_PATH) as f:
            data = json.load(f)
    except Exception:
        return None

    if not data:
        return None

    coin_clean = coin.split("/")[0].split("-")[0].upper()

    profitable = [w for w in data if w.get("total_pnl", 0) > 0]
    unprofitable = [w for w in data if w.get("total_pnl", 0) < 0]

    if not profitable or not unprofitable:
        return None

    def net(wallets) -> float:
        total = 0.0
        for w in wallets:
            for p in w.get("positions", []):
                if p["coin"].upper() == coin_clean:
                    total += p["size"] if p["side"] == "long" else -p["size"]
        return total

    smart_net = net(profitable)
    wrecked_net = net(unprofitable)

    if abs(smart_net) < 0.01 and abs(wrecked_net) < 0.01:
        return None  # No whale positions on this coin

    smart_direction = "long" if smart_net > 0 else "short"
    diverged = (smart_net * wrecked_net) < 0
    total_abs = abs(smart_net) + abs(wrecked_net)
    conviction = abs(smart_net) / total_abs if total_abs > 0 else 0

    return {
        "coin": coin_clean,
        "smart_net": round(smart_net, 2),
        "wrecked_net": round(wrecked_net, 2),
        "smart_direction": smart_direction,
        "diverged": diverged,
        "conviction": round(conviction, 3),
        "profitable_wallets": len(profitable),
        "unprofitable_wallets": len(unprofitable),
    }


async def should_enter(
    coin: str,
    direction: Literal["long", "short"],
    check_whales: bool = True,
    check_funding: bool = True,
) -> tuple[bool, str]:
    """
    Should the bot enter this trade?
    Returns (approved, reason).
    """
    reasons = []
    coin_clean = coin.split("/")[0].split("-")[0].upper()

    # === WHALE CHECK ===
    if check_whales:
        whale = get_whale_divergence(coin)
        if whale is None:
            reasons.append(f"[whale] no data for {coin_clean} — allowing")
        elif whale["smart_direction"] == direction:
            reasons.append(
                f"[whale] ✅ {coin_clean}: smart money is {direction} "
                f"(net {whale['smart_net']:+.1f}, {whale['conviction']*100:.0f}% conviction)"
            )
        elif whale["diverged"]:
            reasons.append(
                f"[whale] ❌ BLOCK {coin_clean}: smart money is {whale['smart_direction']} "
                f"(net {whale['smart_net']:+.1f}), losers are opposite"
            )
            return False, " | ".join(reasons)
        else:
            reasons.append(
                f"[whale] ⚠️ {coin_clean}: weak signal "
                f"(smart {whale['smart_direction']} net {whale['smart_net']:+.1f})"
            )

    # === FUNDING CHECK ===
    if check_funding:
        try:
            funding = get_funding_rate(coin)
        except Exception:
            funding = None

        if funding is None:
            reasons.append(f"[funding] no data for {coin_clean} — allowing")
        else:
            apr = funding["annualized_apr"]

            if direction == "long" and funding["funding_rate"] > 0:
                if apr > MAX_FUNDING_APR:
                    reasons.append(
                        f"[funding] ❌ BLOCK long {coin_clean}: {apr:.0f}% APR cost"
                    )
                    return False, " | ".join(reasons)
                else:
                    reasons.append(f"[funding] long {coin_clean}: {apr:.0f}% APR cost (<{MAX_FUNDING_APR:.0f}%)")
            elif direction == "short" and funding["funding_rate"] < 0:
                if apr > MAX_FUNDING_APR:
                    reasons.append(
                        f"[funding] ❌ BLOCK short {coin_clean}: {apr:.0f}% APR cost"
                    )
                    return False, " | ".join(reasons)
                else:
                    reasons.append(f"[funding] short {coin_clean}: {apr:.0f}% APR cost (<{MAX_FUNDING_APR:.0f}%)")
            else:
                who = "YOU collect" if (
                    (direction == "long" and funding["funding_rate"] < 0) or
                    (direction == "short" and funding["funding_rate"] > 0)
                ) else "neutral"
                reasons.append(f"[funding] {coin_clean}: {who} ({apr:.1f}% APR)")

    return True, " | ".join(reasons) if reasons else "no checks performed"
