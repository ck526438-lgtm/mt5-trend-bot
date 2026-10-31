"""Balayage de sensibilité: la MEME strategie, meme periode, 6 reglages differents.

C'est le fichier a regarder avant de croire un profit factor. Si le resultat change
de signe quand on bouge un parametre, le resultat ne vient pas du marche.

Ecrit sensitivity.csv et docs/img/sensibilite.png.
"""

import copy
import csv
import os

import mt5_client as m
import strategy as st
import backtest as bt

HERE = os.path.dirname(os.path.abspath(__file__))
BARS = 3000
VARIANTS = {
    "base  EMA20/100  ADX20  stop 2,0 ATR": {},
    "EMA20/50 (plus rapide)": {"ema_slow": 50},
    "ADX >= 25 (tendance plus exigeante)": {"adx_min": 25},
    "ADX >= 15 (plus permissif)": {"adx_min": 15},
    "stop 3,0 ATR / trail 3,5 (plus d'air)": {"atr_stop_mult": 3.0, "atr_trail_mult": 3.5},
    "sans filtre H1": {"htf_timeframe": ""},
}


def run() -> list[dict]:
    cfg = bt.load_config(os.path.join(HERE, "config.json"))
    rows = []
    for symbol in cfg["symbols"]:
        meta = m.symbol_meta(symbol)
        bt.CONTRACTS[symbol] = float(meta["contract_size"])
        spread = float(meta["spread"]) * float(meta["point"])
        for name, over in VARIANTS.items():
            c = copy.deepcopy(cfg)
            c["strategy"].update(over)
            df = st.compute_indicators(m.bars(symbol, c["timeframe"], BARS), c)
            htf = str(c["strategy"].get("htf_timeframe") or "")
            if htf:
                h = st.compute_indicators(m.bars(symbol, htf, BARS // 4 + 200), c)
                df = st.attach_htf_trend(df, st.htf_trend(h))
            mm = bt.metrics(bt.simulate(df, c, symbol, spread),
                            float(c["risk"]["risk_pct_per_trade"]))
            rows.append({"symbole": symbol, "reglage": name, "trades": mm.get("trades", 0),
                         "win_rate_pct": mm.get("win_rate_pct", 0),
                         "profit_factor_R": mm.get("profit_factor_R", 0),
                         "expectance_R": mm.get("expectance_R", 0),
                         "drawdown_max_pct": mm.get("drawdown_max_pct", 0)})
    return rows


def main() -> None:
    m.connect()
    rows = run()
    m.shutdown()

    with open(os.path.join(HERE, "sensitivity.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"{'symbole':9s} {'reglage':40s} {'nb':>4s} {'win%':>6s} {'PF_R':>6s} {'esp_R':>8s}")
    for r in rows:
        print(f"{r['symbole']:9s} {r['reglage']:40s} {r['trades']:>4} {r['win_rate_pct']:>6} "
              f"{r['profit_factor_R']:>6} {r['expectance_R']:>8}")
    pos = sum(1 for r in rows if r["expectance_R"] > 0)
    print(f"\n{pos}/{len(rows)} reglages ont une esperance positive sur la meme periode.")
    print("Fichiers ecrits: sensitivity.csv")


if __name__ == "__main__":
    main()
