"""Banco local do agente: operações da conta, diário manual, sessões do dia e sinais ao vivo.

As tabelas `paper_*` continuam aqui só para guardar o histórico antigo do paper trading; a simulação
agora usa um banco próprio (`storage/sim_db.py`).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SIGNALS_TABLE = """
-- Cada análise do motor de decisão (também as ESPERAR), com idade dos dados e latência.
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time_ms INTEGER NOT NULL,
    mode TEXT NOT NULL,              -- ao_vivo / backtest / paper
    symbol TEXT NOT NULL,            -- par de análise
    trade_symbol TEXT NOT NULL,      -- par de operação
    action TEXT NOT NULL,            -- COMPRAR / VENDER / ESPERAR
    confidence REAL NOT NULL,
    regime TEXT,
    price REAL,                      -- preço no par de operação
    entry REAL, stop REAL, target1 REAL, target2 REAL,   -- no par de operação
    rr REAL, size_brl REAL,
    exit_reason TEXT,
    details_json TEXT NOT NULL,      -- motivos, travas, parcelas da confiança, sinais
    data_ages_json TEXT,             -- idade de cada dado no momento da análise
    latency_ms REAL,                 -- tempo total para montar e decidir
    variant TEXT                     -- variante do paper trading (vazio no modo ao vivo)
);
CREATE INDEX IF NOT EXISTS ix_signals_time ON signals(trade_symbol, time_ms);
"""

AGENT_SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Operações executadas lidas da Binance (somente leitura). Uma linha por execução (trade).
CREATE TABLE IF NOT EXISTS account_trades (
    symbol TEXT NOT NULL,
    trade_id INTEGER NOT NULL,
    order_id INTEGER,
    time_ms INTEGER NOT NULL,
    is_buyer INTEGER NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    quote_qty REAL NOT NULL,
    commission REAL NOT NULL,
    commission_asset TEXT NOT NULL,
    is_maker INTEGER,
    fee_quote REAL NOT NULL,         -- taxa convertida para a moeda de cotação do par
    fee_estimated INTEGER NOT NULL,  -- 1 = não deu para converter; taxa estimada pelo config
    signal_id TEXT,                  -- sinal associado (automático ou confirmado por você)
    association TEXT NOT NULL DEFAULT 'pendente',  -- pendente / automatica / confirmada / corrigida
    PRIMARY KEY (symbol, trade_id)
);
CREATE INDEX IF NOT EXISTS ix_account_trades_time ON account_trades(symbol, time_ms);

-- Contingência: operações registradas à mão quando não há chave.
CREATE TABLE IF NOT EXISTS manual_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    time_ms INTEGER NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty REAL NOT NULL CHECK (qty > 0),
    price REAL NOT NULL CHECK (price > 0),
    fee_quote REAL NOT NULL CHECK (fee_quote >= 0),
    note TEXT,
    created_ms INTEGER NOT NULL
);

"""
    + SIGNALS_TABLE
    + """

-- Paper trading: operações VIRTUAIS (sem dinheiro real), por variante da estratégia.
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variant TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_id INTEGER,
    entry_ms INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    qty REAL NOT NULL,
    size REAL NOT NULL,
    fee_in REAL NOT NULL,
    stop REAL, target1 REAL, target2 REAL,   -- no par de operação
    confidence REAL, regime TEXT,
    equity_at_entry REAL NOT NULL,
    checked_until_ms INTEGER NOT NULL,       -- até onde a OCO virtual já foi conferida
    exit_ms INTEGER, exit_price REAL, exit_reason TEXT,
    pnl REAL, pnl_pct REAL, fees REAL, ret_equity REAL
);
CREATE INDEX IF NOT EXISTS ix_paper_trades_variant ON paper_trades(variant, entry_ms);

CREATE TABLE IF NOT EXISTS paper_state (
    variant TEXT PRIMARY KEY,
    equity REAL NOT NULL,
    day TEXT,
    day_start_equity REAL,
    last_decision_close_ms INTEGER,
    started_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_sessions (
    day TEXT PRIMARY KEY,            -- dia de operação no horário local (AAAA-MM-DD)
    capital_brl REAL NOT NULL,
    max_loss_brl REAL NOT NULL,
    par_operacao TEXT NOT NULL,
    saldo_em TEXT NOT NULL,
    created_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL
);
"""
)


def connect(path: Path) -> sqlite3.Connection:
    """Abre (criando se preciso) o banco do agente, com as tabelas e colunas atualizadas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(AGENT_SCHEMA)
    _ensure_column(conn, "signals", "variant", "TEXT")  # bancos criados antes do paper trading
    _ensure_column(conn, "paper_trades", "bypassed_json", "TEXT")  # variante de estudo: travas ignoradas na entrada
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Valor guardado na tabela `meta` para a chave, ou None."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Grava ou atualiza um valor na tabela `meta`."""
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
