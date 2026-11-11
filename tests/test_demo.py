"""Modo demonstração: roda sem internet e sem chave, só com a amostra de candles do repositório."""

import json
from pathlib import Path

import httpx
import pandas as pd
import pytest

from cryptoagent import config
from cryptoagent.cli import app
from cryptoagent.config import DEMO_RELATIVE, load_config
from cryptoagent.data.models import KLINE_COLUMNS
from cryptoagent.data.validation import clean_klines

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "exemplos" / "historico"


@pytest.fixture
def sem_rede_e_sem_chave(monkeypatch):
    """Qualquer tentativa de usar a rede ou de ler as chaves faz o teste falhar."""

    async def rede_proibida(self, *args, **kwargs):
        raise AssertionError("o modo demonstração tentou usar a rede")

    def chave_proibida(*args, **kwargs):
        raise AssertionError("o modo demonstração tentou ler o .env")

    monkeypatch.setattr(httpx.AsyncClient, "send", rede_proibida)
    monkeypatch.setattr(config, "load_secrets", chave_proibida)
    monkeypatch.setenv("CRYPTOAGENT_HOME", str(ROOT))
    monkeypatch.setenv("COLUMNS", "250")  # terminal largo: as tabelas não cortam os nomes
    for name in ("BINANCE_API_KEY", "BINANCE_API_SECRET"):
        monkeypatch.delenv(name, raising=False)


def test_amostra_so_tem_candles_publicos_e_passa_na_validacao():
    cfg = load_config(ROOT / "config" / "config.yaml", ROOT / DEMO_RELATIVE)
    assert cfg.historico.intervalos == {"ETHBRL": ["1m", "5m", "15m", "1h", "4h"]}
    arquivos = sorted(p.relative_to(SAMPLE).as_posix() for p in SAMPLE.rglob("*") if p.is_file())
    assert arquivos == [f"ETHBRL/{tf}.parquet" for tf in ("15m", "1h", "1m", "4h", "5m")]
    fins = set()
    for tf in cfg.historico.intervalos["ETHBRL"]:
        df = pd.read_parquet(SAMPLE / "ETHBRL" / f"{tf}.parquet")
        assert list(df.columns) == KLINE_COLUMNS  # só preço e volume de mercado: nada de conta, saldo ou ordem
        clean, quality = clean_klines(df, tf)
        assert quality.ok and not quality.gaps and not quality.duplicates and len(clean) == len(df)
        fins.add(int(df["close_time"].iloc[-1]))
    assert len(fins) == 1  # todas as séries terminam no mesmo instante
    assert sum(p.stat().st_size for p in SAMPLE.rglob("*.parquet")) < 10_000_000


def test_demo_verifica_o_historico_sem_rede_e_sem_chave(sem_rede_e_sem_chave, capsys):
    assert app.main(["--demo", "historico", "verificar"]) == 0
    saida = capsys.readouterr().out
    assert "MODO DEMONSTRAÇÃO" in saida and "ETHBRL" in saida and "ETHUSDT" not in saida


def test_demo_roda_o_backtest_sem_rede_e_sem_chave(sem_rede_e_sem_chave, capsys):
    assert app.main(["--demo", "backtest", "--rapido"]) == 0
    saida = capsys.readouterr().out
    assert "MODO DEMONSTRAÇÃO" in saida and "Comprar e segurar" in saida
    relatorio = json.loads((ROOT / "data" / "demo" / "reports" / "backtest.json").read_text(encoding="utf-8"))
    texto = json.dumps(relatorio)
    assert "ETHBRL" in texto and "lida da sua conta" not in texto  # custos do config, nunca os da conta


def test_demo_recusa_comandos_que_precisam_de_dados_ao_vivo(sem_rede_e_sem_chave, capsys):
    for comando in (["analisar"], ["painel"], ["conta", "status"], ["paper", "rodar"], ["historico", "baixar"]):
        assert app.main(["--demo", *comando]) == 2
    assert "não roda no modo demonstração" in capsys.readouterr().out


def test_demo_nao_aceita_sobreposicao_junto(sem_rede_e_sem_chave, tmp_path):
    extra = tmp_path / "x.yaml"
    extra.write_text("{}", encoding="utf-8")
    assert app.main(["--demo", "--sobrepor", str(extra), "historico", "verificar"]) == 1


def test_intervalos_vazios_desligam_o_par():
    cfg = load_config(ROOT / "config" / "config.yaml")
    assert "ETHUSDT" in cfg.historico.intervalos  # no config normal o par continua lá
