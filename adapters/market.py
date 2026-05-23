"""Market data adapters. Free public endpoints by default; premium keys via env."""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

SCHEMA_VERSION = 1


@dataclass
class OHLCV:
    schema_version: int = SCHEMA_VERSION
    asset: str = ""
    timestamp: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0


@dataclass
class TickerSnapshot:
    schema_version: int = SCHEMA_VERSION
    asset: str = ""
    timestamp: int = 0
    last: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume_24h: float = 0.0
    change_24h_pct: float = 0.0


class MarketAdapter:
    """Base adapter. Free CCXT public endpoints — no API key needed."""

    def __init__(self, exchange_name: str | None = None):
        self.exchange_name = exchange_name or os.getenv("CCXT_EXCHANGE", "binance")
        self._exchange = None
        self._failure_count = 0
        self._max_failures = 5

    @property
    def circuit_open(self) -> bool:
        return self._failure_count >= self._max_failures

    def _get_exchange(self):
        if self._exchange is None:
            import ccxt
            config = {"enableRateLimit": True}
            api_key = os.getenv("CCXT_API_KEY")
            secret = os.getenv("CCXT_SECRET")
            if api_key and secret:
                config["apiKey"] = api_key
                config["secret"] = secret
            self._exchange = getattr(ccxt, self.exchange_name)(config)
        return self._exchange

    async def fetch_ohlcv(
        self, asset: str, timeframe: str = "1m", limit: int = 50
    ) -> list[OHLCV]:
        """Fetch OHLCV candles with retry (3 attempts, exponential backoff)."""
        if self.circuit_open:
            return []

        last_err = None
        for attempt in range(3):
            try:
                exchange = self._get_exchange()
                candles = await asyncio.to_thread(
                    exchange.fetch_ohlcv, asset, timeframe, limit=limit
                )
                self._failure_count = 0
                return [
                    OHLCV(
                        asset=asset,
                        timestamp=c[0],
                        open=c[1],
                        high=c[2],
                        low=c[3],
                        close=c[4],
                        volume=c[5],
                    )
                    for c in candles
                ]
            except Exception as e:
                last_err = e
                wait = 2 ** attempt
                await asyncio.sleep(wait)

        self._failure_count += 1
        if self._failure_count >= self._max_failures:
            print(f"[adapter] CIRCUIT BREAKER OPEN for {asset} after {self._max_failures} failures: {last_err}")
        return []

    async def fetch_ticker(self, asset: str) -> Optional[TickerSnapshot]:
        """Fetch current ticker snapshot with retry."""
        if self.circuit_open:
            return None

        last_err = None
        for attempt in range(3):
            try:
                exchange = self._get_exchange()
                ticker = await asyncio.to_thread(exchange.fetch_ticker, asset)
                self._failure_count = 0
                return TickerSnapshot(
                    asset=asset,
                    timestamp=ticker.get("timestamp", int(time.time() * 1000)),
                    last=ticker.get("last", 0.0),
                    bid=ticker.get("bid", 0.0),
                    ask=ticker.get("ask", 0.0),
                    volume_24h=ticker.get("baseVolume", 0.0),
                    change_24h_pct=ticker.get("percentage", 0.0),
                )
            except Exception as e:
                last_err = e
                wait = 2 ** attempt
                await asyncio.sleep(wait)

        self._failure_count += 1
        if self._failure_count >= self._max_failures:
            print(f"[adapter] CIRCUIT BREAKER OPEN for {asset} after {self._max_failures} failures: {last_err}")
        return None

    def reset_circuit(self):
        self._failure_count = 0
