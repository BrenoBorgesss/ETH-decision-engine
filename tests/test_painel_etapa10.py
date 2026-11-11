"""Painel (Etapa 10): aviso exato, biblioteca local conferida, rotas novas, erros amigáveis, abas e demonstração."""

import asyncio
import hashlib
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from rich.console import Console

from cryptoagent.account import journal
from cryptoagent.dashboard.server import STATIC, create_app
from cryptoagent.dashboard.service import DISCLAIMER_PAINEL
from cryptoagent.dashboard.views import journal_payload, simulation_payload
from cryptoagent.storage import agent_db
from cryptoagent.timeutils import datetime_to_ms

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "token-de-teste-123"
LOCAL = "http://127.0.0.1:8765"
LIB_SHA256 = "c7dda807d662a95b3d257119ed315cec669e3bdf5aaece75c480a39307f23540"
EXACT = "Ferramenta educacional, não é recomendação financeira. Resultados simulados não garantem resultados reais."
SP = ZoneInfo("America/Sao_Paulo")


def test_aviso_exato_do_roteiro_em_destaque_na_pagina_e_no_estado():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert DISCLAIMER_PAINEL == EXACT
    assert page.count(EXACT) == 2  # faixa fixa no topo e rodapé
    assert 'class="disclaimer"' in page


def test_biblioteca_do_grafico_e_a_conferida_e_nada_vem_de_site_externo():
    lib = STATIC / "vendor" / "lightweight-charts.standalone.production.js"
    assert hashlib.sha256(lib.read_bytes()).hexdigest() == LIB_SHA256
    assert (STATIC / "vendor" / "LICENSE-lightweight-charts.txt").is_file()
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r'src=["\']https?://', page) and not re.search(r'href=["\']https?://', page)


def test_rotulos_real_e_simulado_sempre_com_texto():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert page.count('class="badge sim">SIMULADO') >= 1 and page.count('class="badge real">REAL') >= 3
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert '"DEMONSTRAÇÃO · SIMULADO"' in js and '"REAL · somente leitura"' in js


class Svc:
    def __init__(self):
        self.added, self.removed = [], []

    def snapshot(self):
        return {"modo": "real"}

    def candles_payload(self, tf):
        return {"tf": tf, "candles": []}

    def season_payload(self):
        raise RuntimeError("falha interna com caminho C:/segredo/arquivo.py linha 42")

    def save_session(self, data):
        return data

    def associate(self, data):
        pass

    def simulation_payload(self):
        return {"disponivel": False, "carteiras": []}

    def journal_payload(self):
        return {"operacoes": [], "dias": []}

    def add_journal(self, data):
        if float(data["qtd"]) <= 0:
            raise ValueError("operação inválida")
        self.added.append(data)
        return {"id": 1}

    def remove_journal(self, data):
        self.removed.append(data)


@pytest.fixture
def client():
    svc = Svc()
    return TestClient(create_app(svc, token=TOKEN), base_url=LOCAL, raise_server_exceptions=False), svc


def test_rotas_novas_e_registro_manual_exige_token(client):
    c, svc = client
    assert c.get("/api/simulacao").json()["disponivel"] is False
    assert c.get("/api/diario").json() == {"operacoes": [], "dias": []}
    body = {"lado": "compra", "qtd": 0.01, "preco": 12000}
    assert c.post("/api/diario", json=body).status_code == 403
    assert c.post("/api/diario", json=body, headers={"X-Painel-Token": TOKEN}).json() == {"ok": True, "id": 1}
    bad = c.post("/api/diario", json={**body, "qtd": 0}, headers={"X-Painel-Token": TOKEN})
    assert bad.status_code == 400 and "registro não salvo" in bad.json()["detail"]
    assert c.post("/api/diario/remover", json={"id": 1}).status_code == 403
    assert c.post("/api/diario/remover", json={"id": 1}, headers={"X-Painel-Token": TOKEN}).json() == {"ok": True}
    assert svc.added and svc.removed == [{"id": 1}]


def test_erro_inesperado_vira_mensagem_amigavel_sem_rastreamento(client):
    c, _ = client
    r = c.get("/api/sazonalidade")
    assert r.status_code == 500
    assert r.json() == {"detail": "Erro inesperado no painel. Os detalhes técnicos estão no arquivo de log."}
    assert "Traceback" not in r.text and "segredo" not in r.text and "linha 42" not in r.text


def test_resumo_diario_das_operacoes_reais_registradas_a_mao(tmp_path):
    conn = agent_db.connect(tmp_path / "agent.sqlite")
    t = lambda d, h: datetime_to_ms(datetime(2026, 11, d, h, 0, tzinfo=SP))  # noqa: E731
    journal.add_manual_trade(conn, symbol="ETHBRL", side="buy", qty=0.1, price=12000, fee_quote=1.2, time_ms=t(10, 10))
    journal.add_manual_trade(conn, symbol="ETHBRL", side="sell", qty=0.05, price=12400, fee_quote=0.62, time_ms=t(10, 15))
    journal.add_manual_trade(conn, symbol="ETHBRL", side="sell", qty=0.05, price=11800, fee_quote=0.59, time_ms=t(11, 9))
    j = journal_payload(conn, "America/Sao_Paulo", "00:00")
    conn.close()
    cost = (12000 * 0.1 + 1.2) / 0.1
    dias = {d["dia"]: d for d in j["dias"]}
    assert dias["2026-11-10"]["realizado"] == pytest.approx(0.05 * ((12400 * 0.05 - 0.62) / 0.05 - cost))
    assert dias["2026-11-11"]["realizado"] == pytest.approx(0.05 * ((11800 * 0.05 - 0.59) / 0.05 - cost))
    assert dias["2026-11-10"]["operacoes"] == 2 and j["moeda"]["ETHBRL"] == "R$"
    assert len(j["operacoes"]) == 3 and j["posicoes"] == []


def test_aba_simulacao_le_o_banco_da_simulacao_sem_gravar(tmp_path):
    from test_replay import demo_cfg, ms, run_replay

    cfg = demo_cfg(tmp_path)
    db = tmp_path / "sim.sqlite"
    run_replay(cfg, db, ms(2026, 9, 8, 9), ms(2026, 9, 8, 13))
    before = db.stat().st_mtime_ns
    s = simulation_payload(db, tmp_path / "sem_aprendizado", "America/Sao_Paulo", end_ms=ms(2026, 9, 8, 13))
    w = s["carteiras"][0]
    assert s["disponivel"] and w["variante"] == "padrao" and w["metricas"]["operacoes"] == 2
    assert [t["motivo_saida"] for t in w["operacoes"]] == ["alvo1", "stop"]  # mais recente primeiro
    assert w["curva"][0][1] == 1000 and w["curva"][-1][1] == pytest.approx(w["patrimonio"])
    assert w["resultado_brl"] == pytest.approx(sum(t["resultado_brl"] for t in w["operacoes"]))
    assert db.stat().st_mtime_ns == before
    vazio = simulation_payload(tmp_path / "nao_existe.sqlite", tmp_path, "America/Sao_Paulo")
    assert vazio["disponivel"] is False and vazio["carteiras"] == []


def test_painel_de_demonstracao_roda_sem_rede_e_mostra_a_carteira_virtual(tmp_path):
    from cryptoagent.backtest.costs import CostSchedule
    from cryptoagent.dashboard.demo import DemoService
    from test_replay import demo_cfg, ms

    cfg = demo_cfg(tmp_path)
    cfg.paper.replay_pasta = str(tmp_path / "replay")
    cfg.conta.banco = str(tmp_path / "demo_agent.sqlite")
    costs = CostSchedule("ETHBRL", 0.001, {h: 0.04 for h in range(24)}, [], 0.01, 0.05, "teste")
    start = ms(2026, 9, 8, 10) + 5000
    svc = DemoService(cfg, Console(quiet=True), start_ms=start, end_ms=start + 50 * 60_000, costs=costs, minutes_per_second=10_000)

    async def go():
        task = asyncio.create_task(svc.run())
        for _ in range(400):
            await asyncio.sleep(0.01)
            if svc.now() >= start + 50 * 60_000:
                break
        snap = svc.snapshot()
        candles = svc.candles_payload("5m")
        svc.stop.set()
        await task
        return snap, candles

    snap, candles = asyncio.run(go())
    assert snap["modo"] == "demo" and snap["decisao"]["acao"] in ("COMPRAR", "VENDER", "ESPERAR")
    assert snap["carteira_virtual"]["capital_inicial"] == 1000
    assert snap["aviso_legal"] == EXACT
    assert any(m["acao"] == "COMPRAR" for m in candles["marcadores"])  # a compra das 10:15 aparece no gráfico
    rows = sqlite3.connect(tmp_path / "replay" / "painel-demo.sqlite").execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert rows == 10  # um sinal por candle de 5m fechado em 50 minutos
