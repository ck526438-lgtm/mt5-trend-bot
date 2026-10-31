"""Backtest de la strategie de tendance sur l'historique reel M15 de ton terminal.

Tout est mesure en R (multiples du risque par trade), pas en dollars: avec un lot
fixe, un trade sur OR et un trade sur EURUSD ne sont pas comparables. 1 R = la
distance exacte de ton stop, donc 1 R = ce que tu aurais perdu si le stop etait
atteint. C'est la seule unite honnete pour juger une strategie.

Hypotheses annoncees (pas de magie):
- les bougies MT5 sont cotees bid: un achat paie le spread a l'entree, une vente le paie a la sortie;
- le stop est declenche si la meche le traverse et rempli a ce prix (slippage = 0, optimiste);
- pas de commission ni de swap: si ton broker facture, retranche l'equivalent en R;
- la courbe d'equilibre suppose un risque constant de risk_pct_per_trade par position, capitalise.
"""

import argparse
import json
from datetime import datetime, timezone

import pandas as pd

import mt5_client as m
import strategy as st

CONTRACTS: dict[str, float] = {}


def load_config(path: str = "config.json") -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def close_trades(pos: dict, exit_price: float, exit_time: str, why: str, bar_index: int,
                 contract: float) -> dict:
    pnl = (exit_price - pos["entry"]) * pos["direction"] * pos["lots"] * contract
    return {**pos, "exit": exit_price, "exit_time": exit_time, "why": why,
            "pnl": pnl, "bars": bar_index - pos["bar_index"],
            "r": (exit_price - pos["entry"]) * pos["direction"] / pos["sl_dist"]}


def simulate(df: pd.DataFrame, cfg: dict, symbol: str, spread_price: float) -> list[dict]:
    contract = CONTRACTS.get(symbol, 100.0)
    pos, trades = None, []

    for i in range(1, len(df)):
        row = df.iloc[i]

        if pos is not None:
            exit_price = exit_why = None
            if pos["direction"] == 1 and row["low"] <= pos["sl"]:
                exit_price, exit_why = pos["sl"], "stop suiveur"
            elif pos["direction"] == -1 and row["high"] >= pos["sl"]:
                exit_price, exit_why = pos["sl"], "stop suiveur"
            elif st.trend_broken(pos["direction"], row):
                exit_price = float(row["close"]) + (spread_price if pos["direction"] == -1 else 0.0)
                exit_why = "tendance cassee (EMA)"

            if exit_price is not None:
                trades.append(close_trades(pos, exit_price, str(row["dt"]), exit_why, i, contract))
                pos = None
                continue

            pos["sl"] = st.trail_stop(pos["direction"], pos["sl"], float(row["close"]),
                                      float(row["atr"]), cfg)

        sig = st.entry_signal(df.iloc[: i + 1], cfg, symbol)
        if not sig.is_trade or (pos is not None and pos["direction"] == sig.direction):
            continue

        if pos is not None:
            trades.append(close_trades(pos, float(row["close"]), str(row["dt"]),
                                       "signal inverse", i, contract))
            pos = None

        entry = float(row["close"]) + (spread_price if sig.direction == 1 else 0.0)
        sl = sig.stop + (spread_price if sig.direction == 1 else 0.0)
        pos = {
            "symbol": symbol,
            "direction": sig.direction,
            "side": "ACHAT" if sig.direction == 1 else "VENTE",
            "entry": entry,
            "entry_time": str(row["dt"]),
            "sl": sl,
            "sl_dist": abs(entry - sl),
            "lots": 1.0,
            "bar_index": i,
            "adx": sig.adx,
        }

    if pos is not None:
        last = df.iloc[-1]
        trades.append(close_trades(pos, float(last["close"]), str(last["dt"]),
                                   "fin de backtest", len(df) - 1, contract))
    return [t for t in trades if t["sl_dist"] > 0]


def metrics(trades: list[dict], risk_pct: float) -> dict:
    if not trades:
        return {"trades": 0}
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))

    eq, peak, max_dd = 1.0, 1.0, 0.0
    for r in rs:
        eq *= 1.0 + r * risk_pct / 100.0
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)

    worst_streak = streak = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        worst_streak = max(worst_streak, streak)

    return {
        "trades": len(rs),
        "win_rate_pct": round(100 * len(wins) / len(rs), 1),
        "profit_factor_R": round(gross_win / gross_loss, 2) if gross_loss else float("inf"),
        "expectance_R": round(sum(rs) / len(rs), 3),
        "total_R": round(sum(rs), 1),
        "avg_gain_R": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_perte_R": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "max_R": round(max(rs), 2),
        "min_R": round(min(rs), 2),
        "pertes_consecutives_max": worst_streak,
        "rendement_equilibre_pct": round(100 * (eq - 1.0), 1),
        "drawdown_max_pct": round(100 * max_dd, 1),
        "duree_moyenne_bougies": round(sum(t["bars"] for t in trades) / len(rs), 1),
        "achats": sum(1 for t in trades if t["direction"] == 1),
        "ventes": sum(1 for t in trades if t["direction"] == -1),
    }


def half_split_report(trades: list[dict], risk_pct: float) -> list[dict]:
    if len(trades) < 8:
        return []
    mid = len(trades) // 2
    halves = [(f"1re moitie ({trades[0]['entry_time'][:10]} -> {trades[mid - 1]['entry_time'][:10]})", trades[:mid]),
              (f"2eme moitie ({trades[mid]['entry_time'][:10]} -> {trades[-1]['entry_time'][:10]})", trades[mid:])]
    rows = []
    for label, part in halves:
        mm = metrics(part, risk_pct)
        rows.append({"periode": label, "trades": mm.get("trades", 0),
                     "win_rate_pct": mm.get("win_rate_pct", 0),
                     "profit_factor_R": mm.get("profit_factor_R", 0),
                     "total_R": mm.get("total_R", 0)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=0)
    ap.add_argument("--out", default="backtest_trades.csv")
    args = ap.parse_args()

    cfg = load_config()
    if args.bars:
        cfg["backtest"]["bars"] = args.bars
    risk_pct = float(cfg["risk"]["risk_pct_per_trade"])

    info = m.connect()
    print(f"Compte {info['login']} @ {info['server']} ({info['trade_mode_name']}) | "
          f"equilibre reel {info['balance']} {info['currency']}")

    tf, bars_n = cfg["timeframe"], int(cfg["backtest"]["bars"])
    htf_tf = str(cfg["strategy"].get("htf_timeframe") or "")
    all_trades, summary = [], []

    for symbol in cfg["symbols"]:
        meta = m.symbol_meta(symbol)
        CONTRACTS[symbol] = float(meta["contract_size"])
        spread_price = float(meta["spread"]) * float(meta["point"])

        df = m.bars(symbol, tf, bars_n)
        df = st.compute_indicators(df, cfg)
        if htf_tf:
            h = st.compute_indicators(m.bars(symbol, htf_tf, bars_n // 4 + 200), cfg)
            df = st.attach_htf_trend(df, st.htf_trend(h))

        trades = simulate(df, cfg, symbol, spread_price)
        all_trades += trades
        mm = metrics(trades, risk_pct)
        summary.append({"symbole": symbol, "bougies": len(df),
                        "de": str(df["dt"].iloc[0])[:16], "a": str(df["dt"].iloc[-1])[:16],
                        "spread_pts": meta["spread"], **mm})
        print(f"\n--- {symbol} | {len(df)} bougies {tf} du {summary[-1]['de']} au {summary[-1]['a']} ---")
        print(f"    spread: {meta['spread']} pts ({spread_price:.{meta['digits']}f}) | "
              f"contrat: {meta['contract_size']} unites/lot")
        for k, v in mm.items():
            print(f"  {k:26s} {v}")
        print(f"  {'risk_pct utilise':26s} {risk_pct} % du compte par trade")
        for hrow in half_split_report(trades, risk_pct):
            print(f"  stabilite {hrow['periode']}: {hrow['trades']} trades, "
                  f"win {hrow['win_rate_pct']} %, PF {hrow['profit_factor_R']}, total {hrow['total_R']} R")

    out = pd.DataFrame(all_trades)
    if len(out):
        out[["symbol", "side", "direction", "entry_time", "entry", "exit_time", "exit",
             "sl", "sl_dist", "adx", "bars", "why", "r", "pnl"]].to_csv(
            args.out, index=False, encoding="utf-8-sig")

    total = metrics(all_trades, risk_pct)
    print(f"\n=== PORTEFEUILLE COMPLETE ({len(all_trades)} trades) ===")
    for k, v in total.items():
        print(f"  {k:26s} {v}")
    verdict = ("POSITIVE" if total.get("expectance_R", 0) > 0 else "NEGATIVE")
    print(f"\n  VERDICT: esperance {verdict} ({total.get('expectance_R')} R par trade).")
    print(f"  Si esperance <= 0 ou profit factor < 1,2: ne pas mettre d'argent reel dessus.")
    print(f"  Detail: {args.out} | resume: backtest_summary.json")
    print(f"  Attention: {bars_n} bougies {tf} = echantillon IN-SAMPLE, un seul regime de marche.")

    with open("backtest_summary.json", "w", encoding="utf-8") as f:
        json.dump({"per_symbol": summary, "total": total, "risk_pct": risk_pct,
                   "generated_at": datetime.now(timezone.utc).isoformat()},
                  f, indent=2, ensure_ascii=False)
    m.shutdown()


if __name__ == "__main__":
    main()
