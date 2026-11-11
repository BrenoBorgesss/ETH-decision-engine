"""Painel: API local protegida por token, sem segredos nas respostas; regras de alerta."""

import pytest
from fastapi.testclient import TestClient
from rich.console import Console

from cryptoagent.alerts.notify import Alerter, alert_for_change
from cryptoagent.config import Alertas
from cryptoagent.dashboard.server import create_app
from cryptoagent.dashboard.service import _clean
from cryptoagent.engine.decision import Decision
from cryptoagent.security.redact import register_secrets
from cryptoagent.session.daily import SessionError

TOKEN = "token-de-teste-123"
SECRET = "SEGREDO_DA_CHAVE_0123456789"


class FakeService:
    def __init__(self):
        self.sessions, self.assocs = [], []

    def snapshot(self):
        return _clean({"decisao": {"acao": "ESPERAR", "travas": [f"erro com {SECRET}"]}, "valor": float("nan")})

    def candles_payload(self, tf):
        return {"tf": tf, "candles": []}

    def season_payload(self):
        return {"disponivel": False}

    def save_session(self, data):
        if float(data["perda_max"]) >= float(data["capital"]):
            raise SessionError("a perda máxima precisa ser menor que o capital")
        self.sessions.append(data)
        return data

    def associate(self, data):
        self.assocs.append(data)


@pytest.fixture
def client():
    register_secrets([SECRET])
    svc = FakeService()
    return TestClient(create_app(svc, token=TOKEN)), svc


def test_pagina_traz_o_token_e_estado_nao_vaza_segredo(client):
    c, _ = client
    page = c.get("/")
    assert page.status_code == 200 and TOKEN in page.text and "__TOKEN__" not in page.text
    st = c.get("/api/estado")
    assert st.status_code == 200
    assert SECRET not in st.text and "***" in st.text
    assert st.json()["valor"] is None  # NaN vira null (JSON válido)


def test_alteracoes_exigem_token(client):
    c, svc = client
    body = {"capital": 1000, "perda_max": 30, "par": "ETHBRL"}
    assert c.post("/api/sessao", json=body).status_code == 403
    assert c.post("/api/sessao", json=body, headers={"X-Painel-Token": "errado"}).status_code == 403
    assert c.post("/api/associar", json={"par": "ETHBRL", "operacao": 1, "sinal": 2}).status_code == 403
    assert svc.sessions == [] and svc.assocs == []
    ok = c.post("/api/sessao", json=body, headers={"X-Painel-Token": TOKEN})
    assert ok.status_code == 200 and svc.sessions == [body]


def test_erro_de_validacao_volta_como_400(client):
    c, _ = client
    r = c.post("/api/sessao", json={"capital": 100, "perda_max": 200}, headers={"X-Painel-Token": TOKEN})
    assert r.status_code == 400 and "menor que o capital" in r.json()["detail"]


def test_rotas_rodam_na_thread_do_servico(tmp_path):
    """SQLite só aceita a conexão na thread que a criou: as rotas não podem ir para outra thread."""
    import sqlite3
    import threading

    class SqliteService(FakeService):
        def __init__(self):
            super().__init__()
            self.conn = None

        def snapshot(self):
            if self.conn is None:  # cria a conexão na thread em que a rota roda
                self.conn = sqlite3.connect(tmp_path / "x.sqlite")
                self.thread = threading.get_ident()
            self.conn.execute("SELECT 1")
            return {"thread": threading.get_ident()}

    svc = SqliteService()
    with TestClient(create_app(svc, token=TOKEN)) as c:
        a = c.get("/api/estado").json()["thread"]
        b = c.get("/api/estado").json()["thread"]
    assert a == b  # mesma thread em toda chamada (a do loop), nunca um pool de threads


def test_arquivos_estaticos(client):
    c, _ = client
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/static/style.css").status_code == 200


def dec(action, exit_reason=None, blocks=()):
    d = Decision(action, 60.0, "ETHUSDT", "ETHBRL", 1_800_000_000_000, 2400.0)
    d.exit_reason = exit_reason
    d.blocks = list(blocks)
    d.reasons_for = ["motivo"]
    return d


def test_regras_de_alerta():
    assert alert_for_change(None, dec("ESPERAR"), 12000) is None  # início em ESPERAR: sem alerta
    assert alert_for_change("ESPERAR", dec("ESPERAR"), 12000) is None
    a = alert_for_change("ESPERAR", dec("COMPRAR"), 12000)
    assert a and "COMPRAR ETHBRL" in a.title and not a.urgent
    s = alert_for_change("ESPERAR", dec("VENDER", "stop"), 12000)
    assert s.urgent and "URGENTE" in s.title
    assert alert_for_change(None, dec("COMPRAR"), 12000) is not None


def test_alerta_de_perda_maxima_so_uma_vez_por_dia():
    al = Alerter(Alertas(terminal=False, windows=False), Console(quiet=True))
    d = dec("ESPERAR", blocks=["PERDA MÁXIMA DO DIA ATINGIDA"])
    al.on_decision("ESPERAR", d, 1)
    al.on_decision("ESPERAR", d, 1)
    assert sum("Perda máxima" in a.title for a in al.recent) == 1


def test_alerta_de_dados_so_depois_de_um_minuto(monkeypatch):
    import cryptoagent.alerts.notify as n

    al = Alerter(Alertas(terminal=False, windows=False), Console(quiet=True))
    t = [1_000_000]
    monkeypatch.setattr(n, "now_ms", lambda: t[0])
    al.on_health(False, ["candle atrasado"])
    assert not al.recent
    t[0] += 61_000
    al.on_health(False, ["candle atrasado"])
    assert al.recent[0].urgent
    al.on_health(True, [])
    assert al.recent[0].title == "Dados normalizados"
