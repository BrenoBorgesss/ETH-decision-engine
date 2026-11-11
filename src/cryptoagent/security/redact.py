"""Remove segredos de qualquer texto antes de ir para log, tela ou mensagem de erro.

Duas camadas:
1. Os valores reais das chaves (lidos do .env) são registrados e trocados por "***".
2. Padrões conhecidos (assinatura na URL, cabeçalho de chave, "api_key=...") são
   mascarados mesmo que o valor não tenha sido registrado.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

_MIN_SECRET_LEN = 8  # valores curtos demais não são registrados (evita mascarar palavras comuns)
MASK = "***"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(signature=)[^&\s'\"]+", re.IGNORECASE), r"\1" + MASK),
    (re.compile(r"(x-mbx-apikey['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+", re.IGNORECASE), r"\1" + MASK),
    (
        re.compile(r"((?:api[_-]?key|api[_-]?secret|secret)['\"]?\s*[:=]\s*['\"]?)[^\s'\",}&]+", re.IGNORECASE),
        r"\1" + MASK,
    ),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]+"), "sk-ant-" + MASK),
]

_registered: set[str] = set()


def register_secrets(values: Iterable[str | None]) -> None:
    """Registra valores secretos para serem mascarados; valores muito curtos são ignorados."""
    for value in values:
        if value and len(value) >= _MIN_SECRET_LEN:
            _registered.add(value)


def clear_registered_secrets() -> None:
    """Usado nos testes."""
    _registered.clear()


def redact(text: object) -> str:
    """Devolve o texto com os segredos registrados e os padrões conhecidos trocados por "***"."""
    s = str(text)
    for secret in sorted(_registered, key=len, reverse=True):
        s = s.replace(secret, MASK)
    for pattern, replacement in _PATTERNS:
        s = pattern.sub(replacement, s)
    return s


class RedactingFormatter(logging.Formatter):
    """Formatter que mascara segredos na linha final, incluindo tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        """Formata o registro e mascara os segredos na linha final."""
        return redact(super().format(record))
