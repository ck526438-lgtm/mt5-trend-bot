"""Bot de trading: lit les bougies M15 cloturees, applique la strategie, gere le risque.

mode "paper"  -> signaux + suivi virtuel sur les prix reels (aucun ordre envoye)
mode "live"   -> ordres reels via le terminal MT5, sous trois portes de securite

Arret d'urgence: creer un fichier vide nomme STOP dans ce dossier -> le bot s'arrete
proprement sans couper les positions ouvertes (leur stop reste active chez le broker).
"""

import csv
import json
import os
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd

import mt5_client as m
import risk as rk
import strategy as st

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "paper_state.json")
TRADES_CSV = os.path.join(HERE, "trades.csv")
LOG_PATH = os.path.join(HERE, "bot.log")
STOP_FILE = os.path.join(HERE, "STOP")
SUMMARY_PATH = os.path.join(HERE, "backtest_summary.json")
POLL_SECONDS = 2.0

CONTRACTS: dict[str, float] = {}
METAS: dict[str, dict] = {}


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}Z | {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    if cfg["mode"] not in ("paper", "live"):
        raise SystemExit(f"mode inconnu: {cfg['mode']!r} (attendu 'paper' ou 'live')")
    return cfg


@dataclass
class Position:
    ticket: str
    symbol: str
    direction: int
    lots: float
    entry: float
    sl: float
    sl_dist: float
    opened_at: str
    contract: float = 100.0
    tags: list = field(default_factory=list)


def record_trade(pos: Position, exit_price: float, why: str, pnl: float, r: float, mode: str) -> None:
    exists = os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["mode", "cloture_utc", "symbole", "sens", "lots", "entree", "sortie",
                        "distance_stop", "resultat_R", "resultat_money", "raison", "tags"])
        w.writerow([mode, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), pos.symbol,
                    "ACHAT" if pos.direction == 1 else "VENTE", pos.lots, round(pos.entry, 5),
                    round(exit_price, 5), round(pos.sl_dist, 5), round(r, 2), round(pnl, 2),
                    why, " ".join(str(t) for t in pos.tags)])


class LiveBroker:
    def __init__(self, cfg: dict):
        self.magic = int(cfg["live"]["magic"])
        self.deviation = int(cfg["live"]["deviation_points"])

    def positions(self) -> list[Position]:
        out = []
        for p in m.bot_positions(self.magic):
            dist = abs(p["open_price"] - p["sl"]) if p["sl"] else 0.0
            out.append(Position(str(p["ticket"]), p["symbol"], p["direction"], p["lots"],
                                p["open_price"], p["sl"], dist, p["time"].isoformat(),
                                CONTRACTS.get(p["symbol"], 100.0), [p["comment"]]))
        return out

    def open(self, meta: dict, direction: int, lots: float, sl: float, tags: list) -> Position | None:
        try:
            r = m.open_position(meta, direction, lots, sl, 0.0, self.deviation, self.magic,
                                " ".join(str(t) for t in tags)[:31])
        except m.MT5Error as exc:
            log(f"ORDRE REFUSE {meta['symbol']}: {exc}")
            return None
        if not r.ok:
            log(f"ECHEC ORDRE {meta['symbol']}: {r.error}")
            return None
        d = meta["digits"]
        log(f"ORDRE REEL #{r.order} {meta['symbol']} {'ACHAT' if direction == 1 else 'VENTE'} "
            f"{lots} lot(s) a {r.price:.{d}f} SL {sl:.{d}f}")
        return Position(str(r.order), meta["symbol"], direction, lots, r.price, sl,
                        abs(r.price - sl), datetime.now(timezone.utc).isoformat(),
                        float(meta["contract_size"]), tags)

    def close(self, pos: Position, meta: dict, why: str) -> bool:
        r = m.close_position(int(pos.ticket), meta, pos.direction, pos.lots, self.deviation, self.magic)
        if not r.ok:
            log(f"ECHEC CLOTURE {pos.symbol}: {r.error}")
            return False
        money, price = m.deal_profit(int(pos.ticket))
        price = price or r.price
        rr = (price - pos.entry) * pos.direction / pos.sl_dist if pos.sl_dist else 0.0
        log(f"CLoture REELLE {pos.symbol} #{r.order} a {price:.{meta['digits']}f} ({why}) | "
            f"{money:+.2f} {meta['currency']}")
        record_trade(pos, price, why, money, rr, "live")
        return True

    def modify_sl(self, pos: Position, meta: dict, new_sl: float) -> None:
        if m.modify_stop(int(pos.ticket), meta, new_sl):
            pos.sl = new_sl
        else:
            log(f"Stop suiveur NON applique sur {pos.symbol}: {m.mt5.last_error()}")

    def equity(self) -> float:
        return m.equity()


class PaperBroker:
    """Meme pipeline de decision, remplissage aux prix reels, capital virtuel."""

    def __init__(self, cfg: dict):
        self.start = float(cfg["paper"]["start_balance"])
        self.realized = 0.0
        self.fills: list[Position] = []
        self.next_ticket = 1
        self._restore()

    def _restore(self) -> None:
        if not os.path.exists(STATE_PATH):
            return
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                s = json.load(f)
            self.realized = float(s.get("realized", 0.0))
            self.next_ticket = int(s.get("next_ticket", 1))
            self.start = float(s.get("start_balance", self.start))
            self.fills = [Position(**p) for p in s.get("positions", [])]
            log(f"Etat papier repris: {len(self.fills)} position(s), solde virtuel {self.equity():.2f}")
        except Exception as exc:
            log(f"Etat papier illegible, on repart de zero: {exc}")

    def save(self) -> None:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"realized": self.realized, "next_ticket": self.next_ticket,
                       "start_balance": self.start,
                       "positions": [asdict(p) for p in self.fills],
                       "updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)

    def positions(self) -> list[Position]:
        return list(self.fills)

    def open(self, meta: dict, direction: int, lots: float, sl: float, tags: list) -> Position:
        bid, ask = m.tick(meta["symbol"])
        entry = ask if direction == 1 else bid
        d = meta["digits"]
        pos = Position(f"P{self.next_ticket}", meta["symbol"], direction, lots, entry,
                       sl, abs(entry - sl), datetime.now(timezone.utc).isoformat(),
                       float(meta["contract_size"]), tags)
        self.next_ticket += 1
        self.fills.append(pos)
        self.save()
        log(f"SIGNAL PAPIER {meta['symbol']} {'ACHAT' if direction == 1 else 'VENTE'} "
            f"{lots} lot(s) a {entry:.{d}f} SL {sl:.{d}f} | {' | '.join(str(t) for t in tags)}")
        return pos

    def close(self, pos: Position, meta: dict, why: str) -> bool:
        bid, ask = m.tick(pos.symbol)
        price = bid if pos.direction == 1 else ask
        pnl = (price - pos.entry) * pos.direction * pos.lots * pos.contract
        rr = (price - pos.entry) * pos.direction / pos.sl_dist if pos.sl_dist else 0.0
        self.realized += pnl
        self.fills = [p for p in self.fills if p.ticket != pos.ticket]
        self.save()
        log(f"CLoture papier {pos.symbol} a {price:.{meta['digits']}f} ({why}) | "
            f"{pnl:+.2f} = {rr:+.2f} R | solde virtuel {self.virtual_balance():.2f}")
        record_trade(pos, price, why, pnl, rr, "paper")
        return True

    def modify_sl(self, pos: Position, meta: dict, new_sl: float) -> None:
        pos.sl = new_sl
        self.save()

    def virtual_balance(self) -> float:
        return self.start + self.realized

    def equity(self) -> float:
        floating = 0.0
        for p in self.fills:
            bid, ask = m.tick(p.symbol)
            price = bid if p.direction == 1 else ask
            floating += (price - p.entry) * p.direction * p.lots * p.contract
        return self.virtual_balance() + floating


def backtest_gate(cfg: dict) -> tuple[bool, str]:
    """Le live est bloque tant que le backtest n'est pas positif sur ces symboles."""
    if not os.path.exists(SUMMARY_PATH):
        return False, "aucun backtest_summary.json: lance run_backtest.bat avant le live"
    try:
        with open(SUMMARY_PATH, encoding="utf-8") as f:
            s = json.load(f)
    except Exception as exc:
        return False, f"backtest_summary.json illisible ({exc})"

    rows = [r for r in s.get("per_symbol", []) if r.get("symbole") in cfg["symbols"]]
    if not rows:
        return False, "le backtest ne couvre aucun des symboles du config"
    detail = [f"{r['symbole']}: esperance {r.get('expectance_R', 0)} R/trade, "
              f"profit factor {r.get('profit_factor_R', 0)}, {r.get('trades', 0)} trades" for r in rows]
    bad = [r for r in rows if r.get("expectance_R", 0) <= 0 or r.get("profit_factor_R", 0) < 1.2]
    return (not bad), " | ".join(detail)


def startup(cfg: dict) -> LiveBroker | PaperBroker:
    info = m.connect(int(cfg["live"].get("account_login") or 0))
    log(f"Terminal {info['trade_mode_name']} | compte {info['login']} @ {info['server']} | "
        f"equilibre {info['equity']} {info['currency']} | levier 1:{info['leverage']} | "
        f"connecte={info['connected']}")

    for symbol in cfg["symbols"]:
        METAS[symbol] = m.symbol_meta(symbol)
        CONTRACTS[symbol] = float(METAS[symbol]["contract_size"])

    if cfg["mode"] == "paper":
        return PaperBroker(cfg)

    if not cfg["live"].get("confirm_live"):
        raise SystemExit("mode 'live' dans config.json mais confirm_live=false. "
                         "Passe mode a 'paper', ou mets confirm_live a true quand ta decision est prise.")
    if info["trade_mode"] != 1:
        raise SystemExit(f"Le compte connecte est un compte {info['trade_mode_name']}: connecte ton "
                         f"compte REEL dans MetaTrader 5 avant d'exiger des ordres reels.")
    if cfg["live"].get("override_backtest_gate"):
        log("ATTENTION: porte du backtest forcee (override_backtest_gate=true). "
            "Aucune preuve de rentabilite sur ces reglages, risque de perte reel.")
        return LiveBroker(cfg)
    ok, why = backtest_gate(cfg)
    if not ok:
        raise SystemExit(f"LIVE BLOQUE par la porte de securite -> {why}\n"
                         f"Exigence: esperance > 0 R et profit factor >= 1,2 par symbole.\n"
                         f"Choix: rester en mode 'paper', ameliorer la strategie, ou assumer le risque "
                         f'avec "override_backtest_gate": true dans la section live de config.json.')
    log(f"Porte du backtest ouverte: {why}")
    return LiveBroker(cfg)


def process_bar(cfg: dict, symbol: str, meta: dict, df: pd.DataFrame,
                broker, guard: rk.DailyGuard) -> None:
    row = df.iloc[-1]
    digits = meta["digits"]
    mine = [p for p in broker.positions() if p.symbol == symbol]

    for pos in mine:
        if st.trend_broken(pos.direction, row):
            broker.close(pos, meta, "tendance cassee (EMA)")
            continue
        atr_ = float(row["atr"]) if not pd.isna(row["atr"]) else pos.sl_dist
        new_sl = round(st.trail_stop(pos.direction, pos.sl, float(row["close"]), atr_, cfg), digits)
        min_dist = float(meta.get("trade_stops_level", 0)) * float(meta["point"])
        if min_dist and abs(float(row["close"]) - new_sl) < min_dist:
            continue
        if abs(new_sl - pos.sl) > 0.5 * (10 ** -digits):
            log(f"Stop suiveur {symbol}: {pos.sl:.{digits}f} -> {new_sl:.{digits}f}")
            broker.modify_sl(pos, meta, new_sl)

    if mine:
        return

    sig = st.entry_signal(df, cfg, symbol)
    if not sig.is_trade:
        log(f"{symbol} bougie {sig.bar_time}: pas de trade ({sig.reason})")
        return

    inside, hour_msg = rk.in_session(cfg)
    if not inside:
        log(f"{symbol} entree ignoree: {hour_msg} -> hors plage {cfg['risk']['trade_hours_utc_start']:02d}-"
            f"{cfg['risk']['trade_hours_utc_end']:02d} UTC")
        return

    bid, ask = m.tick(symbol)
    spread_pts = rk.spread_points(meta, bid, ask)
    if not rk.spread_ok(cfg, symbol, spread_pts):
        log(f"{symbol} entree ignoree: spread {spread_pts:.0f} pts > limite "
            f"{cfg['risk']['max_spread_points'].get(symbol)}")
        return

    if len(broker.positions()) >= int(cfg["risk"]["max_open_positions"]):
        log(f"{symbol} entree ignoree: {len(broker.positions())} position(s) deja ouverte(s)")
        return

    eq = broker.equity()
    ok, why = guard.check(eq)
    if not ok:
        log(f"{symbol} entree ignoree: {why}")
        return

    est_entry = ask if sig.direction == 1 else bid
    sl, pulled = rk.clamp_stop_to_broker(sig.stop + (est_entry - sig.price), est_entry,
                                         sig.direction, meta)
    sizing = rk.size_position(eq, cfg, meta, sl, est_entry)
    if sizing.lots <= 0:
        log(f"{symbol} entree ignoree: dimensionnement impossible ({sizing.note})")
        return

    target_risk = eq * float(cfg["risk"]["risk_pct_per_trade"]) / 100.0
    if sizing.risk_money > target_risk * 1.5:
        log(f"{symbol} entree REFUSEE: risque {sizing.risk_money:.2f} = "
            f"{sizing.risk_money / eq * 100:.2f} % > visee {cfg['risk']['risk_pct_per_trade']} % "
            f"(lot minimum impose). {sizing.note}")
        return

    side = "ACHAT" if sig.direction == 1 else "VENTE"
    log(f"{symbol} SIGNAL {side} cours {est_entry:.{digits}f} (cloture {sig.price:.{digits}f}) "
        f"stop {sl:.{digits}f} (dist {sizing.stop_distance:.{digits}f}) lots {sizing.lots} "
        f"risque {sizing.risk_money:.2f} = {sizing.risk_money / eq * 100:.2f} % | "
        f"{' | '.join(str(t) for t in sig.tags)}")
    if pulled:
        log(f"{symbol} stop rapproche a la distance minimale imposee par le broker")

    if broker.open(meta, sig.direction, sizing.lots, sl, sig.tags) is not None:
        guard.trades += 1


def fetch_frame(symbol: str, cfg: dict) -> tuple[pd.DataFrame, datetime]:
    tf = cfg["timeframe"]
    df, bar_dt = m.last_closed_bar(symbol, tf, 300)
    df = st.compute_indicators(df, cfg)
    htf_tf = str(cfg["strategy"].get("htf_timeframe") or "")
    if htf_tf:
        h = st.compute_indicators(m.bars(symbol, htf_tf, 300), cfg)
        df = st.attach_htf_trend(df, st.htf_trend(h))
    return df, bar_dt


def main() -> None:
    cfg = load_config()
    broker = startup(cfg)
    guard = rk.DailyGuard(max_loss_pct=float(cfg["risk"]["max_daily_loss_pct"]),
                          max_trades=int(cfg["risk"]["max_trades_per_day"]))
    seen: dict[str, datetime] = {}
    last_heartbeat = 0.0

    r = cfg["risk"]
    log(f"Mode {cfg['mode'].upper()} demarre | {', '.join(cfg['symbols'])} {cfg['timeframe']} | "
        f"risque {r['risk_pct_per_trade']} %/trade | stop journalier {r['max_daily_loss_pct']} % | "
        f"max {r['max_open_positions']} positions | {r['trade_hours_utc_start']:02d}-"
        f"{r['trade_hours_utc_end']:02d} UTC | arret: creer le fichier STOP")

    while True:
        try:
            if os.path.exists(STOP_FILE):
                log("Fichier STOP detecte -> arret propre (les positions gardent leur stop chez le broker)")
                break

            now = datetime.now(timezone.utc)
            eq = broker.equity()
            guard.roll_day(eq, now)
            ok, why = guard.check(eq)
            if not ok and guard.halted:
                log(f"COUPE JOURNALIERE ACTIVE: {why}")

            for symbol in cfg["symbols"]:
                df, bar_dt = fetch_frame(symbol, cfg)
                if seen.get(symbol) == bar_dt:
                    continue
                seen[symbol] = bar_dt
                log(f"{symbol} bougie {cfg['timeframe']} close {bar_dt:%Y-%m-%d %H:%M} UTC | "
                    f"equilibre {eq:.2f} | {why}")
                process_bar(cfg, symbol, METAS[symbol], df, broker, guard)

            if isinstance(broker, PaperBroker):
                broker.save()

            if now.timestamp() - last_heartbeat > 900:
                last_heartbeat = now.timestamp()
                log(f"BATTEMENT: {len(broker.positions())} position(s), "
                    f"equilibre {broker.equity():.2f}, {why}")

            m.wait_seconds(POLL_SECONDS)
        except KeyboardInterrupt:
            log("Interrompu (Ctrl+C) -> arret")
            break
        except SystemExit:
            raise
        except Exception as exc:
            log(f"ERREUR: {exc} -> relance dans 15 s\n{traceback.format_exc()[-1200:]}")
            m.wait_seconds(15)

    if isinstance(broker, PaperBroker):
        broker.save()
    m.shutdown()


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        print(exc)
        sys.exit(1)
