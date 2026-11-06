import asyncio
import logging
import multiprocessing
import shutil
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from aiogram import Bot
from aiogram.types import (
    FSInputFile,
    InlineQueryResultArticle,
    InlineQueryResultCachedSticker,
    InputTextMessageContent,
)

from quoted_bot.config import Settings
from quoted_bot.domain.models import Quote, QuoteMessage
from quoted_bot.infrastructure.database import Database, JobRow, now
from quoted_bot.infrastructure.rendering import render_quote

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, database: Database, bot: Bot, settings: Settings):
        self.database, self.bot, self.settings = database, bot, settings
        self.pool = self._pool()

    @staticmethod
    def _pool() -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))

    async def download(self, file_id: str, path: Path) -> str | None:
        file = await self.bot.get_file(file_id)
        if not file.file_path or (file.file_size or 0) > self.settings.max_media_bytes:
            return None
        await self.bot.download_file(file.file_path, destination=path, timeout=20)
        if path.stat().st_size > self.settings.max_media_bytes:
            path.unlink()
            return None
        return str(path)

    async def prepare(self, quote: Quote, directory: Path) -> None:
        for index, message in enumerate(quote.messages):
            for entity in message.entities:
                try:
                    if entity["type"] == "avatar":
                        photos = await self.bot.get_user_profile_photos(entity["user_id"], limit=1)
                        if photos.photos:
                            message.avatar_path = await self.download(
                                photos.photos[0][-1].file_id, directory / f"avatar-{index}"
                            )
                    elif entity["type"] == "media":
                        kind = entity["kind"]
                        message.media_label = entity.get("file_name") or kind.upper()
                        if kind in {"photo", "sticker"}:
                            message.media_path = await self.download(
                                entity["file_id"], directory / f"media-{index}"
                            )
                except Exception:
                    logger.info("Optional quote media unavailable", exc_info=True)

    async def run(self) -> None:
        scratch = self.settings.data_dir / "render-work"
        scratch.mkdir(parents=True, exist_ok=True)
        for leftover in scratch.glob("quote-*"):
            if leftover.is_dir() and not leftover.is_symlink():
                shutil.rmtree(leftover)
        last_cleanup = now()
        try:
            while True:
                if now() - last_cleanup > timedelta(minutes=10):
                    await self.database.cleanup(now() - timedelta(hours=24))
                    last_cleanup = now()
                job = await self.database.claim()
                if job is None:
                    await asyncio.sleep(0.5)
                    continue
                await self.process(job)
        finally:
            self.pool.shutdown(wait=False, cancel_futures=True)

    async def process(self, job: JobRow) -> None:
        delivering = False
        try:
            scratch = self.settings.data_dir / "render-work"
            scratch.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="quote-", dir=scratch) as temp:
                directory = Path(temp)
                quote = Quote([QuoteMessage.from_dict(m) for m in job.payload["messages"]])
                await asyncio.wait_for(self.prepare(quote, directory), timeout=60)
                future = asyncio.get_running_loop().run_in_executor(
                    self.pool, render_quote, quote, str(directory / "quote.webp")
                )
                try:
                    result = await asyncio.wait_for(future, timeout=self.settings.render_timeout)
                except TimeoutError:
                    # Python 3.12 has no public terminate_workers API.
                    for process in self.pool._processes.values():
                        process.terminate()
                    self.pool.shutdown(wait=False, cancel_futures=True)
                    self.pool = self._pool()
                    raise ValueError(
                        "Генерация заняла слишком много времени. Сократите цитату."
                    ) from None
                destination = job.destination
                if "guest_query_id" in destination:
                    staged = await self.bot.send_sticker(
                        self.settings.staging_chat_id, FSInputFile(result)
                    )
                    if not staged.sticker:
                        raise RuntimeError("Telegram did not return a sticker")
                    await self.database.finish(job.id, "delivering")
                    delivering = True
                    await self.bot.answer_guest_query(
                        destination["guest_query_id"],
                        InlineQueryResultCachedSticker(
                            id=job.id, sticker_file_id=staged.sticker.file_id
                        ),
                    )
                    try:
                        await self.bot.delete_message(
                            self.settings.staging_chat_id, staged.message_id
                        )
                    except Exception:
                        logger.info("Could not remove staging message", exc_info=True)
                else:
                    await self.database.finish(job.id, "delivering")
                    delivering = True
                    await self.bot.send_sticker(destination["chat_id"], FSInputFile(result))
                await self.database.finish(job.id, "done")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Quote job %s failed", job.id)
            await self.database.finish(job.id, "uncertain" if delivering else "failed", str(error))
            if not delivering:
                text = (
                    str(error)
                    if isinstance(error, ValueError)
                    else "Не удалось создать цитату. Попробуйте ещё раз."
                )
                try:
                    if "guest_query_id" in job.destination:
                        await self.bot.answer_guest_query(
                            job.destination["guest_query_id"],
                            InlineQueryResultArticle(
                                id=job.id,
                                title="Не удалось создать цитату",
                                input_message_content=InputTextMessageContent(message_text=text),
                            ),
                        )
                    else:
                        await self.bot.send_message(job.destination["chat_id"], text)
                except Exception:
                    logger.info("Could not deliver failure notice", exc_info=True)
