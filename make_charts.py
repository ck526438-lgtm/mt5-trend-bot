"""Genere les images du README a partir des resultels reellement mesures.

Aucune donnee inventee: tout vient de backtest_trades.csv, backtest_summary.json,
sensitivity.csv et des bougies MT5 du terminal. Relance apres un nouveau backtest.
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

import mt5_client as m
import strategy as st
import backtest as bt

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(HERE, "docs", "img")
EUR, XAU = "#1f77b4", "#d4a017"
POS, NEG = "#2e8b57", "#c0392b"
plt.rcParams.update({"figure.facecolor": "white", "axes.grid": True,
                     "grid.alpha": .25, "font.size": 10, "axes.titlesize": 12})


def save(fig, name: str) -> None:
    path = os.path.join(IMG, name)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print("ecrit:", path)


def load_trades() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(HERE, "backtest_trades.csv"))
    for col in ("entry_time", "exit_time"):
        df[col] = pd.to_datetime(df[col], utc=True)
    return df


def equity_curve(trades: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.6))
    for symbol, color in (("EURUSD", EUR), ("XAUUSD", XAU)):
        t = trades[trades["symbol"] == symbol].sort_values("exit_time")
        ax.plot(t["exit_time"], t["r"].cumsum(), color=color, lw=1.9,
                label=f"{symbol} — total {t['r'].sum():+.1f} R")
    t_all = trades.sort_values("exit_time")
    ax.plot(t_all["exit_time"], t_all["r"].cumsum(), color="#555", lw=1.2, ls="--",
            label=f"les deux ensemble — total {t_all['r'].sum():+.1f} R")
    ax.axhline(0, color="black", lw=.8)
    ax.set_ylabel("resultat cumule (R)")
    ax.set_title("Courbe d'equite en multiples de risque — 0,5 % du compte par trade")
    ax.legend()
    fmt(ax)
    save(fig, "equite.png")


def fmt(ax) -> None:
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    fig = ax.get_figure()
    fig.autofmt_xdate(rotation=0, ha="center")


def drawdown(trades: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(9, 3.6))
    for symbol, color in (("EURUSD", EUR), ("XAUUSD", XAU)):
        t = trades[trades["symbol"] == symbol].sort_values("exit_time")
        cum = t["r"].cumsum()
        dd = cum - cum.cummax()
        ax.fill_between(t["exit_time"], dd, 0, color=color, alpha=.35,
                        label=f"{symbol} (pire {dd.min():+.1f} R)")
        ax.plot(t["exit_time"], dd, color=color, lw=1.1)
    ax.set_ylabel("eloigne du sommet (R)")
    ax.set_title("Drawdown: ce qu'il a fallu accepter avant que ça remonte")
    ax.legend()
    fmt(ax)
    save(fig, "drawdown.png")


def distribution(trades: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.8))
    for ax, symbol, color in zip(axes, ("EURUSD", "XAUUSD"), (EUR, XAU)):
        r = trades.loc[trades["symbol"] == symbol, "r"]
        counts, edges, patches = ax.hist(r, bins=22, edgecolor="white")
        for p, left in zip(patches, edges[:-1]):
            p.set_facecolor(POS if left >= 0 else NEG)
            p.set_alpha(.85)
        ax.axvline(0, color="black", lw=.8)
        ax.axvline(r.mean(), color="#222", lw=1.8, ls="--", label=f"esperance {r.mean():+.3f} R")
        win = 100 * (r > 0).mean()
        pf = r[r > 0].sum() / abs(r[r <= 0].sum())
        ax.set_title(f"{symbol}: {len(r)} trades, {win:.0f} % gagnants, PF {pf:.2f}")
        ax.set_xlabel("resultat par trade (R)")
        ax.set_ylabel("nombre de trades")
        ax.legend()
    save(fig, "distribution_R.png")


def anatomy(cfg: dict) -> None:
    info = m.connect()
    symbol = "EURUSD"
    meta = m.symbol_meta(symbol)
    bt.CONTRACTS[symbol] = float(meta["contract_size"])
    df = st.compute_indicators(m.bars(symbol, cfg["timeframe"], 260), cfg)
    h = st.compute_indicators(m.bars(symbol, "H1", 260 // 4 + 200), cfg)
    df = st.attach_htf_trend(df, st.htf_trend(h))
    df["x"] = pd.to_datetime(df["time"], unit="s", utc=True)

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1]})
    up = df["close"] >= df["open"]
    ax.vlines(df["x"], df["low"], df["high"], color="#999", lw=.6)
    ax.bar(df["x"][up], (df["close"] - df["open"])[up], bottom=df["open"][up],
           width=0.0095, color=POS, alpha=.85)
    ax.bar(df["x"][~up], (df["open"] - df["close"])[~up], bottom=df["close"][~up],
           width=0.0095, color=NEG, alpha=.85)
    ax.plot(df["x"], df["ema_fast"], color=EUR, lw=1.4, label=f"EMA{cfg['strategy']['ema_fast']}")
    ax.plot(df["x"], df["ema_slow"], color="#8e44ad", lw=1.6,
            label=f"EMA{cfg['strategy']['ema_slow']} (reference de tendance)")

    trades = load_trades()
    t = trades[(trades["symbol"] == symbol) & (trades["entry_time"] >= df["x"].iloc[0])]
    long_e = t[t["direction"] == 1]
    short_e = t[t["direction"] == -1]
    ax.scatter(long_e["entry_time"], long_e["entry"], marker="^", s=70, color=POS, zorder=5,
               label="entree achat")
    ax.scatter(short_e["entry_time"], short_e["entry"], marker="v", s=70, color=NEG, zorder=5,
               label="entree vente")
    ax.scatter(t["exit_time"], t["exit"], marker="o", s=34, facecolors="none", edgecolors="#333",
               zorder=5, label="sortie")
    ax.set_ylabel(f"prix {symbol} (M15)")
    ax.legend(loc="upper left", ncol=2, fontsize=8.5)
    ax.set_title("Anatomie: les trades reels de la strategie sur le marche (meme code que le bot)")

    ax2.plot(df["x"], df["adx"], color="#333", lw=1.2, label="ADX")
    ax2.axhline(cfg["strategy"]["adx_min"], color=NEG, ls="--", lw=1.1,
               label=f"seuil {cfg['strategy']['adx_min']}")
    ax2.fill_between(df["x"], 0, 60, where=df["adx"] < cfg["strategy"]["adx_min"],
                     color=NEG, alpha=.13, label="zone refusee (marche sans tendance)")
    ax2.set_ylabel("ADX")
    ax2.set_xlabel("date (UTC)")
    ax2.set_ylim(0, 60)
    ax2.legend(fontsize=8.5, ncol=3, loc="upper left")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %Hh"))
    fig.autofmt_xdate(rotation=0, ha="center")
    save(fig, "anatomie.png")
    m.shutdown()


def sensibilite_chart() -> None:
    path = os.path.join(HERE, "sensitivity.csv")
    if not os.path.exists(path):
        print("sensitivity.csv absent: lance `python sensitivity.py` puis relance ce script")
        return
    rows = pd.read_csv(path)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharex=True)
    for ax, symbol in zip(axes, ("EURUSD", "XAUUSD")):
        part = rows[rows["symbole"] == symbol]
        labels = [l.split(" (")[0] for l in part["reglage"]]
        colors = [POS if v > 0 else NEG for v in part["expectance_R"]]
        ax.barh(labels, part["expectance_R"], color=colors, alpha=.85)
        ax.axvline(0, color="black", lw=.9)
        ax.set_title(symbol)
        ax.tick_params(axis="y", labelsize=8)
        ax.set_xlabel("esperance par trade (R)")
    axes[0].invert_yaxis()
    fig.suptitle("Meme strategie, meme periode, 6 reglages de parametres", y=.99)
    save(fig, "sensibilite.png")


def main() -> None:
    os.makedirs(IMG, exist_ok=True)
    cfg = bt.load_config(os.path.join(HERE, "config.json"))
    trades = load_trades()
    print(f"{len(trades)} trades charges depuis backtest_trades.csv")
    equity_curve(trades)
    drawdown(trades)
    distribution(trades)
    anatomy(cfg)
    sensibilite_chart()


if __name__ == "__main__":
    main()
