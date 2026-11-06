from datetime import UTC, datetime

import pytest

from quoted_bot.application.service import Application
from quoted_bot.domain.models import QuoteMessage
from quoted_bot.infrastructure.database import Database


@pytest.fixture
async def database(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    yield db
    await db.engine.dispose()


def message(index, text="Привет"):
    return QuoteMessage(index, -100, datetime.now(UTC), "Алиса", text)


async def test_sequence_uses_order_not_contiguous_ids(database):
    app = Application(database)
    for index in (10, 12, 18):
        await app.ingest(message(index))
    assert [m.message_id for m in await app.select_sequence(-100, 10, 2)] == [10, 12, 18]
    with pytest.raises(ValueError):
        await app.select_sequence(-100, 10, 3)
    with pytest.raises(ValueError):
        await app.select_sequence(-100, 11, 0)


async def test_selection_is_isolated_and_deduplicated(database):
    app = Application(database)
    assert await app.add_selection(-100, 1, message(10)) == 1
    assert await app.add_selection(-100, 1, message(10)) == 1
    assert await app.get_selection(-100, 2) == []
    for index in range(11, 20):
        await app.add_selection(-100, 1, message(index))
    with pytest.raises(ValueError):
        await app.add_selection(-100, 1, message(20))
    assert len(await app.get_selection(-100, 1)) == 10


async def test_jobs_dedupe_and_restart_delivery_safety(database):
    app = Application(database)
    job_id = await app.enqueue([message(10)], {"chat_id": -100}, "event:1")
    assert await app.enqueue([message(10)], {"chat_id": -100}, "event:1") == job_id
    assert (await database.claim()).id == job_id
    assert await database.claim() is None
    await database.initialize()
    assert (await database.claim()).id == job_id
    await database.finish(job_id, "delivering")
    await database.initialize()
    assert await database.claim() is None


async def test_edit_updates_buffer_and_collect_survives_read(database):
    app = Application(database)
    await app.ingest(message(10, "До"))
    await app.ingest(message(10, "После"))
    assert (await app.select_sequence(-100, 10, 0))[0].text == "После"
    await app.set_collecting(-100, 1, True)
    assert await app.is_collecting(-100, 1)
    await app.clear_selection(-100, 1)
    assert not await app.is_collecting(-100, 1)
