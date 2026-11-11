"""Configuração de logs: arquivo rotativo em UTC, sempre passando pelo filtro de segredos.

Dois formatos (logs.formato no config):
- texto: uma linha legível por evento (padrão);
- json: um objeto JSON por linha (log estruturado), com os campos ts, nivel, logger, msg e,
  quando houver, erro (traceback) e os campos extras passados em `extra={...}`.
"""

from __future__ import annotations

import json
import logging
import time
from logging.handlers import RotatingFileHandler

from cryptoagent.config import Config, Secrets
from cryptoagent.security.redact import RedactingFormatter, redact, register_secrets

DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_FILE = "agente.log"
# Atributos que todo LogRecord tem; o que não está aqui veio de `extra={...}` e entra no JSON.
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}


class JsonRedactingFormatter(logging.Formatter):
    """Um objeto JSON por linha, em UTC, com segredos mascarados em todos os campos."""

    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        """Monta a linha JSON do registro, com os segredos mascarados."""
        payload: dict[str, object] = {
            "ts": self.formatTime(record, DATE_FORMAT),
            "nivel": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if record.exc_info:
            payload["erro"] = redact(self.formatException(record.exc_info))
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and key not in payload:
                payload[key] = value if isinstance(value, (int, float, bool)) or value is None else redact(value)
        return json.dumps(payload, ensure_ascii=False)


def build_formatter(formato: str) -> logging.Formatter:
    """Formatter do formato pedido (texto ou json), sempre com filtro de segredos e horário UTC."""
    if formato == "json":
        return JsonRedactingFormatter()
    formatter = RedactingFormatter(TEXT_FORMAT, DATE_FORMAT)
    formatter.converter = time.gmtime
    return formatter


def setup_logging(cfg: Config, secrets: Secrets) -> None:
    """Registra os segredos no filtro e liga o log em arquivo rotativo + avisos no terminal."""
    register_secrets(secrets.raw_values())

    log_dir = cfg.path(cfg.logs.pasta)
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_dir / LOG_FILE, maxBytes=int(cfg.logs.tamanho_max_mb * 1_000_000), backupCount=cfg.logs.arquivos_antigos, encoding="utf-8"
    )
    file_handler.setFormatter(build_formatter(cfg.logs.formato))

    # No terminal fica sempre o texto legível, mesmo quando o arquivo é JSON.
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(build_formatter("texto"))
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
