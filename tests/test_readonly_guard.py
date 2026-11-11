"""Varredura do código-fonte: nada que envie ordens, saques ou transferências pode existir."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

FORBIDDEN = [
    r"\.post\(",
    r"\.put\(",
    r"\.delete\(",
    r"\.patch\(",
    r"""["']POST["']""",
    r"""["']DELETE["']""",
    r"/api/v3/order",
    r"/sapi/v1/margin/order",
    r"/fapi/v1/order",
    r"/withdraw",
    r"/sapi/v1/asset/transfer",
    r"listenKey",
    r"userDataStream",
]


# Única exceção: as rotas LOCAIS do painel (127.0.0.1) que recebem os formulários (sessão do dia, associação e
# diário manual). São decoradores do FastAPI em server.py, não chamadas para fora.
LOCAL_ROUTES = {("cryptoagent/dashboard/server.py", re.compile(r"^\s*@app\.post\(\"/api/[a-z]+(?:/[a-z]+)?\"\)\s*$"))}


def _allowed(rel: str, line: str) -> bool:
    return any(rel == f and rx.match(line) for f, rx in LOCAL_ROUTES)


def test_codigo_fonte_nao_tem_chamadas_de_escrita():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern in FORBIDDEN:
                if re.search(pattern, line) and not _allowed(rel, line):
                    offenders.append(f"{rel}:{n}: {pattern}")
    assert offenders == []


def test_servidor_do_painel_nao_conecta_em_nada_externo():
    text = (SRC / "cryptoagent" / "dashboard" / "server.py").read_text(encoding="utf-8")
    for outbound in ("httpx", "BinanceReadOnlyClient", "requests", "urllib", "websockets"):
        assert outbound not in text


def test_painel_so_escuta_no_proprio_computador():
    cli = "\n".join(p.read_text(encoding="utf-8") for p in sorted((SRC / "cryptoagent" / "cli").glob("*.py")))
    assert 'host="127.0.0.1"' in cli and "0.0.0.0" not in cli


def test_env_esta_no_gitignore():
    gitignore = (SRC.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore
