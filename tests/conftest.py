import pytest

from cryptoagent.security.redact import clear_registered_secrets


@pytest.fixture(autouse=True)
def _clean_secret_registry():
    clear_registered_secrets()
    yield
    clear_registered_secrets()
