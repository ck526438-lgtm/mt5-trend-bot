"""Wrapper MetaTrader5: connexion, donnees, ordres.

Tout passe par le terminal MT5 deja lance et connecte chez ton broker. Le bot ne
connaît pas ton mot de passe: il utilise la session ouverte dans l'application.
"""

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

CANDIDATE_PATHS = [
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files\MetaTrader 5\terminal.exe",
    os.path.expandvars(r"%PROGRAMFILES%\MetaTrader 5\terminal64.exe"),
]


class MT5Error(RuntimeError):
    pass


def _find_terminal() -> str | None:
    for p in CANDIDATE_PATHS:
        if os.path.exists(p):
            return p
    return None


def connect(login: int = 0) -> dict:
    path = _find_terminal()
    kwargs = {"timeout": 20000}
    if path:
        kwargs["path"] = path
    if not mt5.initialize(**kwargs):
        raise MT5Error(f"MT5.initialize() echoue: {mt5.last_error()} "
                       f"— lance l'application MetaTrader 5 et connecte-toi a ton compte.")

    info = mt5.terminal_info()
    acc = mt5.account_info()
    if acc is None:
        raise MT5Error("Aucun compte connecte dans le terminal MT5. "
                       "Connecte-toi a un compte ( reel ou demo ) dans MetaTrader 5 avant de lancer le bot.")
    if login and acc.login != login:
        raise MT5Error(f"Le terminal est connecte au compte {acc.login}, pas {login} demande dans config.json.")

    mode_names = {0: "DEMO", 1: "REAL", 2: "CONTEST"}
    return {
        "login": acc.login,
        "server": acc.server,
        "balance": acc.balance,
        "equity": acc.equity,
        "currency": acc.currency,
        "leverage": acc.leverage,
        "trade_mode": acc.trade_mode,
        "trade_mode_name": mode_names.get(acc.trade_mode, str(acc.trade_mode)),
        "connected": bool(info and info.connected),
        "terminal": path,
    }


def shutdown() -> None:
    mt5.shutdown()


def symbol_meta(symbol: str) -> dict:
    if not mt5.symbol_select(symbol, True):
        raise MT5Error(f"Symbole {symbol} indisponible sur ce compte "
                       f"(l'as-tu rendu visible dans Market Watch ?) -> {mt5.last_error()}")
    i = mt5.symbol_info(symbol)
    if i is None:
        raise MT5Error(f"Pas d'infos symbole pour {symbol}")
    return {
        "symbol": symbol,
        "point": i.point,
        "digits": i.digits,
        "contract_size": i.trade_contract_size,
        "volume_min": i.volume_min,
        "volume_max": i.volume_max,
        "volume_step": i.volume_step,
        "trade_stops_level": i.trade_stops_level,
        "trade_freeze_level": i.trade_freeze_level,
        "spread": i.spread,
        "filling": i.filling_mode,
        "currency": i.currency_profit,
    }


def bars(symbol: str, timeframe: str, count: int) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[timeframe], 0, count)
    if rates is None or len(rates) == 0:
        raise MT5Error(f"Aucune donnee {timeframe} sur {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


def last_closed_bar(symbol: str, timeframe: str, count: int = 300) -> tuple[pd.DataFrame, datetime]:
    """Bougies sans la bougie en cours: un signal sur une bougie ouverte n'est pas fige."""
    df = bars(symbol, timeframe, count)
    bar_dt = datetime.fromtimestamp(int(df["time"].iloc[-1]), tz=timezone.utc)
    return df.iloc[:-1].reset_index(drop=True), bar_dt


def tick(symbol: str) -> tuple[float, float]:
    t = mt5.symbol_info_tick(symbol)
    if t is None:
        raise MT5Error(f"Pas de tick sur {symbol}")
    return float(t.bid), float(t.ask)


def _filling_type(meta: dict):
    """filling_mode est un masque: bit0 = FOK, bit1 = IOC, bit2 = RETURN."""
    fm = int(meta["filling"])
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    if fm & 4:
        return mt5.ORDER_FILLING_RETURN
    return mt5.ORDER_FILLING_IOC


@dataclass
class OrderResult:
    ok: bool
    retcode: int
    order: int
    deal: int
    volume: float
    price: float
    error: str


def open_position(meta: dict, direction: int, lots: float, sl: float, tp: float,
                  deviation: int, magic: int, comment: str) -> OrderResult:
    symbol = meta["symbol"]
    bid, ask = tick(symbol)
    order_type = mt5.ORDER_TYPE_BUY if direction == 1 else mt5.ORDER_TYPE_SELL
    price = ask if direction == 1 else bid
    digits = meta["digits"]
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lots),
        "type": order_type,
        "price": round(price, digits),
        "deviation": int(deviation),
        "magic": int(magic),
        "sl": round(sl, digits) if sl else 0.0,
        "tp": round(tp, digits) if tp else 0.0,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_type(meta),
        "comment": comment[:31],
    }
    if req["sl"] == 0.0:
        raise MT5Error("ordre refuse: aucun stop-loss calcule")

    r = mt5.order_send(req)
    if r is None:
        return OrderResult(False, -1, 0, 0, lots, price, str(mt5.last_error()))
    ok = r.retcode == mt5.TRADE_RETCODE_DONE and r.order != 0
    return OrderResult(ok, r.retcode, r.order, r.deal, lots, r.price or price,
                       "" if ok else f"retcode {r.retcode}: {r.comment}")


def close_position(ticket: int, meta: dict, direction: int, lots: float, deviation: int, magic: int) -> OrderResult:
    bid, ask = tick(meta["symbol"])
    close_type = mt5.ORDER_TYPE_SELL if direction == 1 else mt5.ORDER_TYPE_BUY
    price = bid if direction == 1 else ask
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": meta["symbol"],
        "volume": float(lots),
        "type": close_type,
        "position": int(ticket),
        "price": round(price, meta["digits"]),
        "deviation": int(deviation),
        "magic": int(magic),
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_type(meta),
        "comment": "bot close",
    }
    r = mt5.order_send(req)
    if r is None:
        return OrderResult(False, -1, 0, 0, lots, price, str(mt5.last_error()))
    ok = r.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PARTIAL)
    return OrderResult(ok, r.retcode, r.order, r.deal, lots, r.price or price,
                       "" if ok else f"retcode {r.retcode}: {r.comment}")


def modify_stop(ticket: int, meta: dict, new_sl: float) -> bool:
    r = mt5.order_send({
        "action": mt5.TRADE_ACTION_SLTP,
        "position": int(ticket),
        "symbol": meta["symbol"],
        "sl": round(new_sl, meta["digits"]),
    })
    return r is not None and r.retcode == mt5.TRADE_RETCODE_DONE


def deal_profit(position_ticket: int) -> tuple[float, float]:
    """Profit realise (money, pourcent de rien) et prix moyen de cloture d'une position."""
    deals = mt5.history_deals_get(position=int(position_ticket)) or []
    money = sum(float(d.profit) + float(d.swap) + float(d.commission) for d in deals)
    closes = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
    price = float(closes[-1].price) if closes else 0.0
    return money, price


def bot_positions(magic: int) -> list[dict]:
    """positions_get() ne filtre pas par magic: on le fait cote Python."""
    out = []
    for p in mt5.positions_get() or []:
        if int(p.magic) != int(magic):
            continue
        out.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "direction": 1 if p.type == mt5.POSITION_TYPE_BUY else -1,
            "lots": p.volume,
            "open_price": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            "time": datetime.fromtimestamp(p.time, tz=timezone.utc),
            "comment": p.comment,
        })
    return out


def equity() -> float:
    acc = mt5.account_info()
    return float(acc.equity) if acc else 0.0


def wait_seconds(seconds: float) -> None:
    time.sleep(seconds)
