from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineQueryResultCachedSticker

from quoted_bot.config import Settings
from quoted_bot.domain.models import QuoteMessage
from quoted_bot.infrastructure.database import JobRow
from quoted_bot.infrastructure.worker import Worker


def job(destination):
    message = QuoteMessage(1, 42, datetime.now(UTC), "Алиса", "Привет!")
    return JobRow(id="test-job", payload={"messages": [message.to_dict()]}, destination=destination)


def worker(tmp_path):
    return Worker(
        AsyncMock(),
        AsyncMock(),
        Settings(
            bot_token="123456:ABC",
            staging_chat_id=-100123,
            data_dir=tmp_path,
        ),
    )


async def test_worker_renders_and_delivers_regular_sticker(tmp_path):
    instance = worker(tmp_path)
    try:
        await instance.process(job({"chat_id": 42}))
        instance.bot.send_sticker.assert_awaited_once()
        assert instance.bot.send_sticker.call_args.args[0] == 42
        assert instance.database.finish.call_args.args == ("test-job", "done")
        assert not list((tmp_path / "render-work").iterdir())
    finally:
        instance.pool.shutdown(wait=True)


async def test_guest_delivery_uses_staged_file_id(tmp_path):
    instance = worker(tmp_path)
    instance.bot.send_sticker.return_value = SimpleNamespace(
        sticker=SimpleNamespace(file_id="telegram-file-id"),
        message_id=123,
    )
    try:
        await instance.process(job({"guest_query_id": "guest-id"}))
        guest_id, result = instance.bot.answer_guest_query.call_args.args
        assert guest_id == "guest-id"
        assert isinstance(result, InlineQueryResultCachedSticker)
        assert result.sticker_file_id == "telegram-file-id"
        instance.bot.delete_message.assert_awaited_once_with(-100123, 123)
        assert instance.database.finish.call_args.args == ("test-job", "done")
    finally:
        instance.pool.shutdown(wait=True)


async def test_delivery_error_is_uncertain_and_not_retried(tmp_path):
    instance = worker(tmp_path)
    instance.bot.send_sticker.side_effect = TimeoutError("connection lost")
    try:
        await instance.process(job({"chat_id": 42}))
        assert instance.database.finish.call_args.args[1] == "uncertain"
        instance.bot.send_sticker.assert_awaited_once()
        instance.bot.send_message.assert_not_awaited()
    finally:
        instance.pool.shutdown(wait=True)
