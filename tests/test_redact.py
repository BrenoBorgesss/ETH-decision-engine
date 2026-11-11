"""Segredos nunca aparecem em logs nem em mensagens."""

import io
import logging

from cryptoagent.security.redact import RedactingFormatter, redact, register_secrets

KEY = "AbCdEfGh1234567890KEYVALUE"
SECRET = "zYxWvU9876543210SECRETVALUE"


def test_valores_registrados_sao_mascarados():
    register_secrets([KEY, SECRET])
    text = redact(f"erro com {KEY} e {SECRET}")
    assert KEY not in text and SECRET not in text
    assert "***" in text


def test_assinatura_na_url_e_mascarada_mesmo_sem_registro():
    url = "https://api.binance.com/api/v3/account?timestamp=1&recvWindow=5000&signature=deadbeef1234"
    assert "deadbeef1234" not in redact(url)


def test_cabecalho_de_chave_e_mascarado():
    text = redact("{'X-MBX-APIKEY': 'minhachave123456'}")
    assert "minhachave123456" not in text


def test_chave_anthropic_e_mascarada():
    assert "abc123def456" not in redact("sk-ant-api03-abc123def456")


def test_valores_curtos_nao_sao_registrados():
    register_secrets(["abc"])
    assert redact("abc abc") == "abc abc"


def test_formatter_mascara_inclusive_traceback():
    register_secrets([SECRET])
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(message)s"))
    logger = logging.getLogger("teste.redact")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        raise RuntimeError(f"falhou usando {SECRET}")
    except RuntimeError:
        logger.exception("erro na chamada com %s", SECRET)
    output = stream.getvalue()
    assert SECRET not in output
    assert "RuntimeError" in output
