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
LOCAL = "http://127.0.0.1:8765"  # o painel só aceita ser chamado por 127.0.0.1/localhost
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
    return TestClient(create_app(svc, token=TOKEN), base_url=LOCAL), svc


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
            if getattr(self, "fechar", False):
                self.conn.close()  # fecha na mesma thread que criou (o SQLite não aceita outra)
            return {"thread": threading.get_ident()}

    svc = SqliteService()
    with TestClient(create_app(svc, token=TOKEN), base_url=LOCAL) as c:
        a = c.get("/api/estado").json()["thread"]
        b = c.get("/api/estado").json()["thread"]
        svc.fechar = True
        assert c.get("/api/estado").json()["thread"] == b
    assert a == b  # mesma thread em toda chamada (a do loop), nunca um pool de threads


@pytest.mark.parametrize(
    "host", ["evil.example.com", "evil.example.com:8765", "127.0.0.1.evil.example.com", "192.168.0.10:8765"]
)
def test_host_de_fora_e_recusado_em_todas_as_rotas(host):
    """DNS rebinding: um site externo chamando o painel pelo próprio domínio não lê nem altera nada."""
    svc = FakeService()
    c = TestClient(create_app(svc, token=TOKEN), base_url=f"http://{host}")
    for path in ("/", "/api/estado", "/api/candles", "/api/sazonalidade", "/static/app.js"):
        r = c.get(path)
        assert r.status_code == 400, path
        assert TOKEN not in r.text
    body = {"capital": 1000, "perda_max": 30, "par": "ETHBRL"}
    assert c.post("/api/sessao", json=body, headers={"X-Painel-Token": TOKEN}).status_code == 400
    assert svc.sessions == []


@pytest.mark.parametrize("base", ["http://127.0.0.1:8765", "http://localhost:8765", "http://127.0.0.1"])
def test_host_local_e_aceito(base):
    c = TestClient(create_app(FakeService(), token=TOKEN), base_url=base)
    assert c.get("/api/estado").status_code == 200


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


def test_notificacao_do_windows_nao_herda_as_chaves(monkeypatch):
    """O PowerShell que mostra a notificação é um processo filho: não pode receber as chaves do .env."""
    import cryptoagent.alerts.notify as n

    seen = {}
    monkeypatch.setenv("BINANCE_API_KEY", SECRET)
    monkeypatch.setenv("BINANCE_API_SECRET", SECRET + "_B")
    monkeypatch.setattr(n.subprocess, "Popen", lambda cmd, **kw: seen.update(kw))
    Alerter(Alertas(terminal=False, windows=True), Console(quiet=True))._toast(n.Alert(1, "título", "corpo"))
    env = seen["env"]
    assert env["CA_TITLE"] == "título" and env["CA_BODY"] == "corpo"
    assert "BINANCE_API_KEY" not in env and "BINANCE_API_SECRET" not in env
    assert SECRET not in " ".join(env.values())


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


def test_resumo_do_paper_vem_do_banco_da_simulacao_em_modo_leitura(tmp_path):
    """O painel lê a carteira virtual do banco SÓ da simulação, sem nunca gravar nele."""
    from cryptoagent.config import load_config
    from cryptoagent.dashboard.service import LiveService
    from cryptoagent.storage import sim_db

    cfg = load_config()
    cfg.paper.banco = str(tmp_path / "simulacao.sqlite")
    fake = LiveService.__new__(LiveService)  # só o método de leitura, sem abrir conexões nem a Binance
    fake.cfg = cfg
    fake.sim_db_path = cfg.path(cfg.paper.banco)
    assert fake._paper_summary() == []  # simulação nunca rodou
    conn = sim_db.connect(tmp_path / "simulacao.sqlite")
    conn.execute(
        "INSERT INTO paper_state (variant, equity, initial_capital, max_loss_day, started_ms) VALUES ('padrao', 1010, 1000, 30, 1)"
    )
    conn.commit()
    conn.close()
    antes = (tmp_path / "simulacao.sqlite").stat().st_mtime_ns
    resumo = fake._paper_summary()
    assert resumo == [{"variant": "padrao", "equity": 1010.0, "started_ms": 1, "fechadas": 0, "abertas": 0}]
    assert (tmp_path / "simulacao.sqlite").stat().st_mtime_ns == antes
