"""Gera a amostra de candles do modo demonstração (pasta `exemplos/historico`).

A amostra é um recorte do histórico PÚBLICO de candles da Binance (data.binance.vision), já
baixado em `data/history` pelo comando `cryptoagent historico baixar`. Não contém nenhum dado
de conta: só preço e volume de mercado, iguais para qualquer pessoa.

- Par: ETHBRL. Intervalos 1m, 5m, 15m e 1h: copiados do histórico, sem alteração.
- Intervalo 4h: a Binance não publica o arquivo de 4h deste par na configuração padrão do
  projeto, então ele é montado somando os candles de 1h (4 por vez, alinhados em UTC), que é
  exatamente como a corretora forma o candle de 4h.
- Cada arquivo passa pela mesma validação usada na coleta (`data/validation.py`).

Uso (na raiz do projeto, com o histórico já baixado):
    python scripts/gerar_amostra_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from cryptoagent.data.models import KLINE_COLUMNS, normalize_klines
from cryptoagent.data.validation import clean_klines
from cryptoagent.timeutils import interval_ms

SYMBOL = "ETHBRL"
START_UTC = "2026-06-25"  # ~5 semanas de aquecimento dos indicadores (EMA 200 no 4h) antes de agosto
END_UTC = "2026-10-02"  # exclusivo: agosto (treino) e setembro (teste) completos, mais 1 dia de outubro
# (o walk-forward só aceita um mês de teste se existir candle do mês seguinte, prova de que o mês fechou)
COPIED = ["1m", "5m", "15m", "1h"]
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "history"
TARGET = ROOT / "exemplos" / "historico"


def to_ms(day: str) -> int:
    """Meia-noite UTC do dia, em ms."""
    return int(pd.Timestamp(day, tz="UTC").value // 1_000_000)


def aggregate(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Junta candles menores em candles de `interval` (alinhados em UTC). Só devolve candles completos."""
    step = interval_ms(interval)
    src_step = int(df["open_time"].diff().dropna().mode().iloc[0])
    bucket = df["open_time"] - df["open_time"] % step
    g = df.groupby(bucket, sort=True)
    out = pd.DataFrame({
        "open_time": g["open_time"].first().index,
        "open": g["open"].first().to_numpy(),
        "high": g["high"].max().to_numpy(),
        "low": g["low"].min().to_numpy(),
        "close": g["close"].last().to_numpy(),
        "volume": g["volume"].sum().to_numpy(),
        "close_time": g["open_time"].first().index + step - 1,
        "quote_volume": g["quote_volume"].sum().to_numpy(),
        "trades": g["trades"].sum().to_numpy(),
        "taker_buy_base": g["taker_buy_base"].sum().to_numpy(),
        "taker_buy_quote": g["taker_buy_quote"].sum().to_numpy(),
    })
    complete = (g.size() == step // src_step).to_numpy()
    return normalize_klines(out.loc[complete, KLINE_COLUMNS])


def main() -> int:
    """Gera os arquivos da amostra e mostra um resumo. Devolve o código de saída."""
    a, b = to_ms(START_UTC), to_ms(END_UTC)
    frames: dict[str, pd.DataFrame] = {}
    for tf in COPIED:
        path = SOURCE / SYMBOL / f"{tf}.parquet"
        if not path.is_file():
            print(f"Falta {path}: rode antes  cryptoagent historico baixar")
            return 1
        df = pd.read_parquet(path)
        frames[tf] = df[(df["open_time"] >= a) & (df["open_time"] < b)].reset_index(drop=True)
    frames["4h"] = aggregate(frames["1h"], "4h")

    total = 0
    for tf, df in frames.items():
        clean, quality = clean_klines(df, tf)
        if not quality.ok or quality.gaps or quality.duplicates or quality.out_of_order or list(df.columns) != KLINE_COLUMNS:
            print(f"{SYMBOL} {tf}: amostra reprovada na validação ({quality.summary()})")
            return 1
        out = TARGET / SYMBOL / f"{tf}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        clean.to_parquet(out, index=False, compression="zstd", compression_level=19)
        total += out.stat().st_size
        first = pd.to_datetime(clean["open_time"].iloc[0], unit="ms", utc=True)
        last = pd.to_datetime(clean["open_time"].iloc[-1], unit="ms", utc=True)
        print(f"{SYMBOL} {tf}: {len(clean)} candles, {first:%Y-%m-%d %H:%M} a {last:%Y-%m-%d %H:%M} UTC, {out.stat().st_size / 1e6:.2f} MB")
    print(f"Total: {total / 1e6:.2f} MB em {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
