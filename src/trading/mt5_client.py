"""MT5 Client — Async HTTP client for the Windows MT5 Bridge.

Talks to the FastAPI bridge running on Windows via httpx.
Bridge URL configured via MT5_BRIDGE_URL env var (default: http://localhost:8710).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from src.utils.logging import get_logger

log = get_logger("trading.mt5_client")

_DEFAULT_BRIDGE_URL = "http://localhost:8710"
_TIMEOUT = 10  # seconds


class MT5Client:
    """Async client for the MT5 REST bridge."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (
            base_url or os.environ.get("MT5_BRIDGE_URL", _DEFAULT_BRIDGE_URL)
        ).rstrip("/")
        self._client: httpx.AsyncClient | None = None
        # Symbol mapping: e.g. XAUUSD -> XAUUSDm for Exness
        self._symbol_map: dict[str, str] = {}
        trading_sym = os.environ.get("TRADING_SYMBOL", "")
        if trading_sym and trading_sym != "XAUUSD":
            # Auto-map base symbol to broker-specific symbol
            base = trading_sym.rstrip("m").rstrip(".")  # XAUUSDm -> XAUUSD
            if base != trading_sym:
                self._symbol_map[base] = trading_sym

    def _resolve_symbol(self, symbol: str) -> str:
        """Map generic symbol to broker-specific name."""
        return self._symbol_map.get(symbol, symbol)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=_TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def health(self) -> dict[str, Any]:
        """Check bridge health. Raises on failure."""
        client = await self._get_client()
        resp = await client.get("/health")
        resp.raise_for_status()
        return resp.json()

    async def is_available(self) -> bool:
        """Check if bridge is reachable. Returns bool (no exception)."""
        try:
            await self.health()
            return True
        except Exception:
            return False

    async def get_tick(self, symbol: str) -> dict[str, Any]:
        """Get current bid/ask for a symbol."""
        symbol = self._resolve_symbol(symbol)
        client = await self._get_client()
        resp = await client.get(f"/tick/{symbol}")
        resp.raise_for_status()
        return resp.json()

    async def get_rates(
        self, symbol: str, timeframe: str = "H1", count: int = 100
    ) -> list[dict[str, Any]]:
        """Get OHLCV candle data."""
        symbol = self._resolve_symbol(symbol)
        client = await self._get_client()
        resp = await client.get(
            f"/rates/{symbol}",
            params={"timeframe": timeframe, "count": count},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_account(self) -> dict[str, Any]:
        """Get account info."""
        client = await self._get_client()
        resp = await client.get("/account")
        resp.raise_for_status()
        return resp.json()

    async def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Get open positions."""
        client = await self._get_client()
        params = {"symbol": symbol} if symbol else {}
        resp = await client.get("/positions", params=params)
        resp.raise_for_status()
        return resp.json()

    async def place_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        sl: float | None = None,
        tp: float | None = None,
        comment: str = "JARVIS",
    ) -> dict[str, Any]:
        """Place a market order."""
        symbol = self._resolve_symbol(symbol)
        client = await self._get_client()
        payload: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "volume": volume,
            "comment": comment,
        }
        if sl is not None:
            payload["sl"] = sl
        if tp is not None:
            payload["tp"] = tp
        resp = await client.post("/order", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def close_position(
        self, ticket: int, volume: float | None = None
    ) -> dict[str, Any]:
        """Close a position by ticket."""
        client = await self._get_client()
        payload: dict[str, Any] = {}
        if volume is not None:
            payload["volume"] = volume
        resp = await client.post(f"/close/{ticket}", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_history(
        self, days: int = 7, symbol: str | None = None
    ) -> list[dict[str, Any]]:
        """Get trade history."""
        client = await self._get_client()
        params: dict[str, Any] = {"days": days}
        if symbol:
            params["symbol"] = symbol
        resp = await client.get("/history/deals", params=params)
        resp.raise_for_status()
        return resp.json()

    async def place_pending(
        self,
        symbol: str,
        order_type: str,
        volume: float,
        price: float,
        sl: float | None = None,
        tp: float | None = None,
        comment: str = "JARVIS",
    ) -> dict[str, Any]:
        """Place a pending (limit/stop) order."""
        symbol = self._resolve_symbol(symbol)
        client = await self._get_client()
        payload: dict[str, Any] = {
            "symbol": symbol,
            "order_type": order_type,
            "volume": volume,
            "price": price,
            "comment": comment,
        }
        if sl is not None:
            payload["sl"] = sl
        if tp is not None:
            payload["tp"] = tp
        resp = await client.post("/order/pending", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def modify_order(
        self,
        ticket: int,
        sl: float | None = None,
        tp: float | None = None,
        price: float | None = None,
    ) -> dict[str, Any]:
        """Modify SL/TP/price on pending order or open position."""
        client = await self._get_client()
        payload: dict[str, Any] = {}
        if sl is not None:
            payload["sl"] = sl
        if tp is not None:
            payload["tp"] = tp
        if price is not None:
            payload["price"] = price
        resp = await client.put(f"/order/{ticket}", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def cancel_order(self, ticket: int) -> dict[str, Any]:
        """Cancel a pending order."""
        client = await self._get_client()
        resp = await client.delete(f"/order/{ticket}")
        resp.raise_for_status()
        return resp.json()

    async def get_pending_orders(
        self, symbol: str | None = None
    ) -> list[dict[str, Any]]:
        """List pending orders."""
        client = await self._get_client()
        params = {"symbol": symbol} if symbol else {}
        resp = await client.get("/orders/pending", params=params)
        resp.raise_for_status()
        return resp.json()
