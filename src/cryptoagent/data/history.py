"""Histórico de candles: download do data.binance.vision + complemento pela API REST.

- Arquivos mensais para os meses completos, diários para o mês atual até ontem, e a
  API REST para as horas de hoje.
- Cada arquivo baixado é conferido com o SHA-256 publicado pela Binance (.CHECKSUM);
  arquivo que não confere é descartado e registrado.
- O ZIP é lido em memória (nada é extraído para o disco nem executado).
- Salvo em Parquet: data/history/<PAR>/<intervalo>.parquet, horários em ms UTC.
- Um "manifest" registra o que já foi baixado, para não baixar de novo.
"""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import io
import json
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import httpx
import pandas as pd

from cryptoagent.data.models import KLINE_COLUMNS, Gap, empty_klines, find_gaps, normalize_klines
from cryptoagent.timeutils import UTC, interval_ms, now_ms

log = logging.getLogger(__name__)


class HistoryDownloadError(Exception):
    """Falha ao baixar ou ler um arquivo de histórico."""
    pass


@dataclass(frozen=True)
class Period:
    """Um arquivo de histórico da Binance: mensal (ano e mês) ou diário (ano, mês e dia)."""
    kind: str  # "monthly" | "daily"
    year: int
    month: int
    day: int | None = None

    @property
    def label(self) -> str:
        """Rótulo do período: AAAA-MM ou AAAA-MM-DD."""
        base = f"{self.year}-{self.month:02d}"
        return base if self.day is None else f"{base}-{self.day:02d}"

    def url(self, base_url: str, symbol: str, interval: str) -> str:
        """Endereço do arquivo ZIP deste período para o par e o intervalo."""
        return f"{base_url}/data/spot/{self.kind}/klines/{symbol}/{interval}/{symbol}-{interval}-{self.label}.zip"

    def days(self) -> list[Period]:
        """Os dias do mês deste período, como períodos diários."""
        n = calendar.monthrange(self.year, self.month)[1]
        return [Period("daily", self.year, self.month, d) for d in range(1, n + 1)]


def plan_periods(today_utc: date, months: int) -> list[Period]:
    """Meses completos anteriores ao mês atual + dias do mês atual até ontem (UTC)."""
    periods: list[Period] = []
    current = today_utc.year * 12 + today_utc.month - 1
    for back in range(months, 0, -1):
        idx = current - back
        periods.append(Period("monthly", idx // 12, idx % 12 + 1))
    periods.extend(Period("daily", today_utc.year, today_utc.month, d) for d in range(1, today_utc.day))
    return periods


def parse_kline_csv(raw: bytes) -> pd.DataFrame:
    """Lê o CSV de klines (com ou sem cabeçalho; horários em ms ou µs)."""
    has_header = raw[:20].lower().startswith(b"open_time")
    df = pd.read_csv(io.BytesIO(raw), header=0 if has_header else None, usecols=range(11))
    df.columns = KLINE_COLUMNS
    return normalize_klines(df)


def read_zip_csv(content: bytes) -> pd.DataFrame:
    """Lê em memória o único CSV de um ZIP de klines. Levanta HistoryDownloadError se o ZIP não tiver exatamente um CSV."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise HistoryDownloadError(f"ZIP inesperado: {len(names)} arquivos CSV")
        return parse_kline_csv(zf.read(names[0]))


def verify_checksum(content: bytes, checksum_text: str) -> bool:
    """True se o SHA-256 do conteúdo confere com o do arquivo .CHECKSUM."""
    parts = checksum_text.split()
    return bool(parts) and hashlib.sha256(content).hexdigest() == parts[0].strip().lower()


class HistoryStore:
    """Histórico de candles em disco: um Parquet e um manifesto por par e intervalo."""
    def __init__(self, folder: Path):
        self.folder = folder

    def path(self, symbol: str, interval: str) -> Path:
        """Caminho do Parquet do par e intervalo."""
        return self.folder / symbol / f"{interval}.parquet"

    def _manifest_path(self, symbol: str, interval: str) -> Path:
        return self.folder / symbol / f"{interval}.manifest.json"

    def load(self, symbol: str, interval: str) -> pd.DataFrame:
        """Candles gravados, ou uma tabela vazia se o arquivo não existe."""
        p = self.path(symbol, interval)
        return pd.read_parquet(p) if p.is_file() else empty_klines()

    def save(self, symbol: str, interval: str, df: pd.DataFrame) -> None:
        """Grava os candles com troca atômica (escreve num temporário e renomeia)."""
        p = self.path(symbol, interval)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(p)  # troca atômica: nunca fica um arquivo pela metade

    def read_manifest(self, symbol: str, interval: str) -> set[str]:
        """Rótulos dos períodos já baixados."""
        p = self._manifest_path(symbol, interval)
        return set(json.loads(p.read_text("utf-8"))) if p.is_file() else set()

    def write_manifest(self, symbol: str, interval: str, labels: set[str]) -> None:
        """Grava os rótulos dos períodos já baixados."""
        p = self._manifest_path(symbol, interval)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(sorted(labels)), encoding="utf-8")


@dataclass
class DownloadStats:
    """Resultado de um download ou verificação: arquivos, linhas, período coberto e buracos."""
    symbol: str
    interval: str
    files_ok: int = 0
    files_skipped: int = 0
    missing: list[str] = field(default_factory=list)
    checksum_failures: list[str] = field(default_factory=list)
    rows_total: int = 0
    rows_from_rest: int = 0
    first_ms: int | None = None
    last_ms: int | None = None
    gaps: list[Gap] = field(default_factory=list)

    @property
    def missing_candles(self) -> int:
        """Total de candles faltando, somando todos os buracos."""
        return sum(g.missing for g in self.gaps)


async def _fetch(http: httpx.AsyncClient, url: str, sem: asyncio.Semaphore, attempts: int = 3, wait_s: float = 1.5) -> bytes | None:
    """Baixa um arquivo. Retorna None se não existir (404/403).

    Repete até `attempts` vezes, esperando `wait_s` × número da tentativa entre uma e outra.
    """
    error: Exception | None = None
    for attempt in range(attempts):
        async with sem:
            try:
                resp = await http.get(url)
            except httpx.TransportError as exc:
                error = exc
            else:
                if resp.status_code in (403, 404):
                    return None
                if resp.status_code == 200:
                    return resp.content
                error = RuntimeError(f"HTTP {resp.status_code}")
        await asyncio.sleep(wait_s * (attempt + 1))
    raise HistoryDownloadError(f"Falha ao baixar {url}: {error}")


async def download_history(
    store: HistoryStore,
    http: httpx.AsyncClient,
    base_url: str,
    symbol: str,
    interval: str,
    months: int,
    *,
    today_utc: date | None = None,
    concurrency: int = 4,
    attempts: int = 3,
    retry_wait_s: float = 1.5,
) -> DownloadStats:
    """Baixa do data.binance.vision os períodos que ainda faltam, confere o checksum de cada um, junta com o que já existe e grava. Devolve as estatísticas."""
    today_utc = today_utc or datetime.now(UTC).date()
    stats = DownloadStats(symbol, interval)
    periods = plan_periods(today_utc, months)
    done = store.read_manifest(symbol, interval)
    todo = [p for p in periods if p.label not in done]
    stats.files_skipped = len(periods) - len(todo)
    latest_monthly = max((p for p in periods if p.kind == "monthly"), key=lambda p: (p.year, p.month), default=None)
    sem = asyncio.Semaphore(concurrency)

    async def get_period(period: Period) -> list[tuple[str, pd.DataFrame]]:
        url = period.url(base_url, symbol, interval)
        content = await _fetch(http, url, sem, attempts, retry_wait_s)
        if content is None:
            if period == latest_monthly:
                # O arquivo do mês anterior às vezes só sai alguns dias depois: usa os diários.
                parts = await asyncio.gather(*(get_period(d) for d in period.days() if d.label not in done))
                return [item for part in parts for item in part]
            stats.missing.append(period.label)
            return []
        checksum = await _fetch(http, url + ".CHECKSUM", sem, attempts, retry_wait_s)
        if checksum is None or not verify_checksum(content, checksum.decode("utf-8", "replace")):
            stats.checksum_failures.append(period.label)
            log.warning("Checksum não confere para %s %s %s; arquivo descartado", symbol, interval, period.label)
            return []
        stats.files_ok += 1
        return [(period.label, read_zip_csv(content))]

    results = await asyncio.gather(*(get_period(p) for p in todo))
    new = [item for part in results for item in part]

    frames = [store.load(symbol, interval), *(df for _, df in new)]
    combined = normalize_klines(pd.concat([f for f in frames if not f.empty], ignore_index=True)) if any(
        not f.empty for f in frames
    ) else empty_klines()
    store.save(symbol, interval, combined)
    store.write_manifest(symbol, interval, done | {label for label, _ in new})
    _fill_stats(stats, combined, interval)
    return stats


class KlinesSource(Protocol):
    """Qualquer objeto com o método `klines` do cliente REST (permite usar um falso nos testes)."""
    async def klines(self, symbol: str, interval: str, *, start_ms: int | None = ..., end_ms: int | None = ..., limit: int = ...) -> tuple[pd.DataFrame, object]: ...


async def update_from_rest(
    store: HistoryStore,
    client: KlinesSource,
    symbol: str,
    interval: str,
    *,
    months: int,
    now: int | None = None,
    max_requests: int = 2000,
) -> int:
    """Completa o histórico até o último candle FECHADO usando a API REST. Retorna quantas linhas entraram.

    `now` deve ser o horário da Binance (client.server_now_ms()), não o do PC.
    """
    df = store.load(symbol, interval)
    step = interval_ms(interval)
    now = now if now is not None else now_ms()
    start = int(df["open_time"].iloc[-1]) + step if not df.empty else now - months * 31 * 86_400_000
    start -= start % step
    new_frames: list[pd.DataFrame] = []
    for _ in range(max_requests):
        if start + step > now:
            break
        frame, _ = await client.klines(symbol, interval, start_ms=start, limit=1000)
        frame = frame[frame["close_time"] < now]  # só candles já fechados
        if frame.empty:
            break
        new_frames.append(frame)
        start = int(frame["open_time"].iloc[-1]) + step
        if len(frame) < 1000:
            break
    if not new_frames:
        return 0
    added = sum(len(f) for f in new_frames)
    store.save(symbol, interval, normalize_klines(pd.concat([df, *new_frames], ignore_index=True)))
    return added


def summarize(store: HistoryStore, symbol: str, interval: str) -> DownloadStats:
    """Estatísticas do histórico já gravado, sem baixar nada."""
    stats = DownloadStats(symbol, interval)
    _fill_stats(stats, store.load(symbol, interval), interval)
    return stats


def _fill_stats(stats: DownloadStats, df: pd.DataFrame, interval: str) -> None:
    stats.rows_total = len(df)
    if not df.empty:
        stats.first_ms = int(df["open_time"].iloc[0])
        stats.last_ms = int(df["open_time"].iloc[-1])
    stats.gaps = find_gaps(df, interval)
