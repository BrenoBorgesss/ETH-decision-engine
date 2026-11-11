"""Política de repetição compartilhada por TODA a coleta de dados pela rede.

Usada pelo cliente REST (`data/rest.py`) e pelo download dos arquivos de histórico
(`data/history.py`), para que os dois tratem falhas do mesmo jeito:

- falha de rede, tempo esgotado e erro 5xx: tenta de novo, esperando cada vez mais
  (espera inicial × 2^tentativa, até um teto), com um pouco de aleatoriedade para não
  martelar o servidor em intervalos fixos;
- 429 (limite de requisições): espera o tempo que o servidor pediu no cabeçalho
  `Retry-After` antes de tentar de novo;
- 418 (IP bloqueado temporariamente): para na hora, sem insistir.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass

STATUS_RATE_LIMITED = 429
STATUS_IP_BANNED = 418


@dataclass(frozen=True)
class RetryPolicy:
    """Quantas vezes tentar e quanto esperar entre as tentativas."""
    attempts: int  # total de tentativas (1 = não repete)
    base_s: float = 0.5  # espera depois da 1ª falha; dobra a cada nova falha
    max_s: float = 30.0  # teto da espera entre tentativas
    retry_after_default_s: float = 60.0  # espera no 429 quando o servidor não informa o tempo
    retry_after_max_s: float = 300.0  # nunca espera mais que isso por um 429
    jitter: bool = True  # multiplica a espera por um valor entre 0,5 e 1,5

    def delay(self, attempt: int, rnd: Callable[[], float] = random.random) -> float:
        """Espera (s) depois da tentativa número `attempt` (0 = primeira) ter falhado."""
        base = min(self.max_s, self.base_s * 2**attempt)
        return base * (0.5 + rnd()) if self.jitter else base

    def retry_after(self, headers: Mapping[str, str]) -> float:
        """Espera (s) pedida pelo servidor num 429, limitada a `retry_after_max_s`."""
        value = str(headers.get("retry-after", "")).strip()
        wanted = float(value) if value.replace(".", "", 1).isdigit() else self.retry_after_default_s
        return min(wanted, self.retry_after_max_s)


def should_retry_status(status: int) -> bool:
    """True para os códigos HTTP que valem uma nova tentativa (erro do servidor)."""
    return status >= 500
