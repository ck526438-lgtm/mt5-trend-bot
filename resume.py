"""Analyse du journal trades.csv: ce que le robot a vraiment produit (paper ou reel).

Ne demande pas MT5. Relance-le de temps en temps: c'est le seul endroit ou la
strategie est jugee sur des trades reels, pas sur du backtest.
"""

import csv
import os
from collections import defaultdict

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.csv")
MIN_TRADES_POUR_CONCLURE = 100


def stats(rows: list[float]) -> dict:
    if not rows:
        return {"trades": 0}
    wins = [r for r in rows if r > 0]
    losses = [r for r in rows if r <= 0]
    cum, peak, dd = 0.0, 0.0, 0.0
    for r in rows:
        cum += r
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    gw, gl = sum(wins), abs(sum(losses))
    return {
        "trades": len(rows),
        "win_rate_pct": round(100 * len(wins) / len(rows), 1),
        "esperance_R": round(sum(rows) / len(rows), 3),
        "total_R": round(sum(rows), 2),
        "profit_factor_R": round(gw / gl, 2) if gl else float("inf"),
        "max_R": round(max(rows), 2),
        "min_R": round(min(rows), 2),
        "drawdown_R": round(dd, 2),
    }


def main() -> None:
    if not os.path.exists(PATH):
        print("Aucun trades.csv: le robot n'a pas encore cloture de trade.")
        return
    with open(PATH, encoding="utf-8-sig") as f:
        trades = [t for t in csv.DictReader(f)]
    if not trades:
        print("trades.csv vide.")
        return

    for t in trades:
        t["r"] = float(t["resultat_R"] or 0)
        t["money"] = float(t["resultat_money"] or 0)

    print(f"{len(trades)} trades clotures dans le journal\n")
    groups = {"TOUT": trades}
    for t in trades:
        groups.setdefault(f"mode {t['mode']}", []).append(t)
        groups.setdefault(f"{t['symbole']}", []).append(t)

    for name, part in groups.items():
        money = sum(p["money"] for p in part)
        s = {k: v for k, v in stats([p["r"] for p in part]).items()}
        s["argent_total"] = round(money, 2)
        print(f"--- {name} ---")
        for k, v in s.items():
            print(f"  {k:20s} {v}")
        print()

    by_day = defaultdict(list)
    for t in trades:
        by_day[t["cloture_utc"][:10]].append(t["r"])
    print("Par journee:")
    for day in sorted(by_day):
        rs = by_day[day]
        print(f"  {day}: {len(rs)} trades, {round(sum(rs), 2)} R, detail {[round(x, 2) for x in rs]}")

    print("\n15 derniers trades:")
    for t in trades[-15:]:
        print(f"  {t['cloture_utc']} {t['symbole']:8s} {t['sens']:6s} {t['lots']:>5s} lot  "
              f"{t['resultat_R']:>6s} R  {t['resultat_money']:>9s}  {t['raison']}")

    total = len(trades)
    if total < MIN_TRADES_POUR_CONCLURE:
        print(f"\nCAVEAT: {total} trades, c'est trop peu pour conclure. "
              f"En dessous d'environ {MIN_TRADES_POUR_CONCLURE} trades clotures, la difference entre "
              f"un systeme gagnant et un systeme perdant tient dans le bruit.")
    else:
        e = stats([t["r"] for t in trades])["esperance_R"]
        print(f"\nEsperance {e} R/trade sur {total} trades: "
              f"{'positive' if e > 0 else 'NEGATIVE -> ne pas grossir les mises'}")


if __name__ == "__main__":
    main()
