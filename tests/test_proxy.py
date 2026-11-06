import pytest
from pydantic import ValidationError

from quoted_bot.config import Settings
from quoted_bot.infrastructure.telegram_session import create_session


def settings(proxy_url):
    return Settings(
        _env_file=None, bot_token="123456:ABC", staging_chat_id=-100, proxy_url=proxy_url
    )


@pytest.mark.parametrize("scheme", ["http", "socks4", "socks5"])
async def test_proxy_connector_supports_authentication_and_remote_dns(scheme):
    config = settings(f"{scheme}://user:p%40ss@proxy.example:1080")
    session = create_session(config.proxy_url.get_secret_value())
    try:
        options = session._connector_init
        assert options["host"] == "proxy.example"
        assert options["port"] == 1080
        assert options["username"] == "user"
        assert options["password"] == "p@ss"
        assert options["rdns"] is True
        assert "p%40ss" not in repr(config)
    finally:
        await session.close()


@pytest.mark.parametrize("value", [None, ""])
async def test_unset_proxy_uses_direct_connection(value):
    config = settings(value)
    assert config.proxy_url is None
    session = create_session()
    try:
        assert session.proxy is None
    finally:
        await session.close()


@pytest.mark.parametrize(
    "value",
    ["https://user:secret@host:123", "socks5://host", "http://host:99999", "http://host:80/path"],
)
def test_invalid_proxy_fails_without_exposing_credentials(value):
    with pytest.raises(ValidationError) as error:
        settings(value)
    assert "secret" not in str(error.value)
