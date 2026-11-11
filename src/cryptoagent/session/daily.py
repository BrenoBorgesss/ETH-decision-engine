"""Sessão do dia: as respostas que você dá no início de cada dia de operação.

- Capital de hoje em R$ (base para o resultado em % e checagem contra o saldo).
- Perda máxima aceita hoje em R$ (base do limite diário e do risco por operação).
- Par em que vai operar (se o config não fixar) e onde mantém o saldo (BRL ou USDT).

Enquanto a sessão do dia não for respondida, o agente fica em ESPERAR.
Por segurança, a perda máxima pode ser REDUZIDA no meio do dia, mas não aumentada.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Literal

from cryptoagent.timeutils import local_trading_day, now_ms


class SessionError(ValueError):
    """Resposta inválida nas perguntas do dia."""
    pass


@dataclass(frozen=True)
class DailySession:
    """Sessão de um dia de operação: capital, perda máxima, par e moeda do saldo."""
    day: date
    capital_brl: float
    max_loss_brl: float
    par_operacao: str
    saldo_em: Literal["BRL", "USDT"]
    created_ms: int
    updated_ms: int


def validate(capital_brl: float, max_loss_brl: float, par_operacao: str, saldo_em: str, candidates: list[str]) -> None:
    """Confere as respostas do dia; levanta SessionError com o motivo se algo estiver errado."""
    if capital_brl <= 0:
        raise SessionError("o capital precisa ser maior que zero")
    if max_loss_brl <= 0:
        raise SessionError("a perda máxima precisa ser maior que zero")
    if max_loss_brl >= capital_brl:
        raise SessionError("a perda máxima precisa ser menor que o capital")
    if par_operacao not in candidates:
        raise SessionError(f"par {par_operacao} não está entre os candidatos {candidates}")
    if saldo_em not in ("BRL", "USDT"):
        raise SessionError("saldo_em deve ser BRL ou USDT")
    if par_operacao.endswith("BRL") and saldo_em != "BRL":
        raise SessionError(f"operando {par_operacao}, o saldo da operação fica em BRL")


def _row_to_session(row: sqlite3.Row) -> DailySession:
    return DailySession(
        day=date.fromisoformat(row["day"]),
        capital_brl=row["capital_brl"],
        max_loss_brl=row["max_loss_brl"],
        par_operacao=row["par_operacao"],
        saldo_em=row["saldo_em"],
        created_ms=row["created_ms"],
        updated_ms=row["updated_ms"],
    )


def today(tz_name: str, day_start: str, now: int | None = None) -> date:
    """Dia de operação atual (ou do instante `now`) no fuso local."""
    return local_trading_day(now if now is not None else now_ms(), tz_name, day_start)


def get_session(conn: sqlite3.Connection, day: date) -> DailySession | None:
    """Sessão gravada para o dia, ou None."""
    row = conn.execute("SELECT * FROM daily_sessions WHERE day = ?", (day.isoformat(),)).fetchone()
    return _row_to_session(row) if row else None


def last_session(conn: sqlite3.Connection) -> DailySession | None:
    """Sessão mais recente gravada, ou None."""
    row = conn.execute("SELECT * FROM daily_sessions ORDER BY day DESC LIMIT 1").fetchone()
    return _row_to_session(row) if row else None


def save_session(
    conn: sqlite3.Connection,
    day: date,
    *,
    capital_brl: float,
    max_loss_brl: float,
    par_operacao: str,
    saldo_em: str,
    candidates: list[str],
    now: int | None = None,
) -> DailySession:
    """Grava ou atualiza a sessão do dia. No mesmo dia, não deixa aumentar a perda máxima nem trocar o par."""
    validate(capital_brl, max_loss_brl, par_operacao, saldo_em, candidates)
    now = now if now is not None else now_ms()
    existing = get_session(conn, day)
    if existing is not None:
        if max_loss_brl > existing.max_loss_brl + 1e-9:
            raise SessionError(
                f"não é permitido AUMENTAR a perda máxima no meio do dia (hoje: R$ {existing.max_loss_brl:,.2f}). "
                "Só dá para reduzir. Isso evita aumentar o risco depois de perdas."
            )
        if par_operacao != existing.par_operacao:
            raise SessionError(f"o par de hoje já foi definido como {existing.par_operacao}; troque amanhã")
    conn.execute(
        """INSERT INTO daily_sessions (day, capital_brl, max_loss_brl, par_operacao, saldo_em, created_ms, updated_ms)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(day) DO UPDATE SET capital_brl=excluded.capital_brl, max_loss_brl=excluded.max_loss_brl,
               saldo_em=excluded.saldo_em, updated_ms=excluded.updated_ms""",
        (day.isoformat(), capital_brl, max_loss_brl, par_operacao, saldo_em, now, now),
    )
    conn.commit()
    session = get_session(conn, day)
    assert session is not None
    return session
