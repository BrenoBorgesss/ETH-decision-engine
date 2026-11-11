"""Relatório do paper trading: os sinais registrados x o que o mercado fez depois.

1. Operações virtuais: mesmas métricas do backtest (já com taxas e slippage).
2. Cada sinal com níveis (COMPRAR, e também os ESPERAR que tinham entrada/stop/alvo calculados):
   nas `horizonte` horas seguintes, o preço tocou primeiro o ALVO 1, primeiro o STOP, ou nenhum?
   Alvo que não paga o custo de ida e volta conta à parte ("alvo sem lucro"): tocar nele não dá dinheiro.
   Isso mostra se as travas estão evitando operações ruins (ou boas).
3. Calibração da confiança: a % de "alvo com lucro" sobe junto com a confiança?
4. Comparação com a expectativa do backtest.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from cryptoagent.backtest.execution import oco_check
from cryptoagent.backtest.metrics import compute_metrics
from cryptoagent.engine.decision import COST_GATES
from cryptoagent.paper.book import to_backtest_trades
from cryptoagent.timeutils import now_ms

BINS = [(0, 40), (40, 60), (60, 80), (80, 101)]


@dataclass
class SignalOutcome:
    """O que o mercado fez depois de um sinal: desfecho (ver OUTCOMES) e os movimentos máximos a favor e contra."""
    signal_id: int
    action: str
    confidence: float
    outcome: str  # ver OUTCOMES
    mfe_pct: float  # maior alta depois do sinal (vs entrada)
    mae_pct: float  # maior queda depois do sinal


# "alvo sem lucro" = o preço chegou ao alvo, mas o alvo não pagava o custo de ida e volta: na prática, perda.
OUTCOMES = ("alvo com lucro", "alvo sem lucro", "stop primeiro", "nenhum", "aguardando")


def signal_cost_pct(s: object) -> float | None:
    """Custo de ida e volta (%) usado nos níveis do sinal. Sinais antigos não o guardavam: deduz do risco/retorno
    gravado (rr = (alvo − custo) / (stop + custo), com alvo e stop em % da entrada)."""
    raw = getattr(s, "details_json", None)
    if isinstance(raw, str):
        cost = json.loads(raw).get("cost_pct")
        if cost is not None:
            return float(cost)
    rr = getattr(s, "rr", None)
    if rr is None or not pd.notna(rr):
        return None
    a, b = (s.target1 / s.entry - 1) * 100, (1 - s.stop / s.entry) * 100
    return (a - rr * b) / (1 + rr)


def evaluate_signals(signals: pd.DataFrame, bars_1m: pd.DataFrame, horizon_h: float, now: int | None = None,
                     default_cost_pct: float = 0.0) -> list[SignalOutcome]:
    """Para cada sinal com entrada/stop/alvo: o que aconteceu nas `horizon_h` horas seguintes (candles de 1m do par).
    Chegar ao alvo só conta como acerto se o alvo pagar o custo de ida e volta."""
    now = now or now_ms()
    t = bars_1m["open_time"].to_numpy()
    out = []
    for s in signals.itertuples():
        # NULL do banco vira NaN, e NaN conta como "verdadeiro" em Python: por isso pd.notna
        if not all(pd.notna(x) and x > 0 for x in (s.entry, s.stop, s.target1)):
            continue
        a = int(np.searchsorted(t, s.time_ms, side="left"))
        end = s.time_ms + int(horizon_h * 3_600_000)
        b = int(np.searchsorted(t, end, side="left"))
        if b <= a:
            continue
        hi, lo, op = (bars_1m[c].to_numpy()[a:b] for c in ("high", "low", "open"))
        hit = oco_check(op, hi, lo, stop=s.stop, target=s.target1, half_spread=0.0, slip_stop_pct=0.0)
        if hit is not None:
            if hit.reason == "alvo1":
                cost = signal_cost_pct(s)
                cost = default_cost_pct if cost is None else cost
                outcome = "alvo com lucro" if (s.target1 / s.entry - 1) * 100 > cost else "alvo sem lucro"
            else:
                outcome = "stop primeiro"
        else:
            outcome = "aguardando" if end > now else "nenhum"
        out.append(SignalOutcome(int(s.id), s.action, float(s.confidence), outcome, (hi.max() / s.entry - 1) * 100, (lo.min() / s.entry - 1) * 100))
    return out


@dataclass
class VariantReport:
    """Relatório de uma variante do paper trading: patrimônio, métricas, desfecho dos sinais e calibração."""
    variant: str
    started_ms: int
    equity: float
    open_trade: dict | None
    metrics: dict
    signals_by_action: dict[str, int]
    outcomes: dict[str, dict[str, int]] = field(default_factory=dict)  # ação -> resultado -> quantidade
    calibration: list[tuple[str, int, float]] = field(default_factory=list)  # faixa, n, % alvo com lucro
    backtest_expectancy_pct: float | None = None
    backtest_trades: int = 0
    enough: bool = False
    gates: list[GateStat] = field(default_factory=list)  # só na variante de estudo


GATE_PT = {
    "regime": "regime bloqueado",
    "tendencia": "filtro de tendência",
    "esticado": "preço não esticado",
    "gatilho": "gatilho anti-faca",
    "distancia_alvo": "alvo perto demais (custo)",
    "risco_retorno": "risco/retorno baixo",
    "confianca": "confiança abaixo do mínimo",
}


@dataclass
class GateStat:
    """Operações de estudo que a trava teria BARRADO x as que ela deixaria passar."""

    gate: str
    n_blocked: int
    win_blocked_pct: float | None
    avg_blocked_pct: float | None
    n_passed: int
    win_passed_pct: float | None
    avg_passed_pct: float | None


def _group(pnls: list[float]) -> tuple[int, float | None, float | None]:
    if not pnls:
        return 0, None, None
    return len(pnls), sum(p > 0 for p in pnls) / len(pnls) * 100, float(np.mean(pnls))


def gate_stats(rows: list[sqlite3.Row]) -> list[GateStat]:
    """Para cada trava de estratégia: resultado médio (% líquido, com taxas) com e sem ela ter disparado."""
    trades = [(json.loads(r["bypassed_json"]), float(r["pnl_pct"])) for r in rows if r["bypassed_json"] is not None and r["pnl_pct"] is not None]
    out = []
    for key in GATE_PT:
        if key in COST_GATES:
            continue  # nunca ignoradas no estudo: não há o que comparar
        blocked = [p for g, p in trades if key in g]
        passed = [p for g, p in trades if key not in g]
        if blocked or passed:
            out.append(GateStat(key, *_group(blocked), *_group(passed)))
    return out


def variant_report(conn: sqlite3.Connection, variant: str, capital: float, bars_1m: pd.DataFrame, horizon_h: float, min_trades: int,
                   backtest_json: dict | None, trade_symbol: str) -> VariantReport:
    """Monta o relatório de uma variante a partir do banco e dos candles de 1m do par de operação."""
    st = conn.execute("SELECT * FROM paper_state WHERE variant = ?", (variant,)).fetchone()
    started = int(st["started_ms"]) if st else now_ms()
    rows = conn.execute("SELECT * FROM paper_trades WHERE variant = ? AND exit_ms IS NOT NULL ORDER BY entry_ms", (variant,)).fetchall()
    open_row = conn.execute("SELECT * FROM paper_trades WHERE variant = ? AND exit_ms IS NULL", (variant,)).fetchone()
    trades = to_backtest_trades(rows)
    metrics = compute_metrics(trades, started, now_ms(), capital)
    sig = pd.read_sql_query("SELECT * FROM signals WHERE mode = 'paper' AND variant = ? ORDER BY time_ms", conn, params=(variant,))
    rep = VariantReport(
        variant, started, float(st["equity"]) if st else capital, dict(open_row) if open_row else None, metrics,
        sig["action"].value_counts().to_dict() if not sig.empty else {},
    )
    outs = evaluate_signals(sig, bars_1m, horizon_h)
    for o in outs:
        key = o.action if o.action != "ESPERAR" else "ESPERAR (com níveis)"
        rep.outcomes.setdefault(key, {}).setdefault(o.outcome, 0)
        rep.outcomes[key][o.outcome] += 1
    decided = [o for o in outs if o.outcome in ("alvo com lucro", "alvo sem lucro", "stop primeiro")]
    for lo, hi in BINS:
        group = [o for o in decided if lo <= o.confidence < hi]
        if group:
            rep.calibration.append((f"{lo}–{min(hi, 100)}%", len(group), sum(o.outcome == "alvo com lucro" for o in group) / len(group) * 100))
    if backtest_json and trade_symbol in backtest_json:
        bm = (backtest_json[trade_symbol].get("padrao") or {}).get("metricas", {})
        rep.backtest_expectancy_pct = bm.get("expectativa_pct")
        rep.backtest_trades = int(bm.get("operacoes") or 0)
    rep.enough = metrics["operacoes"] >= min_trades
    rep.gates = gate_stats(rows)
    return rep


def load_backtest_json(path: Path) -> dict | None:
    """Lê o relatório JSON do backtest, ou None se o arquivo não existe."""
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
