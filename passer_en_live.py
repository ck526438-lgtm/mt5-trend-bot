"""Bascule config.json en mode live, avec double validation explicite de ta part.

Affiche d'abord ce que le backtest dit vraiment, puis demande de taper LIVE.
Ensuite le bot refuse quand meme si le compte MT5 connecte n'est pas un compte reel
ou si la porte du backtest est fermee (sauf override_backtest_gate).
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "config.json")
SUMMARY = os.path.join(HERE, "backtest_summary.json")


def main() -> None:
    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)

    print("=" * 62)
    print("PASSAGE EN TRADING REEL")
    print("=" * 62)

    if os.path.exists(SUMMARY):
        with open(SUMMARY, encoding="utf-8") as f:
            s = json.load(f)
        print("\nCe que le backtest dit sur ces reglages:")
        for row in s.get("per_symbol", []):
            print(f"  {row.get('symbole')}: esperance {row.get('expectance_R')} R/trade, "
                  f"profit factor {row.get('profit_factor_R')}, {row.get('trades')} trades, "
                  f"drawdown {row.get('drawdown_max_pct')} %")
        bad = [r for r in s.get("per_symbol", [])
               if r.get("expectance_R", 0) <= 0 or r.get("profit_factor_R", 0) < 1.2]
        if bad:
            print("\n  >>> Ces reglages n'ONT PAS de resultat positif prouve.")
            print("  >>> Trader reel dessus = parier de l'argent sans avantage mesure.")
            answer = input("\nTaper OUI pour assumer quand meme: ").strip().upper()
            if answer != "OUI":
                print("Annule, rien n'a ete change.")
                return
            cfg["live"]["override_backtest_gate"] = True
    else:
        print("\nAucun backtest trouve: lance run_backtest.bat avant de trader en reel.")
        return

    cfg["mode"] = "live"
    cfg["live"]["confirm_live"] = True
    typed = input("\nTape exactement LIVE pour confirmer le trading a l'argent reel: ").strip()
    if typed != "LIVE":
        print("Confirmation absente: config laisse en mode '" + cfg.get("mode", "?") + "'.")
        raise SystemExit(0)

    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print("config.json: mode=live, confirm_live=true. Le bot enverra des ordres reels.")
    print("Retour en mode papier: passe \"mode\" a \"paper\" dans config.json.\n")
    subprocess.run([sys.executable, os.path.join(HERE, "bot.py")])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAnnule.")
