"""Registro dos sinais e associação entre as suas compras e os sinais.

Associação automática: uma compra sua no par de operação é ligada ao último COMPRAR emitido
até `window_min` minutos antes dela. Você confirma ou corrige no painel (ou pelo terminal).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from cryptoagent.engine.decision import Decision


def save_signal(
    conn: sqlite3.Connection,
    d: Decision,
    *,
    ratio: float,
    mode: str,
    data_ages: dict[str, float] | None = None,
    latency_ms: float | None = None,
    variant: str | None = None,
    market_extras: dict | None = None,
) -> int:
    """Grava a decisão. Preços e níveis vão no par de OPERAÇÃO (multiplicados por `ratio`)."""
    lv = d.levels

    def t(x: float | None) -> float | None:
        return x * ratio if x is not None else None

    details = {
        "parts": [asdict(p) for p in d.parts],
        "reasons_for": d.reasons_for,
        "reasons_against": d.reasons_against,
        "blocks": d.blocks,
        "signals": [
            {"name": s.name, "score": s.score, "weight": s.weight, "detail": s.detail, "available": s.available}
            for s in d.signals
        ],
        "trigger": asdict(d.trigger) if d.trigger else None,
        "invalidation": d.invalidation,
        "position_note": d.position_note,
        "analysis_price": d.price,
        "ratio": ratio,
        # custo de ida e volta usado nos níveis (%): o relatório só conta "alvo" como acerto se o alvo pagar isso
        "cost_pct": (lv.target_distance_pct - lv.reward_net_pct) if lv else None,
        # dados que não têm histórico (ex.: order book): guardados para avaliar depois no paper trading
        "market": market_extras or {},
    }
    cur = conn.execute(
        """INSERT INTO signals (time_ms, mode, symbol, trade_symbol, action, confidence, regime, price, entry, stop, target1, target2,
               rr, size_brl, exit_reason, details_json, data_ages_json, latency_ms, variant)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            d.time_ms,
            mode,
            d.symbol,
            d.trade_symbol,
            d.action,
            d.confidence,
            d.regime,
            t(d.price),
            t(lv.entry) if lv else None,
            t(lv.stop) if lv else None,
            t(lv.target1) if lv else None,
            t(lv.target2) if lv else None,
            lv.rr if lv else None,
            lv.size_brl if lv else None,
            d.exit_reason,
            json.dumps(details, ensure_ascii=False),
            json.dumps(data_ages or {}),
            latency_ms,
            variant,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def auto_associate(conn: sqlite3.Connection, trade_symbol: str, window_min: float = 30) -> int:
    """Liga compras ainda 'pendente' ao último COMPRAR até `window_min` antes. Retorna quantas foram ligadas."""
    linked = 0
    buys = conn.execute(
        "SELECT trade_id, time_ms FROM account_trades WHERE symbol = ? AND is_buyer = 1 AND association = 'pendente'",
        (trade_symbol,),
    ).fetchall()
    for b in buys:
        sig = conn.execute(
            """SELECT id FROM signals WHERE trade_symbol = ? AND action = 'COMPRAR' AND mode = 'ao_vivo'
               AND time_ms <= ? AND time_ms >= ? ORDER BY time_ms DESC LIMIT 1""",
            (trade_symbol, b["time_ms"], b["time_ms"] - int(window_min * 60_000)),
        ).fetchone()
        status = "automatica" if sig else "sem_sinal"
        conn.execute(
            "UPDATE account_trades SET signal_id = ?, association = ? WHERE symbol = ? AND trade_id = ?",
            (str(sig["id"]) if sig else None, status, trade_symbol, b["trade_id"]),
        )
        linked += int(sig is not None)
    conn.commit()
    return linked


def set_association(conn: sqlite3.Connection, trade_symbol: str, trade_id: int, signal_id: int | None) -> None:
    """Você confirma (mesmo sinal) ou corrige (outro sinal / nenhum) a associação de uma compra."""
    row = conn.execute(
        "SELECT signal_id FROM account_trades WHERE symbol = ? AND trade_id = ?", (trade_symbol, trade_id)
    ).fetchone()
    if row is None:
        raise ValueError(f"operação {trade_id} não encontrada em {trade_symbol}")
    if signal_id is not None and conn.execute("SELECT 1 FROM signals WHERE id = ?", (signal_id,)).fetchone() is None:
        raise ValueError(f"sinal #{signal_id} não existe")
    status = "confirmada" if row["signal_id"] == (str(signal_id) if signal_id is not None else None) else "corrigida"
    conn.execute(
        "UPDATE account_trades SET signal_id = ?, association = ? WHERE symbol = ? AND trade_id = ?",
        (str(signal_id) if signal_id is not None else None, status, trade_symbol, trade_id),
    )
    conn.commit()


def levels_for_open_lots(
    conn: sqlite3.Connection, trade_symbol: str, open_trade_ids: list[str]
) -> tuple[float, float, float, str] | None:
    """Stop e alvos (no par de operação) do sinal associado à compra aberta mais recente, se houver."""
    for trade_id in reversed(open_trade_ids):
        if not trade_id.isdigit():  # registros do diário manual não têm sinal associado
            continue
        row = conn.execute(
            """SELECT s.id, s.stop, s.target1, s.target2 FROM account_trades a JOIN signals s ON s.id = CAST(a.signal_id AS INTEGER)
               WHERE a.symbol = ? AND a.trade_id = ? AND a.association IN ('automatica', 'confirmada', 'corrigida')""",
            (trade_symbol, int(trade_id)),
        ).fetchone()
        if row and row["stop"] and row["target1"]:
            return row["stop"], row["target1"], row["target2"] or row["target1"], f"sinal #{row['id']}"
    return None
