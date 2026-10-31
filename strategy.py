"""Signal de tendance partage par le backtest et le bot reel.

Les deux modes appellent ces fonctions identiques: un signal qui marche en
backtest est le meme calcule en direct (les divergences backtest/live sont la
cause n1 des strategies "qui marchaient sur le papier").
"""

from dataclasses import dataclass, field
import numpy as np
import pandas as pd


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return _wilder(tr, period)


def adx(df: pd.DataFrame, period: int) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr_ = atr(df, period)
    plus_di = 100 * _wilder(plus_dm, period) / atr_
    minus_di = 100 * _wilder(minus_dm, period) / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _wilder(dx.fillna(0), period)


def compute_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    s = cfg["strategy"]
    out = df.copy()
    out["ema_fast"] = ema(out["close"], int(s["ema_fast"]))
    out["ema_slow"] = ema(out["close"], int(s["ema_slow"]))
    out["atr"] = atr(out, int(s["atr_period"]))
    out["adx"] = adx(out, int(s["adx_period"]))
    return out


def htf_trend(df_htf: pd.DataFrame) -> list[tuple[float, int]]:
    """Signe de tendance par bougie du cadre superieur, date d'CLOTURE incluse."""
    up = (df_htf["ema_fast"] > df_htf["ema_slow"]).astype(int)
    down = (df_htf["ema_fast"] < df_htf["ema_slow"]).astype(int)
    trend = up - down
    trend[df_htf["ema_fast"].isna() | df_htf["ema_slow"].isna()] = 0
    return list(zip(df_htf["time"].astype(float) + 3600.0, trend.astype(int)))


def attach_htf_trend(df: pd.DataFrame, pairs: list[tuple[float, int]]) -> pd.DataFrame:
    """Chaque M15 herite de la derniere bougie superieure TERMINEE: aucun regard vers le futur."""
    if not pairs:
        df["htf_trend"] = 0
        return df
    close_times, trends = zip(*pairs)
    ct = np.asarray(close_times, dtype=float)
    tr = np.asarray(trends, dtype=int)
    idx = np.searchsorted(ct, df["time"].astype(float).values, side="right") - 1
    df["htf_trend"] = np.where(idx >= 0, tr[np.clip(idx, 0, len(tr) - 1)], 0)
    return df


@dataclass
class Signal:
    symbol: str
    direction: int
    price: float
    stop: float
    atr: float
    adx: float
    bar_time: str
    reason: str
    tags: list = field(default_factory=list)

    @property
    def is_trade(self) -> bool:
        return self.direction in (1, -1)


def _overshoot_atr(row: pd.Series) -> float:
    """Ecart prix/EMA20 exprime en ATR: au-dela d'un seuil, la bougie est deja partie."""
    if row["atr"] <= 0:
        return 0.0
    return abs(float(row["close"]) - float(row["ema_fast"])) / float(row["atr"])


def entry_signal(df: pd.DataFrame, cfg: dict, symbol: str) -> Signal:
    s = cfg["strategy"]
    row = df.iloc[-1]
    price = float(row["close"])
    atr_ = float(row["atr"])
    bar_time = str(row["dt"]) if "dt" in row.index else str(row["time"])

    no_signal = Signal(symbol, 0, price, 0.0, atr_, float(row["adx"]), bar_time, "", [])

    if any(pd.isna(row[k]) for k in ("ema_fast", "ema_slow", "atr", "adx")):
        no_signal.reason = "indicateurs pas encore chauds"
        return no_signal

    strong = float(row["adx"]) >= float(s["adx_min"])
    over = _overshoot_atr(row)
    expanded = over > float(s["max_bar_expansion_atr"])
    momentum = bool(row["close"] > row["open"]) if s["require_momentum"] else True
    down_momentum = bool(row["close"] < row["open"]) if s["require_momentum"] else True

    long_ok = row["ema_fast"] > row["ema_slow"] and row["close"] > row["ema_slow"] and momentum
    short_ok = row["ema_fast"] < row["ema_slow"] and row["close"] < row["ema_slow"] and down_momentum

    f, e = int(s["ema_fast"]), int(s["ema_slow"])
    if long_ok:
        direction, stop = 1, price - float(s["atr_stop_mult"]) * atr_
        why = f"EMA{f}>EMA{e} + prix>EMA{e}"
    elif short_ok:
        direction, stop = -1, price + float(s["atr_stop_mult"]) * atr_
        why = f"EMA{f}<EMA{e} + prix<EMA{e}"
    else:
        no_signal.reason = "pas d'alignement EMA/prix"
        return no_signal

    tags = [f"ADX {row['adx']:.1f}", f"ATR {atr_:.5f}", why]
    htf_tf = str(s.get("htf_timeframe") or "")
    htf = int(row["htf_trend"]) if htf_tf and "htf_trend" in row.index else 0
    tags.append(f"{htf_tf or 'HTF'} {'haussiere' if htf == 1 else 'baisse' if htf == -1 else 'mixte'}")
    if not strong:
        no_signal.reason = f"marche sans tendance (ADX {row['adx']:.1f} < {s['adx_min']})"
        no_signal.tags = tags
        return no_signal
    if htf != 0 and htf != direction:
        no_signal.reason = f"signal {htf_tf} contraire (contre-tendance)"
        no_signal.tags = tags
        return no_signal
    if expanded:
        no_signal.reason = f"bougie deja etendue: {over:.1f} xATR au-dela d'EMA{s['ema_fast']}"
        no_signal.tags = tags
        return no_signal
    if atr_ <= 0:
        no_signal.reason = "ATR nul"
        return no_signal

    return Signal(symbol, direction, price, stop, atr_, float(row["adx"]), bar_time,
                  "tendance alignee + ADX suffisant", tags)


def trail_stop(direction: int, current_stop: float, close: float, atr_: float, cfg: dict) -> float:
    """Stop suiveur a sens unique: il ne recule jamais."""
    mult = float(cfg["strategy"]["atr_trail_mult"])
    if direction == 1:
        return max(current_stop, close - mult * atr_)
    return min(current_stop, close + mult * atr_)


def trend_broken(direction: int, row: pd.Series) -> bool:
    if direction == 1:
        return row["ema_fast"] < row["ema_slow"]
    return row["ema_fast"] > row["ema_slow"]
