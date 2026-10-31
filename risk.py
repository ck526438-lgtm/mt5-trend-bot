"""Dimensionnement de position et garde-fous de risque.

Le signal decide QUOI faire, ce module decide COMBIEN et SI on a encore le droit
de trader. C'est la partie qui protege le capital, donc elle passe avant
n'importe quelle entree.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Sizing:
    lots: float
    stop_distance: float
    risk_money: float
    balance_used: float
    note: str = ""


def price_per_lot(contract_size: float) -> float:
    """Valeur monetaire d'un mouvement de 1,00 de prix pour 1 lot."""
    return float(contract_size)


def size_position(balance: float, cfg: dict, meta: dict, signal_stop: float, entry: float) -> Sizing:
    r, s = cfg["risk"], cfg["strategy"]
    risk_pct = float(r["risk_pct_per_trade"])
    stop_distance = abs(entry - signal_stop)

    if balance <= 0 or stop_distance <= 0:
        return Sizing(0.0, stop_distance, 0.0, balance, "equilibre ou stop invalide")

    money_per_lot = price_per_lot(meta["contract_size"])
    if money_per_lot <= 0:
        return Sizing(0.0, stop_distance, 0.0, balance, "taille de contrat inconnue")

    raw_lots = (balance * risk_pct / 100.0) / (stop_distance * money_per_lot)

    step = float(meta["volume_step"]) or 0.01
    lots = math.floor(raw_lots / step) * step
    lots = round(lots, 8)

    floor_lot = max(float(meta["volume_min"]), float(r["min_lots"]))
    ceil_lot = min(float(meta["volume_max"]), float(r["max_lots"]))

    note = ""
    if lots < floor_lot:
        lots = floor_lot
        risk_real = stop_distance * money_per_lot * lots
        note = f"porte au lot minimum {floor_lot} -> risque {risk_real:.2f} ({risk_real / balance * 100:.2f} %)"
    elif lots > ceil_lot:
        lots = ceil_lot
        note = f"plafonne a {ceil_lot} lots"

    risk_money = stop_distance * money_per_lot * lots
    return Sizing(lots, stop_distance, risk_money, balance, note)


def spread_points(meta: dict, bid: float, ask: float) -> float:
    point = float(meta["point"]) or 1e-5
    return (ask - bid) / point


def spread_ok(cfg: dict, symbol: str, spread_pts: float) -> bool:
    limits = cfg["risk"].get("max_spread_points", {})
    limit = float(limits.get(symbol, min(limits.values()) if limits else 1e9))
    return spread_pts <= limit


def in_session(cfg: dict, now: datetime | None = None) -> tuple[bool, str]:
    r = cfg["risk"]
    now = now or datetime.now(timezone.utc)
    start, end = int(r["trade_hours_utc_start"]), int(r["trade_hours_utc_end"])
    h = now.hour
    inside = start <= h < end if start <= end else (h >= start or h < end)
    return inside, f"{h:02d}h UTC dans {start:02d}-{end:02d}"


def clamp_stop_to_broker(cfg_stop: float, entry: float, direction: int, meta: dict) -> tuple[float, bool]:
    """Rapproche le stop si le broker impose une distance minimale."""
    min_dist = float(meta.get("trade_stops_level", 0)) * float(meta["point"])
    if min_dist <= 0:
        return cfg_stop, False
    if direction == 1 and (entry - cfg_stop) < min_dist:
        return entry - min_dist, True
    if direction == -1 and (cfg_stop - entry) < min_dist:
        return entry + min_dist, True
    return cfg_stop, False


@dataclass
class DailyGuard:
    max_loss_pct: float
    max_trades: int
    day: str = ""
    start_equity: float = 0.0
    trades: int = 0
    halted: bool = False

    def roll_day(self, equity: float, now: datetime) -> None:
        key = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if key != self.day:
            self.day, self.start_equity, self.trades, self.halted = key, equity, 0, False

    def check(self, equity: float) -> tuple[bool, str]:
        if self.start_equity <= 0:
            return True, "equilibre inconnu"
        dd_pct = (self.start_equity - equity) / self.start_equity * 100.0
        if self.halted:
            return False, f"coupe journaliere active (-{dd_pct:.2f} % aujourd'hui)"
        if dd_pct >= self.max_loss_pct:
            self.halted = True
            return False, f"perte max journaliere atteinte: -{dd_pct:.2f} % (limite {self.max_loss_pct} %)"
        if self.trades >= self.max_trades:
            return False, f"quota de {self.max_trades} trades/jour atteint"
        return True, f"risque OK (-{dd_pct:.2f} % du jour, {self.trades}/{self.max_trades} trades)"
