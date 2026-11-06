import asyncio
import logging

from aiogram import Bot, Dispatcher

from quoted_bot.application.service import Application
from quoted_bot.config import Settings
from quoted_bot.diagnostics import build_id
from quoted_bot.infrastructure.database import Database
from quoted_bot.infrastructure.telegram_session import create_session
from quoted_bot.infrastructure.worker import Worker
from quoted_bot.presentation.telegram import create_router


async def main() -> None:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=settings.log_level)
    logging.getLogger(__name__).info("Starting quoted-bot build=%s", build_id())
    database = Database(settings.database_url)
    await database.initialize()
    session = create_session(settings.proxy_url.get_secret_value() if settings.proxy_url else None)
    bot = Bot(settings.bot_token, session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(Application(database)))
    # Ordered updates avoid races between buffering, selection and /qmake.
    worker = asyncio.create_task(Worker(database, bot, settings).run())
    polling = None
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        polling = asyncio.create_task(
            dispatcher.start_polling(bot, handle_as_tasks=False, close_bot_session=False)
        )
        done, _ = await asyncio.wait({worker, polling}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        worker.cancel()
        if polling:
            polling.cancel()
        await asyncio.gather(*([worker, polling] if polling else [worker]), return_exceptions=True)
        await bot.session.close()
        await database.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
