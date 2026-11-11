"""Configuração de logs: arquivo rotativo em UTC, sempre passando pelo filtro de segredos."""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler

from cryptoagent.config import Config, Secrets
from cryptoagent.security.redact import RedactingFormatter, register_secrets


def setup_logging(cfg: Config, secrets: Secrets) -> None:
    register_secrets(secrets.raw_values())

    log_dir = cfg.path(cfg.logs.pasta)
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%SZ")
    formatter.converter = time.gmtime

    file_handler = RotatingFileHandler(log_dir / "agente.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.WARNING)
    # Tracebacks vão só para o arquivo de log; na tela fica a mensagem de erro curta.
    console_handler.addFilter(lambda record: not record.exc_info)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(cfg.logs.nivel)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Bibliotecas de rede logam URLs completas em INFO; deixamos só avisos.
    for noisy in ("httpx", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
