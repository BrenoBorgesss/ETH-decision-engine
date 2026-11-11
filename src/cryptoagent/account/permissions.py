"""Checagem das permissões da chave da Binance (/sapi/v1/account/apiRestrictions).

Regra: a chave só pode LER. Qualquer outra permissão ligada (trading, saque,
transferência, margem, futuros, opções...) faz o agente avisar e se recusar a rodar.
Permissões novas que a Binance venha a criar também são recusadas por padrão:
só o que está em ALLOWED_TRUE pode estar ligado.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cryptoagent.data.rest import BinanceReadOnlyClient

# Únicas permissões que podem estar ligadas.
ALLOWED_TRUE = {"enableReading", "enableFixReadOnly"}
# Não é permissão de ação: é a restrição por IP (ligada = mais seguro).
NOT_A_PERMISSION = {"ipRestrict"}

LABELS = {
    "enableReading": "leitura",
    "enableSpotAndMarginTrading": "trading spot e margem",
    "enableWithdrawals": "SAQUES",
    "enableInternalTransfer": "transferência interna",
    "permitsUniversalTransfer": "transferência universal",
    "enableMargin": "margem",
    "enableFutures": "futuros",
    "enableVanillaOptions": "opções",
    "enablePortfolioMarginTrading": "portfolio margin",
    "enableFixApiTrade": "trading via FIX",
    "enableFixReadOnly": "leitura via FIX",
}


class ExcessivePermissions(Exception):
    """A chave tem permissões além de leitura: o agente não roda."""


@dataclass(frozen=True)
class PermissionCheck:
    """Resultado da checagem das permissões da chave: pode ler, quais permissões indevidas estão ligadas e se há restrição por IP."""
    can_read: bool
    forbidden: list[str]  # permissões ligadas que não deveriam estar
    ip_restricted: bool
    flags: dict[str, bool] = field(default_factory=dict)
    checked_ms: int | None = None

    @property
    def ok(self) -> bool:
        """True se a chave lê e não tem nenhuma permissão além de leitura."""
        return self.can_read and not self.forbidden

    def describe_forbidden(self) -> str:
        """Nomes em português das permissões indevidas, separados por vírgula."""
        return ", ".join(LABELS.get(k, k) for k in self.forbidden)

    def raise_if_unsafe(self) -> None:
        """Levanta ExcessivePermissions se a chave tiver qualquer permissão além de leitura."""
        if self.forbidden:
            raise ExcessivePermissions(
                "A chave da Binance tem permissões além de leitura: "
                f"{self.describe_forbidden()}. Por segurança o agente NÃO vai rodar. "
                "Edite a chave no site da Binance deixando só 'Enable Reading' (ou crie uma nova) e tente de novo."
            )


def evaluate_restrictions(data: dict, checked_ms: int | None = None) -> PermissionCheck:
    """Interpreta a resposta de apiRestrictions: tudo que estiver ligado e não for leitura (nem a restrição por IP) entra como indevido."""
    flags = {k: v for k, v in data.items() if isinstance(v, bool)}
    forbidden = sorted(k for k, v in flags.items() if v and k not in ALLOWED_TRUE and k not in NOT_A_PERMISSION)
    return PermissionCheck(
        can_read=flags.get("enableReading", False),
        forbidden=forbidden,
        ip_restricted=flags.get("ipRestrict", False),
        flags=flags,
        checked_ms=checked_ms,
    )


async def check_permissions(client: BinanceReadOnlyClient) -> PermissionCheck:
    """Consulta as permissões da chave na Binance (somente leitura) e as avalia."""
    resp = await client.get("/sapi/v1/account/apiRestrictions")
    return evaluate_restrictions(resp.data, resp.received_ms)  # type: ignore[arg-type]
