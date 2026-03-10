"""MT5 Bridge — FastAPI REST wrapper for MetaTrader5 Python package.

Runs on Windows (not WSL2) because MetaTrader5 uses Win32 IPC.
Start: uvicorn mt5_bridge:app --host 127.0.0.1 --port 8710

Required (Windows Python): pip install MetaTrader5 fastapi uvicorn websockets
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import MetaTrader5 as mt5
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel


# ── Timeframe mapping ──────────────────────────────────────────────────

_TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M5": mt5.TIMEFRAME_M5,
    "M10": mt5.TIMEFRAME_M10,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
    "H4": mt5.TIMEFRAME_H4,
    "H6": mt5.TIMEFRAME_H6,
    "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


# ── Pending order type mapping ────────────────────────────────────────

_ORDER_TYPE_MAP = {
    "buy_limit": mt5.ORDER_TYPE_BUY_LIMIT,
    "sell_limit": mt5.ORDER_TYPE_SELL_LIMIT,
    "buy_stop": mt5.ORDER_TYPE_BUY_STOP,
    "sell_stop": mt5.ORDER_TYPE_SELL_STOP,
}


# ── Pydantic models ───────────────────────────────────────────────────

class OrderRequest(BaseModel):
    symbol: str
    side: str  # "buy" or "sell"
    volume: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    comment: str = "JARVIS"


class CloseRequest(BaseModel):
    volume: Optional[float] = None  # None = full close


class PendingOrderRequest(BaseModel):
    symbol: str
    order_type: str  # "buy_limit", "sell_limit", "buy_stop", "sell_stop"
    volume: float
    price: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    comment: str = "JARVIS"


class ModifyRequest(BaseModel):
    sl: Optional[float] = None
    tp: Optional[float] = None
    price: Optional[float] = None


# ── Lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not mt5.initialize():
        err = mt5.last_error()
        raise RuntimeError(f"MT5 initialize failed: {err}")
    info = mt5.terminal_info()
    print(f"MT5 connected: {info.name} (build {info.build})")
    yield
    mt5.shutdown()
    print("MT5 disconnected")


app = FastAPI(title="MT5 Bridge", version="1.0", lifespan=lifespan)


# ── Helpers ───────────────────────────────────────────────────────────

def _rates_to_list(rates) -> list[dict]:
    """Convert numpy structured array from MT5 to list of dicts."""
    if rates is None or len(rates) == 0:
        return []
    result = []
    for r in rates:
        result.append({
            "time": int(r["time"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "tick_volume": int(r["tick_volume"]),
            "spread": int(r["spread"]),
            "real_volume": int(r["real_volume"]),
        })
    return result


def _position_to_dict(pos) -> dict:
    """Convert MT5 position named tuple to dict."""
    return {
        "ticket": pos.ticket,
        "symbol": pos.symbol,
        "type": pos.type,  # 0=buy, 1=sell
        "volume": pos.volume,
        "price_open": pos.price_open,
        "price_current": pos.price_current,
        "sl": pos.sl,
        "tp": pos.tp,
        "profit": pos.profit,
        "swap": pos.swap,
        "time": int(pos.time),
        "magic": pos.magic,
        "comment": pos.comment,
    }


def _deal_to_dict(deal) -> dict:
    """Convert MT5 deal named tuple to dict."""
    return {
        "ticket": deal.ticket,
        "order": deal.order,
        "symbol": deal.symbol,
        "type": deal.type,
        "volume": deal.volume,
        "price": deal.price,
        "profit": deal.profit,
        "swap": deal.swap,
        "commission": deal.commission,
        "time": int(deal.time),
        "magic": deal.magic,
        "comment": deal.comment,
    }


def _order_to_dict(order) -> dict:
    """Convert MT5 order named tuple to dict."""
    return {
        "ticket": order.ticket,
        "symbol": order.symbol,
        "type": order.type,
        "volume_initial": order.volume_initial,
        "volume_current": order.volume_current,
        "price_open": order.price_open,
        "price_current": order.price_current,
        "sl": order.sl,
        "tp": order.tp,
        "time_setup": int(order.time_setup),
        "time_expiration": int(order.time_expiration),
        "state": order.state,
        "magic": order.magic,
        "comment": order.comment,
    }


# ── Endpoints ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Verify MT5 terminal is connected."""
    info = mt5.terminal_info()
    if info is None:
        raise HTTPException(503, "MT5 terminal not connected")
    account = mt5.account_info()
    return {
        "status": "ok",
        "terminal": info.name,
        "build": info.build,
        "connected": info.connected,
        "account": account.login if account else None,
        "server": account.server if account else None,
    }


@app.get("/tick/{symbol}")
async def get_tick(symbol: str):
    """Get current bid/ask/last for a symbol."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise HTTPException(404, f"Symbol {symbol} not found or not selected")
    return {
        "symbol": symbol,
        "bid": tick.bid,
        "ask": tick.ask,
        "last": tick.last,
        "volume": tick.volume,
        "time": tick.time,
        "time_msc": tick.time_msc,
    }


@app.get("/rates/{symbol}")
async def get_rates(symbol: str, timeframe: str = "H1", count: int = 100):
    """Get OHLCV candle data."""
    tf = _TF_MAP.get(timeframe.upper())
    if tf is None:
        raise HTTPException(400, f"Invalid timeframe: {timeframe}. Valid: {list(_TF_MAP.keys())}")
    count = max(1, min(count, 100_000))
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        err = mt5.last_error()
        raise HTTPException(404, f"No data for {symbol} {timeframe}: {err}")
    return _rates_to_list(rates)


@app.get("/account")
async def get_account():
    """Get account info."""
    info = mt5.account_info()
    if info is None:
        raise HTTPException(500, f"Failed to get account: {mt5.last_error()}")
    return {
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "margin_level": info.margin_level,
        "profit": info.profit,
        "leverage": info.leverage,
        "currency": info.currency,
        "name": info.name,
    }


@app.get("/positions")
async def get_positions(symbol: Optional[str] = None):
    """Get open positions."""
    if symbol:
        positions = mt5.positions_get(symbol=symbol)
    else:
        positions = mt5.positions_get()
    if positions is None:
        return []
    return [_position_to_dict(p) for p in positions]


@app.post("/order")
async def place_order(req: OrderRequest):
    """Place a market order."""
    # Bridge-level volume safety cap
    if req.volume > 5.0:
        raise HTTPException(400, f"Volume {req.volume} exceeds bridge maximum of 5.0 lots")

    # Validate side
    side = req.side.lower()
    if side not in ("buy", "sell"):
        raise HTTPException(400, f"side must be 'buy' or 'sell', got '{req.side}'")

    # Ensure symbol is visible
    sym_info = mt5.symbol_info(req.symbol)
    if sym_info is None:
        raise HTTPException(404, f"Symbol {req.symbol} not found")
    if not sym_info.visible:
        mt5.symbol_select(req.symbol, True)

    # Get current price
    tick = mt5.symbol_info_tick(req.symbol)
    if tick is None:
        raise HTTPException(500, f"Cannot get tick for {req.symbol}")

    price = tick.ask if side == "buy" else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": req.symbol,
        "volume": req.volume,
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": 234000,
        "comment": req.comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    if req.sl is not None:
        request["sl"] = req.sl
    if req.tp is not None:
        request["tp"] = req.tp

    result = mt5.order_send(request)
    if result is None:
        raise HTTPException(500, f"order_send failed: {mt5.last_error()}")

    return {
        "retcode": result.retcode,
        "deal": result.deal,
        "order": result.order,
        "volume": result.volume,
        "price": result.price,
        "comment": result.comment,
        "request_id": result.request_id,
    }


@app.post("/close/{ticket}")
async def close_position(ticket: int, req: CloseRequest = None):
    """Close a position by ticket."""
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        raise HTTPException(404, f"Position {ticket} not found")

    pos = positions[0]
    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        raise HTTPException(500, f"Cannot get tick for {pos.symbol}")

    close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
    price = tick.bid if pos.type == 0 else tick.ask
    volume = (req.volume if req and req.volume else pos.volume)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": volume,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": 234000,
        "comment": "JARVIS_close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        raise HTTPException(500, f"close failed: {mt5.last_error()}")

    return {
        "retcode": result.retcode,
        "deal": result.deal,
        "order": result.order,
        "comment": result.comment,
    }


@app.get("/history/deals")
async def get_deals(days: int = 7, symbol: Optional[str] = None):
    """Get deal history for last N days."""
    days = max(1, min(days, 365))
    from_date = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0
    ) - timedelta(days=days)
    to_date = datetime.now(tz=timezone.utc)

    if symbol:
        deals = mt5.history_deals_get(from_date, to_date, symbol=symbol)
    else:
        deals = mt5.history_deals_get(from_date, to_date)

    if deals is None:
        return []
    return [_deal_to_dict(d) for d in deals]


@app.get("/symbols")
async def get_symbols(group: str = "*USD*"):
    """Get available symbols matching a pattern."""
    symbols = mt5.symbols_get(group=group)
    if symbols is None:
        return []
    return [
        {
            "name": s.name,
            "bid": s.bid,
            "ask": s.ask,
            "spread": s.spread,
            "digits": s.digits,
            "description": s.description,
        }
        for s in symbols[:100]  # cap at 100
    ]


@app.post("/order/pending")
async def place_pending_order(req: PendingOrderRequest):
    """Place a pending order (limit or stop)."""
    # Bridge-level volume safety cap
    if req.volume > 5.0:
        raise HTTPException(400, f"Volume {req.volume} exceeds bridge maximum of 5.0 lots")

    # Validate order type
    otype = req.order_type.lower()
    mt5_order_type = _ORDER_TYPE_MAP.get(otype)
    if mt5_order_type is None:
        raise HTTPException(
            400,
            f"Invalid order_type '{req.order_type}'. "
            f"Valid: {list(_ORDER_TYPE_MAP.keys())}",
        )

    # Ensure symbol is visible
    sym_info = mt5.symbol_info(req.symbol)
    if sym_info is None:
        raise HTTPException(404, f"Symbol {req.symbol} not found")
    if not sym_info.visible:
        mt5.symbol_select(req.symbol, True)

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": req.symbol,
        "volume": req.volume,
        "type": mt5_order_type,
        "price": req.price,
        "magic": 234000,
        "comment": req.comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    if req.sl is not None:
        request["sl"] = req.sl
    if req.tp is not None:
        request["tp"] = req.tp

    result = mt5.order_send(request)
    if result is None:
        raise HTTPException(500, f"order_send failed: {mt5.last_error()}")

    return {
        "retcode": result.retcode,
        "deal": result.deal,
        "order": result.order,
        "volume": result.volume,
        "price": result.price,
        "comment": result.comment,
        "request_id": result.request_id,
    }


@app.put("/order/{ticket}")
async def modify_order(ticket: int, req: ModifyRequest):
    """Modify a pending order or open position (SL/TP/price)."""
    # Check if it's a pending order first
    orders = mt5.orders_get(ticket=ticket)
    if orders:
        # Modify pending order
        order = orders[0]
        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": ticket,
            "symbol": order.symbol,
            "price": req.price if req.price is not None else order.price_open,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        if req.sl is not None:
            request["sl"] = req.sl
        else:
            request["sl"] = order.sl
        if req.tp is not None:
            request["tp"] = req.tp
        else:
            request["tp"] = order.tp

        result = mt5.order_send(request)
        if result is None:
            raise HTTPException(500, f"modify failed: {mt5.last_error()}")

        return {
            "retcode": result.retcode,
            "order": result.order,
            "comment": result.comment,
            "modified": "pending_order",
        }

    # Check if it's an open position
    positions = mt5.positions_get(ticket=ticket)
    if positions:
        pos = positions[0]

        # Cannot modify entry price of an open position
        if req.price is not None:
            raise HTTPException(
                400, "Cannot modify entry price of an open position"
            )

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": req.sl if req.sl is not None else pos.sl,
            "tp": req.tp if req.tp is not None else pos.tp,
        }

        result = mt5.order_send(request)
        if result is None:
            raise HTTPException(500, f"modify failed: {mt5.last_error()}")

        return {
            "retcode": result.retcode,
            "order": result.order,
            "comment": result.comment,
            "modified": "position",
        }

    raise HTTPException(404, f"No pending order or position with ticket {ticket}")


@app.delete("/order/{ticket}")
async def cancel_pending_order(ticket: int):
    """Cancel a pending order."""
    orders = mt5.orders_get(ticket=ticket)
    if not orders:
        raise HTTPException(404, f"Pending order {ticket} not found")

    request = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": ticket,
    }

    result = mt5.order_send(request)
    if result is None:
        raise HTTPException(500, f"cancel failed: {mt5.last_error()}")

    return {
        "retcode": result.retcode,
        "order": result.order,
        "comment": result.comment,
    }


@app.get("/orders/pending")
async def get_pending_orders(symbol: Optional[str] = None):
    """Get pending orders with optional symbol filter."""
    if symbol:
        orders = mt5.orders_get(symbol=symbol)
    else:
        orders = mt5.orders_get()
    if orders is None:
        return []
    return [_order_to_dict(o) for o in orders]


@app.websocket("/ws/ticks/{symbol}")
async def ws_ticks(websocket: WebSocket, symbol: str):
    """Stream real-time tick data via WebSocket."""
    await websocket.accept()
    mt5.symbol_select(symbol, True)
    last_time_msc = 0

    try:
        while True:
            tick = mt5.symbol_info_tick(symbol)
            if tick and tick.time_msc > last_time_msc:
                last_time_msc = tick.time_msc
                await websocket.send_json({
                    "symbol": symbol,
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "last": tick.last,
                    "time_msc": tick.time_msc,
                })
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8710)
