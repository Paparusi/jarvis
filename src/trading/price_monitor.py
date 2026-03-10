"""PriceMonitor — Lightweight price watcher for XAUUSD trading zones.

NO LLM. Polls MT5 every N seconds, checks price against alert zones,
fires callbacks when price enters a zone or invalidates it.

Usage:
    monitor = PriceMonitor(mt5_client, poll_interval=10)
    monitor.set_plan(plan_dict)
    monitor.on_alert(my_alert_handler)
    monitor.on_invalidate(my_invalidation_handler)
    monitor.on_scenario(my_scenario_handler)
    await monitor.start()
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, Awaitable

from src.utils.logging import get_logger

log = get_logger("trading.price_monitor")

AlertCallback = Callable[[dict, float], Awaitable[None]]
InvalidateCallback = Callable[[dict, str], Awaitable[None]]
ScenarioCallback = Callable[[dict], Awaitable[None]]


class PriceMonitor:
    """Lightweight price watcher. NO LLM. Polls MT5 every N seconds."""

    def __init__(self, mt5_client: Any, poll_interval: int = 10, symbol: str = "") -> None:
        self._mt5 = mt5_client
        self._poll_interval = poll_interval
        self._symbol = symbol or __import__("os").environ.get("TRADING_SYMBOL", "XAUUSD")
        self._zones: list[dict] = []
        self._scenarios: list[dict] = []
        self._alert_cb: AlertCallback | None = None
        self._invalidate_cb: InvalidateCallback | None = None
        self._scenario_cb: ScenarioCallback | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._triggered_zones: set[str] = set()  # zone_ids already triggered (avoid re-trigger)

    # ── Plan management ──────────────────────────────────────────────

    def set_plan(self, plan_dict: dict) -> None:
        """Load zones and scenarios from a TradePlan dict.

        plan_dict has keys: alert_zones (list[dict]), scenarios (list[dict]).
        Each zone dict has: zone_id, direction, price_high, price_low,
        confluence_score, sl_price, tp1_price, tp2_price, invalidation (str).
        Each scenario dict has: condition, action, new_bias.
        Clears existing zones/scenarios and triggered set.
        """
        self._zones = list(plan_dict.get("alert_zones", []))
        self._scenarios = list(plan_dict.get("scenarios", []))
        self._triggered_zones.clear()
        log.info("plan_set", zone_count=len(self._zones), scenario_count=len(self._scenarios))

    # ── Callback registration ────────────────────────────────────────

    def on_alert(self, callback: AlertCallback) -> None:
        """Register callback fired when price enters an alert zone."""
        self._alert_cb = callback

    def on_invalidate(self, callback: InvalidateCallback) -> None:
        """Register callback fired when a zone is invalidated."""
        self._invalidate_cb = callback

    def on_scenario(self, callback: ScenarioCallback) -> None:
        """Register callback fired when a scenario condition is met."""
        self._scenario_cb = callback

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("price_monitor_started", poll_interval=self._poll_interval)

    async def stop(self) -> None:
        """Stop the background polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("price_monitor_stopped")

    # ── Main loop ────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Main loop: get tick -> check zones -> fire callbacks."""
        while self._running:
            try:
                tick = await self._mt5.get_tick(self._symbol)
                price = tick.get("bid", 0.0)

                if price <= 0:
                    await asyncio.sleep(self._poll_interval)
                    continue

                # Check each zone
                for zone in list(self._zones):
                    zone_id = zone.get("zone_id", "")

                    # Skip already triggered
                    if zone_id in self._triggered_zones:
                        continue

                    # Check if price enters zone
                    if self._price_in_zone(price, zone):
                        self._triggered_zones.add(zone_id)
                        log.info("zone_alert", zone_id=zone_id, price=price)
                        if self._alert_cb:
                            await self._alert_cb(zone, price)

                    # Check invalidation — parse price from invalidation string
                    invalidation = zone.get("invalidation", "")
                    if invalidation and self._check_invalidation(price, invalidation):
                        log.info("zone_invalidated", zone_id=zone_id, price=price)
                        self._zones.remove(zone)
                        if self._invalidate_cb:
                            await self._invalidate_cb(zone, f"Price {price} invalidated zone")

                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("price_monitor_error", error=str(exc))
                await asyncio.sleep(self._poll_interval)

    # ── Zone checks ──────────────────────────────────────────────────

    def _price_in_zone(self, price: float, zone: dict) -> bool:
        """Check if price is within zone's price_high and price_low (inclusive)."""
        return zone.get("price_low", 0.0) <= price <= zone.get("price_high", 0.0)

    def _check_invalidation(self, price: float, invalidation: str) -> bool:
        """Parse invalidation string and check if current price invalidates the zone.

        Format: "Zone invalid if price closes above X.XX"
                or "Zone invalid if price closes below X.XX"

        Extract the number and direction ("above"/"below") and compare.
        If "above" in text and price > extracted_number, return True.
        If "below" in text and price < extracted_number, return True.
        If can't parse, return False.
        """
        match = re.search(r"(above|below)\s+([\d.]+)", invalidation.lower())
        if not match:
            return False
        direction = match.group(1)
        level = float(match.group(2))
        if direction == "above" and price > level:
            return True
        if direction == "below" and price < level:
            return True
        return False

    # ── Zone management ──────────────────────────────────────────────

    def remove_zone(self, zone_id: str) -> None:
        """Remove a zone by ID."""
        self._zones = [z for z in self._zones if z.get("zone_id") != zone_id]
        self._triggered_zones.discard(zone_id)

    def add_zone(self, zone: dict) -> None:
        """Add a new zone."""
        self._zones.append(zone)

    # ── Properties ───────────────────────────────────────────────────

    @property
    def active_zones(self) -> list[dict]:
        """Return a copy of the current active zones."""
        return list(self._zones)

    @property
    def is_running(self) -> bool:
        """Whether the monitor loop is currently running."""
        return self._running
