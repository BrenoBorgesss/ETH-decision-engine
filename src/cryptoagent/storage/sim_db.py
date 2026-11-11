"""Banco da SIMULAÇÃO (carteira virtual), separado do banco da conta real.

Fica em `paper.banco` (padrão `data/simulacao.sqlite`). Não tem nenhuma tabela da conta real
(saldos, operações da Binance, diário manual, sessões): a simulação não lê nem grava nada disso.
O replay usa um arquivo próprio por execução, com este mesmo formato.

Cada operação virtual guarda: horário da decisão e da execução, preço da decisão e preço executado,
quantidade, valor, taxa, stop e alvos, motivo da entrada, as variáveis do momento (features), a
confiança, o cenário de mercado, o resultado em R$ e o motivo da saída.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cryptoagent.storage.agent_db import SIGNALS_TABLE

SIM_SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
    + SIGNALS_TABLE
    + """
CREATE TABLE IF NOT EXISTS paper_state (
    variant TEXT PRIMARY KEY,
    equity REAL NOT NULL,                -- patrimônio virtual atual (R$)
    initial_capital REAL NOT NULL,       -- capital virtual inicial (R$)
    max_loss_day REAL NOT NULL,          -- perda máxima virtual por dia (R$)
    day TEXT,
    day_start_equity REAL,
    last_decision_close_ms INTEGER,
    started_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variant TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_id INTEGER,                   -- sinal (tabela signals) que gerou a compra
    decision_ms INTEGER NOT NULL,        -- quando o motor decidiu comprar
    decision_price REAL NOT NULL,        -- preço médio (bid/ask) no momento da decisão
    entry_ms INTEGER NOT NULL,           -- quando a compra virtual foi executada (depois do atraso de execução)
    entry_price REAL NOT NULL,           -- preço pago: meio spread + slippage sobre o preço do momento
    qty REAL NOT NULL,                   -- quantidade já ajustada às regras de lote da corretora
    size REAL NOT NULL,                  -- valor aplicado (R$)
    fee_in REAL NOT NULL,                -- taxa da compra (R$)
    stop REAL, target1 REAL, target2 REAL,
    confidence REAL, regime TEXT,
    entry_reason TEXT,                   -- motivos da compra, em texto
    features_json TEXT,                  -- variáveis do momento (as mesmas do modelo desafiador)
    scenario_json TEXT,                  -- cenário de mercado: regime, hora, dia da semana, volatilidade, spread...
    equity_at_entry REAL NOT NULL,
    checked_until_ms INTEGER NOT NULL,   -- até onde a OCO virtual já foi conferida
    exit_decision_ms INTEGER,
    exit_ms INTEGER, exit_price REAL, exit_reason TEXT,
    pnl REAL,                            -- resultado líquido em R$ (depois de taxas, spread e slippage)
    pnl_pct REAL, fees REAL, ret_equity REAL,
    bypassed_json TEXT                   -- variante de estudo: travas ignoradas na entrada
);
CREATE INDEX IF NOT EXISTS ix_paper_trades_variant ON paper_trades(variant, entry_ms);

-- Fatos que não viram operação, mas explicam o que aconteceu (ex.: ordem recusada por ser pequena demais).
CREATE TABLE IF NOT EXISTS paper_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time_ms INTEGER NOT NULL,
    variant TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    signal_id INTEGER
);
"""
)


def connect(path: Path) -> sqlite3.Connection:
    """Abre (criando se preciso) o banco da simulação."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SIM_SCHEMA)
    return conn
