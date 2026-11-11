"""Saúde dos dados: idade, atraso de candles, relógio e divergência entre fontes.

Qualquer verificação crítica com problema faz o sinal virar ESPERAR (regra 3 de segurança).
Verificações não críticas (ex.: derivativos) só reduzem a confiança e aparecem como aviso.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cryptoagent.timeutils import interval_ms


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    age_s: float | None = None
    limit_s: float | None = None
    source: str | None = None
    critical: bool = True


@dataclass
class HealthReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def ok(self) -> bool:
        """True só se todas as verificações CRÍTICAS estiverem ok."""
        return all(c.ok for c in self.checks if c.critical)

    @property
    def problems(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.ok and c.critical]

    @property
    def warnings(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.ok and not c.critical]


def check_age(name: str, received_ms: int | None, now_ms: int, max_age_s: float, source: str | None = None, *, critical: bool = True) -> Check:
    if received_ms is None:
        return Check(name, False, "dado ausente", None, max_age_s, source, critical)
    age = (now_ms - received_ms) / 1000
    if age > max_age_s:
        return Check(name, False, f"dado velho: {age:.1f} s (limite {max_age_s:g} s)", age, max_age_s, source, critical)
    return Check(name, True, f"atualizado há {age:.1f} s", age, max_age_s, source, critical)


def check_candle(name: str, last_closed_close_ms: int | None, interval: str, now_ms: int, tolerance_s: float, source: str | None = None) -> Check:
    """O último candle fechado precisa ter terminado há no máximo (intervalo + tolerância).

    Ex.: 1m com tolerância de 30 s -> o último candle fechado não pode ter mais de 90 s.
    """
    limit = interval_ms(interval) / 1000 + tolerance_s
    if last_closed_close_ms is None:
        return Check(name, False, "sem candle fechado", None, limit, source)
    delay = (now_ms - last_closed_close_ms) / 1000
    if delay > limit:
        return Check(name, False, f"candle atrasado: fechou há {delay:.0f} s (limite {limit:.0f} s)", delay, limit, source)
    return Check(name, True, f"último candle fechou há {delay:.0f} s", delay, limit, source)


def check_clock(offset_ms: float, max_ms: float) -> Check:
    """`offset_ms` = relógio da Binance − relógio do PC. Negativo = PC adiantado; positivo = PC atrasado."""
    direction = "adiantado" if offset_ms < 0 else "atrasado"
    detail = f"PC {direction} {abs(offset_ms) / 1000:.1f} s em relação à Binance"
    if abs(offset_ms) > max_ms:
        return Check(
            "relógio do PC",
            False,
            f"{detail} (limite {max_ms / 1000:.1f} s); sincronize o relógio do Windows",
            source="binance-rest:/api/v3/time",
        )
    return Check("relógio do PC", True, detail, source="binance-rest:/api/v3/time")


def check_divergence(name: str, a: float | None, b: float | None, max_pct: float) -> Check:
    """Compara o mesmo preço vindo de duas fontes (ex.: WebSocket x REST)."""
    if a is None or b is None or a <= 0 or b <= 0:
        return Check(name, False, "falta uma das fontes para comparar")
    diff = abs(a - b) / ((a + b) / 2) * 100
    if diff > max_pct:
        return Check(name, False, f"fontes divergem {diff:.3f}% (limite {max_pct:g}%)")
    return Check(name, True, f"fontes batem (diferença {diff:.3f}%)")
