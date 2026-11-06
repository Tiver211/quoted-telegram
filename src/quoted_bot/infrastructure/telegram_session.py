from aiogram.client.session.aiohttp import AiohttpSession


def create_session(proxy_url: str | None = None) -> AiohttpSession:
    """One session for polling, API requests and Telegram file transfers."""
    return AiohttpSession(proxy=proxy_url)
