"""Tests for MT5Client v2 methods (pending orders)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.trading.mt5_client import MT5Client


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("TRADING_SYMBOL", raising=False)
    return MT5Client(base_url="http://test:8710")


def _mock_response(json_data, status_code=200):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    resp.status_code = status_code
    return resp


class TestPlacePending:
    @pytest.mark.asyncio
    async def test_place_buy_limit(self, client):
        mock_resp = _mock_response({"retcode": 10009, "order": 12345, "volume": 0.01, "price": 2640.0, "comment": "JARVIS"})
        with patch.object(client, '_get_client') as mock_get:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_resp
            mock_get.return_value = mock_http
            result = await client.place_pending("XAUUSD", "buy_limit", 0.01, 2640.0, sl=2625.0, tp=2670.0)
            assert result["retcode"] == 10009
            assert result["order"] == 12345
            mock_http.post.assert_called_once()
            call_kwargs = mock_http.post.call_args
            payload = call_kwargs[1]["json"]
            assert payload["order_type"] == "buy_limit"
            assert payload["volume"] == 0.01
            assert payload["price"] == 2640.0
            assert payload["sl"] == 2625.0
            assert payload["tp"] == 2670.0

    @pytest.mark.asyncio
    async def test_place_sell_limit_no_sl_tp(self, client):
        mock_resp = _mock_response({"retcode": 10009, "order": 12346})
        with patch.object(client, '_get_client') as mock_get:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_resp
            mock_get.return_value = mock_http
            result = await client.place_pending("XAUUSD", "sell_limit", 0.05, 2670.0)
            assert result["retcode"] == 10009
            payload = mock_http.post.call_args[1]["json"]
            assert "sl" not in payload
            assert "tp" not in payload

    @pytest.mark.asyncio
    async def test_symbol_resolved(self, client, monkeypatch):
        """Symbol mapping is applied via TRADING_SYMBOL env var."""
        monkeypatch.setenv("TRADING_SYMBOL", "XAUUSDm")
        # Re-create client to pick up env
        client2 = MT5Client(base_url="http://test:8710")
        mock_resp = _mock_response({"retcode": 10009, "order": 12347})
        with patch.object(client2, '_get_client') as mock_get:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_resp
            mock_get.return_value = mock_http
            await client2.place_pending("XAUUSD", "buy_limit", 0.01, 2640.0)
            payload = mock_http.post.call_args[1]["json"]
            assert payload["symbol"] == "XAUUSDm"


class TestModifyOrder:
    @pytest.mark.asyncio
    async def test_modify_sl_tp(self, client):
        mock_resp = _mock_response({"retcode": 10009, "comment": "OK"})
        with patch.object(client, '_get_client') as mock_get:
            mock_http = AsyncMock()
            mock_http.put.return_value = mock_resp
            mock_get.return_value = mock_http
            result = await client.modify_order(12345, sl=2660.0, tp=2680.0)
            assert result["retcode"] == 10009
            mock_http.put.assert_called_once()
            url_arg = mock_http.put.call_args[0][0]
            assert "/order/12345" in url_arg

    @pytest.mark.asyncio
    async def test_modify_price_only(self, client):
        mock_resp = _mock_response({"retcode": 10009, "comment": "OK"})
        with patch.object(client, '_get_client') as mock_get:
            mock_http = AsyncMock()
            mock_http.put.return_value = mock_resp
            mock_get.return_value = mock_http
            await client.modify_order(12345, price=2645.0)
            payload = mock_http.put.call_args[1]["json"]
            assert payload["price"] == 2645.0
            assert "sl" not in payload
            assert "tp" not in payload


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_success(self, client):
        mock_resp = _mock_response({"retcode": 10009, "comment": "OK"})
        with patch.object(client, '_get_client') as mock_get:
            mock_http = AsyncMock()
            mock_http.delete.return_value = mock_resp
            mock_get.return_value = mock_http
            result = await client.cancel_order(12345)
            assert result["retcode"] == 10009
            mock_http.delete.assert_called_once()
            url_arg = mock_http.delete.call_args[0][0]
            assert "/order/12345" in url_arg


class TestGetPendingOrders:
    @pytest.mark.asyncio
    async def test_list_all(self, client):
        orders = [{"ticket": 1001, "symbol": "XAUUSD"}, {"ticket": 1002, "symbol": "XAUUSD"}]
        mock_resp = _mock_response(orders)
        with patch.object(client, '_get_client') as mock_get:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_resp
            mock_get.return_value = mock_http
            result = await client.get_pending_orders()
            assert len(result) == 2
            # No params when symbol is None
            call_kwargs = mock_http.get.call_args
            assert call_kwargs[1].get("params", {}) == {}

    @pytest.mark.asyncio
    async def test_list_by_symbol(self, client):
        orders = [{"ticket": 1001, "symbol": "XAUUSD"}]
        mock_resp = _mock_response(orders)
        with patch.object(client, '_get_client') as mock_get:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_resp
            mock_get.return_value = mock_http
            result = await client.get_pending_orders(symbol="XAUUSD")
            assert len(result) == 1
            call_kwargs = mock_http.get.call_args
            assert call_kwargs[1]["params"]["symbol"] == "XAUUSD"
