"""A chave só pode ler. Qualquer outra permissão = recusa."""

import pytest

from cryptoagent.account.permissions import ExcessivePermissions, evaluate_restrictions

READ_ONLY = {
    "ipRestrict": True,
    "createTime": 1700000000000,
    "enableReading": True,
    "enableWithdrawals": False,
    "enableInternalTransfer": False,
    "enableMargin": False,
    "enableFutures": False,
    "permitsUniversalTransfer": False,
    "enableVanillaOptions": False,
    "enableFixApiTrade": False,
    "enableFixReadOnly": True,
    "enableSpotAndMarginTrading": False,
    "enablePortfolioMarginTrading": False,
}


def test_chave_somente_leitura_passa():
    check = evaluate_restrictions(READ_ONLY)
    assert check.ok and check.can_read and check.ip_restricted and check.forbidden == []
    check.raise_if_unsafe()


@pytest.mark.parametrize(
    "flag",
    [
        "enableSpotAndMarginTrading",
        "enableWithdrawals",
        "enableInternalTransfer",
        "permitsUniversalTransfer",
        "enableFutures",
        "enableMargin",
        "enableFixApiTrade",
    ],
)
def test_qualquer_permissao_de_acao_e_recusada(flag):
    check = evaluate_restrictions({**READ_ONLY, flag: True})
    assert not check.ok and check.forbidden == [flag]
    with pytest.raises(ExcessivePermissions) as info:
        check.raise_if_unsafe()
    assert "NÃO vai rodar" in str(info.value)


def test_permissao_nova_desconhecida_tambem_e_recusada():
    check = evaluate_restrictions({**READ_ONLY, "enableSomethingNew": True})
    assert check.forbidden == ["enableSomethingNew"]


def test_sem_leitura_nao_esta_ok_mas_nao_e_perigosa():
    check = evaluate_restrictions({**READ_ONLY, "enableReading": False})
    assert not check.ok and not check.can_read and check.forbidden == []


def test_sem_restricao_de_ip_e_so_aviso():
    check = evaluate_restrictions({**READ_ONLY, "ipRestrict": False})
    assert check.ok and not check.ip_restricted
