"""Histórico: leitura de CSV (ms e µs), checksum, planejamento de períodos, download e buracos."""

import asyncio
import hashlib
import io
import re
import zipfile
from datetime import date, datetime

import httpx
import pandas as pd

from cryptoagent.data.history import (
    HistoryStore,
    Period,
    download_history,
    parse_kline_csv,
    plan_periods,
    read_zip_csv,
    update_from_rest,
    verify_checksum,
)
from cryptoagent.data.models import find_gaps, klines_to_frame
from cryptoagent.timeutils import UTC, datetime_to_ms

BASE = "https://data.binance.vision"


def csv_rows(start_ms, n, step=60_000, micro=False, header=False):
    mult = 1000 if micro else 1
    lines = (
        [
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"
        ]
        if header
        else []
    )
    for i in range(n):
        t = start_ms + i * step
        lines.append(f"{t * mult},100,101,99,100.5,10,{(t + step - 1) * mult},1000,5,6,600,0")
    return ("\n".join(lines) + "\n").encode()


def make_zip(name, content):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, content)
    return buf.getvalue()


T0 = datetime_to_ms(datetime(2026, 9, 1, tzinfo=UTC))


def test_csv_em_ms_e_em_microssegundos_dao_o_mesmo_resultado():
    a = parse_kline_csv(csv_rows(T0, 5))
    b = parse_kline_csv(csv_rows(T0, 5, micro=True))
    pd.testing.assert_frame_equal(a, b)
    assert int(a["open_time"].iloc[0]) == T0
    assert int(a["close_time"].iloc[0]) == T0 + 59_999


def test_csv_com_cabecalho():
    df = parse_kline_csv(csv_rows(T0, 3, header=True))
    assert len(df) == 3 and df["trades"].dtype == "int64"


def test_zip_em_memoria():
    df = read_zip_csv(make_zip("ETHUSDT-1m-2026-09.csv", csv_rows(T0, 4)))
    assert len(df) == 4


def test_checksum():
    content = b"abc"
    good = hashlib.sha256(content).hexdigest() + "  ETHUSDT-1m-2026-09.zip"
    assert verify_checksum(content, good)
    assert not verify_checksum(b"abd", good)
    assert not verify_checksum(content, "")


def test_plano_de_periodos():
    periods = plan_periods(date(2026, 10, 22), 12)
    monthly = [p.label for p in periods if p.kind == "monthly"]
    daily = [p.label for p in periods if p.kind == "daily"]
    assert monthly[0] == "2025-10" and monthly[-1] == "2026-09" and len(monthly) == 12
    assert daily[0] == "2026-10-01" and daily[-1] == "2026-10-21" and len(daily) == 21


def test_plano_no_dia_1_nao_tem_diarios_e_vira_o_ano():
    periods = plan_periods(date(2026, 1, 1), 2)
    assert [p.label for p in periods] == ["2025-11", "2025-12"]


def test_url():
    assert (
        Period("monthly", 2026, 9).url(BASE, "ETHBRL", "5m")
        == f"{BASE}/data/spot/monthly/klines/ETHBRL/5m/ETHBRL-5m-2026-09.zip"
    )
    assert (
        Period("daily", 2026, 10, 3)
        .url(BASE, "ETHUSDT", "1m")
        .endswith("/daily/klines/ETHUSDT/1m/ETHUSDT-1m-2026-10-03.zip")
    )


def test_buracos():
    df = parse_kline_csv(csv_rows(T0, 10))
    df = df.drop(index=[3, 4, 7]).reset_index(drop=True)
    gaps = find_gaps(df, "1m")
    assert [(g.start_ms, g.missing) for g in gaps] == [(T0 + 3 * 60_000, 2), (T0 + 7 * 60_000, 1)]


class FakeVision:
    """Servidor falso do data.binance.vision: gera ZIP + CHECKSUM para cada URL pedida."""

    def __init__(self, missing_monthly=(), bad_checksum=()):
        self.requests = []
        self.missing_monthly = set(missing_monthly)
        self.bad_checksum = set(bad_checksum)

    def handler(self, request):
        url = str(request.url)
        self.requests.append(url)
        m = re.search(
            r"/(monthly|daily)/klines/(\w+)/(\w+)/\w+-\w+-(\d{4})-(\d{2})(?:-(\d{2}))?\.zip(\.CHECKSUM)?$", url
        )
        kind, _, _, y, mo, d, is_checksum = m.groups()
        label = f"{y}-{mo}" + (f"-{d}" if d else "")
        if kind == "monthly" and label in self.missing_monthly:
            return httpx.Response(404)
        start = datetime_to_ms(datetime(int(y), int(mo), int(d or 1), tzinfo=UTC))
        content = make_zip("x.csv", csv_rows(start, 3, micro=True))
        if is_checksum:
            digest = "0" * 64 if label in self.bad_checksum else hashlib.sha256(content).hexdigest()
            return httpx.Response(200, text=f"{digest}  x.zip")
        return httpx.Response(200, content=content)


def run_download(store, server, months=2, today=date(2026, 10, 3)):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as http:
            return await download_history(store, http, BASE, "ETHUSDT", "1m", months, today_utc=today)

    return asyncio.run(go())


def test_download_completo_com_fallback_para_diarios_e_checksum_ruim(tmp_path):
    store = HistoryStore(tmp_path)
    # setembro (último mês) ainda sem arquivo mensal -> usa os 30 diários; um diário com checksum ruim
    server = FakeVision(missing_monthly={"2026-09"}, bad_checksum={"2026-09-15"})
    stats = run_download(store, server)
    assert stats.checksum_failures == ["2026-09-15"]
    # agosto (mensal) + 29 diários de setembro + 2 diários de outubro
    assert stats.files_ok == 1 + 29 + 2
    df = store.load("ETHUSDT", "1m")
    assert len(df) == 3 * stats.files_ok
    assert df["open_time"].is_monotonic_increasing and df["open_time"].is_unique
    # segunda execução: só tenta de novo o que falhou
    server.requests.clear()
    stats2 = run_download(store, server)
    asked = {u.split("/")[-1] for u in server.requests if not u.endswith(".CHECKSUM")}
    assert asked == {"ETHUSDT-1m-2026-09.zip", "ETHUSDT-1m-2026-09-15.zip"}
    assert stats2.files_skipped > 0


class FakeKlines:
    def __init__(self, start, n_available, step=60_000):
        self.start, self.n, self.step = start, n_available, step
        self.calls = 0

    async def klines(self, symbol, interval, *, start_ms=None, end_ms=None, limit=500):
        self.calls += 1
        first = max(0, (start_ms - self.start) // self.step)
        rows = []
        for i in range(first, min(self.n, first + limit)):
            t = self.start + i * self.step
            rows.append([t, "1", "1", "1", "1", "1", t + self.step - 1, "1", 1, "1", "1", "0"])
        return klines_to_frame(rows), None


def test_update_from_rest_so_grava_candles_fechados(tmp_path):
    store = HistoryStore(tmp_path)
    store.save("ETHUSDT", "1m", parse_kline_csv(csv_rows(T0, 10)))
    fake = FakeKlines(T0, 2500)
    now = T0 + 2400 * 60_000 + 30_000  # no meio do candle 2400 (ainda aberto)
    added = asyncio.run(update_from_rest(store, fake, "ETHUSDT", "1m", months=1, now=now))
    df = store.load("ETHUSDT", "1m")
    assert added == 2390
    assert int(df["open_time"].iloc[-1]) == T0 + 2399 * 60_000
    assert (df["close_time"] < now).all()
    assert find_gaps(df, "1m") == []


# ------------------------------------------------- falhas de rede simuladas (Etapa 4.1)

from cryptoagent.data.history import HistoryDownloadError, _fetch  # noqa: E402
from cryptoagent.data.retry import RetryPolicy  # noqa: E402

RETRY_TESTE = RetryPolicy(attempts=4, base_s=1.0, max_s=30.0, jitter=False)


def fetch_with(handler, retry=RETRY_TESTE):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await _fetch(http, BASE + "/x.zip", asyncio.Semaphore(1), retry, fake_sleep)

    return go, sleeps


def test_politica_de_espera_dobra_ate_o_teto():
    p = RetryPolicy(attempts=10, base_s=0.5, max_s=30.0, jitter=False)
    assert [p.delay(n) for n in range(8)] == [0.5, 1, 2, 4, 8, 16, 30, 30]
    com_sorteio = RetryPolicy(attempts=3, base_s=2.0)
    assert com_sorteio.delay(1, rnd=lambda: 0.0) == 2.0 and com_sorteio.delay(1, rnd=lambda: 1.0) == 6.0


def test_retry_after_usa_o_cabecalho_o_padrao_e_o_teto():
    p = RetryPolicy(attempts=3, retry_after_default_s=60, retry_after_max_s=300)
    assert p.retry_after({"retry-after": "7"}) == 7.0
    assert p.retry_after({}) == 60.0
    assert p.retry_after({"retry-after": "amanhã"}) == 60.0
    assert p.retry_after({"retry-after": "99999"}) == 300.0


def test_download_repete_em_falha_de_rede_timeout_e_erro_do_servidor_com_espera_crescente():
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("sem internet")
        if state["n"] == 2:
            raise httpx.ReadTimeout("demorou demais")
        if state["n"] == 3:
            return httpx.Response(503, text="indisponível")
        return httpx.Response(200, content=b"conteudo")

    go, sleeps = fetch_with(handler)
    assert asyncio.run(go()) == b"conteudo"
    assert state["n"] == 4
    assert sleeps == [1.0, 2.0, 4.0]  # 1 s, depois dobra


def test_download_desiste_depois_do_maximo_de_tentativas():
    calls = []

    def handler(req):
        calls.append(1)
        raise httpx.ConnectError("sem internet")

    go, sleeps = fetch_with(handler, RetryPolicy(attempts=3, base_s=1.0, jitter=False))
    try:
        asyncio.run(go())
        raise AssertionError("deveria ter falhado")
    except HistoryDownloadError as exc:
        assert "falha de rede (ConnectError)" in str(exc)
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]  # não espera depois da última tentativa


def test_download_429_espera_o_retry_after():
    responses = iter([httpx.Response(429, headers={"Retry-After": "12"}), httpx.Response(200, content=b"ok")])
    go, sleeps = fetch_with(lambda req: next(responses))
    assert asyncio.run(go()) == b"ok"
    assert sleeps == [12.0]


def test_download_418_e_erro_do_cliente_param_na_hora():
    for status in (418, 400):
        calls = []

        def handler(req, status=status, calls=calls):
            calls.append(1)
            return httpx.Response(status)

        go, sleeps = fetch_with(handler)
        try:
            asyncio.run(go())
            raise AssertionError("deveria ter falhado")
        except HistoryDownloadError:
            pass
        assert len(calls) == 1 and sleeps == []


def test_download_404_significa_arquivo_inexistente_sem_repetir():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(404)

    go, sleeps = fetch_with(handler)
    assert asyncio.run(go()) is None and len(calls) == 1 and sleeps == []


class KlinesQueCai(FakeKlines):
    """Entrega `ok_calls` páginas e depois simula queda de rede."""

    def __init__(self, start, n_available, ok_calls):
        super().__init__(start, n_available)
        self.ok_calls = ok_calls
        self.starts = []

    async def klines(self, symbol, interval, *, start_ms=None, end_ms=None, limit=500):
        self.starts.append(start_ms)
        if self.calls >= self.ok_calls:
            raise ConnectionError("rede caiu no meio da coleta")
        return await super().klines(symbol, interval, start_ms=start_ms, end_ms=end_ms, limit=limit)


def test_coleta_incremental_guarda_o_que_chegou_e_continua_de_onde_parou(tmp_path):
    store = HistoryStore(tmp_path)
    store.save("ETHUSDT", "1m", parse_kline_csv(csv_rows(T0, 10)))
    now = T0 + 3500 * 60_000 + 30_000
    caiu = KlinesQueCai(T0, 5000, ok_calls=2)
    try:
        asyncio.run(update_from_rest(store, caiu, "ETHUSDT", "1m", months=1, now=now))
        raise AssertionError("deveria ter propagado a falha de rede")
    except ConnectionError:
        pass
    parcial = store.load("ETHUSDT", "1m")
    assert len(parcial) == 10 + 2000  # as duas páginas que chegaram foram gravadas
    # segunda execução: começa no candle seguinte ao último gravado, sem pedir nada de novo
    ok = FakeKlines(T0, 5000)
    pedidos = []
    original = ok.klines

    async def espiao(symbol, interval, *, start_ms=None, end_ms=None, limit=500):
        pedidos.append(start_ms)
        return await original(symbol, interval, start_ms=start_ms, end_ms=end_ms, limit=limit)

    ok.klines = espiao
    added = asyncio.run(update_from_rest(store, ok, "ETHUSDT", "1m", months=1, now=now))
    final = store.load("ETHUSDT", "1m")
    assert pedidos[0] == T0 + 2010 * 60_000
    assert added == 3500 - 2010 and len(final) == 3500
    assert find_gaps(final, "1m") == [] and final["open_time"].is_unique
    # terceira execução, sem candle novo: nenhuma linha entra
    assert asyncio.run(update_from_rest(store, FakeKlines(T0, 5000), "ETHUSDT", "1m", months=1, now=now)) == 0
