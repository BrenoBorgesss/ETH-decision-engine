from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from cryptoagent.config import Config, Mercado, Secrets, load_config
from cryptoagent.costs.recorder import BookState, build_sample, quote_sizes
from cryptoagent.data.market_store import CandleBuffer

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000_000


def test_config_do_projeto_carrega():
    cfg = load_config(ROOT / "config" / "config.yaml")
    assert cfg.mercado.simbolo_analise == "ETHUSDT"
    assert cfg.mercado.simbolos_monitorados == ["ETHUSDT", "ETHBRL", "USDTBRL"]
    assert cfg.root == ROOT
    assert cfg.fuso.local == "America/Sao_Paulo"


def test_sobreposicao_da_hipotese_muda_so_o_declarado():
    base = load_config(ROOT / "config" / "config.yaml")
    hyp = load_config(ROOT / "config" / "config.yaml", ROOT / "config" / "hipotese_setup_1h.yaml")
    assert (hyp.estrategia.tf_setup, hyp.estrategia.tf_gatilho, hyp.estrategia.tempo_max_min) == ("1h", "15m", 720)
    changed = {k for k, v in hyp.estrategia.model_dump().items() if base.estrategia.model_dump()[k] != v}
    assert changed == {"tf_setup", "tf_gatilho", "tempo_max_min"}
    assert hyp.custos == base.custos and hyp.indicadores == base.indicadores


def test_campo_desconhecido_e_erro():
    with pytest.raises(ValidationError):
        Config.model_validate({"mercado": {"simbolo_analize": "ETHUSDT"}})


def test_par_operacao_precisa_ser_candidato():
    with pytest.raises(ValidationError):
        Mercado(par_operacao="BTCUSDT")
    assert Mercado(par_operacao="ethbrl").par_operacao == "ETHBRL"


def test_segredos_nao_aparecem_em_repr():
    s = Secrets(binance_api_key=SecretStr("chave-super-secreta"), binance_api_secret=SecretStr("segredo-super-secreto"))
    assert "chave-super-secreta" not in repr(s) and "segredo-super-secreto" not in str(s)
    assert s.has_binance


def kline(t, close, closed):
    return {"t": t, "T": t + 59_999, "o": "1", "h": "2", "l": "0.5", "c": str(close), "v": "10", "q": "15", "n": 3, "V": "4", "Q": "6", "x": closed}


def test_buffer_de_candles():
    buf = CandleBuffer("ETHUSDT", "1m", maxlen=3)
    buf.apply_ws_kline(kline(NOW, 1.0, True), NOW + 60_000)
    buf.apply_ws_kline(kline(NOW + 60_000, 1.5, False), NOW + 61_000)
    assert buf.latest_closed()["close"] == 1.0
    assert buf.latest()["close"] == 1.5
    buf.apply_ws_kline(kline(NOW + 60_000, 1.7, True), NOW + 120_000)
    assert buf.latest_closed()["close"] == 1.7
    for i in range(2, 6):
        buf.apply_ws_kline(kline(NOW + i * 60_000, 2.0, True), NOW)
    assert len(buf) == 3 and buf.gaps() == []


def test_tamanhos_de_ordem_convertidos_pelo_cambio():
    assert quote_sizes("ETHBRL", [500, 1000], 5.5) == {"500": 500, "1000": 1000}
    assert quote_sizes("ETHUSDT", [550], 5.5) == {"550": pytest.approx(100)}
    assert quote_sizes("ETHUSDT", [550], None) == {"550": None}


def test_amostra_de_custos():
    book = BookState("ETHBRL", bids=[(99.0, 10.0), (98.0, 10.0)], asks=[(101.0, 10.0), (102.0, 10.0)], depth_ms=NOW - 500)
    row = build_sample(book, NOW, {"500": 500.0, "5000": 5000.0}, 5.5, 5000)
    assert row["source"] == "ws"  # sem bookTicker fresco, usa o topo do depth
    assert row["spread_pct"] == pytest.approx(2.0)
    assert row["slippage_json"]["500"] == {"compra_pct": 0.0, "venda_pct": 0.0, "completo": True}
    assert row["slippage_json"]["5000"]["completo"] is False
    assert row["book_age_ms"] == 500

    book.bid, book.ask, book.bid_qty, book.ask_qty, book.ticker_ms = 99.5, 100.5, 1.0, 1.0, NOW - 100
    row = build_sample(book, NOW, {}, None, 5000)
    assert row["source"] == "ws+bookTicker" and row["bid"] == 99.5
