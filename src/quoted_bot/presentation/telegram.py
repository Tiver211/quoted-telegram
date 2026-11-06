"""Telegram adapter: normalization and user interaction only."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from hashlib import sha256

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)

from quoted_bot.application.service import Application
from quoted_bot.diagnostics import build_id
from quoted_bot.domain.models import QuoteMessage
from quoted_bot.presentation.rich_text import extract_rich_text

logger = logging.getLogger(__name__)


def normalize(message: Message) -> QuoteMessage:
    user = message.from_user
    author_id = user.id if user else None
    author = (
        user.full_name
        if user
        else (message.sender_chat.title if message.sender_chat else "Неизвестный автор")
    )
    if message.sender_chat:
        author, author_id = message.sender_chat.title or "Канал", None
    origin = message.forward_origin
    if origin:
        if origin.type == "user":
            user = origin.sender_user
            author, author_id = user.full_name, user.id
        elif origin.type == "hidden_user":
            author, author_id = origin.sender_user_name, None
        elif origin.type == "chat":
            author, author_id = origin.sender_chat.title or "Чат", None
        else:
            author, author_id = origin.chat.title or "Канал", None
    entities = [
        e.model_dump(mode="json", exclude_none=True)
        for e in (message.entities or message.caption_entities or [])
    ]
    text = message.text or message.caption or ""
    if not text and message.rich_message:
        text, rich_entities = extract_rich_text(message.rich_message)
        entities.extend(rich_entities)
    if author_id:
        entities.append({"type": "avatar", "user_id": author_id})
    if message.photo:
        entities.append({"type": "media", "kind": "photo", "file_id": message.photo[-1].file_id})
    else:
        for kind in ("animation", "video", "document", "audio", "voice", "sticker", "video_note"):
            media = getattr(message, kind, None)
            if media:
                entities.append(
                    {
                        "type": "media",
                        "kind": kind,
                        "file_id": media.file_id,
                        "file_name": getattr(media, "file_name", None),
                    }
                )
                break
    label = next(
        (e.get("file_name") or e["kind"].upper() for e in entities if e["type"] == "media"), None
    )
    return QuoteMessage(
        message_id=message.message_id,
        chat_id=message.chat.id,
        sent_at=message.date or datetime.now(UTC),
        author_name=author,
        author_id=author_id,
        text=text,
        media_label=label,
        entities=entities,
    )


def is_content(message: Message) -> bool:
    return bool(
        message.text
        or message.caption
        or message.photo
        or message.video
        or message.document
        or message.animation
        or message.audio
        or message.voice
        or message.sticker
        or message.video_note
        or (message.rich_message and extract_rich_text(message.rich_message)[0])
    )


def is_command(message: Message) -> bool:
    return bool(message.text and message.text.startswith("/"))


def resolve_reply(message: Message) -> tuple[QuoteMessage, bool]:
    """Resolve full replies or Telegram's external reply/selected text representation.

    The boolean means the source can participate in the current chat's sequence.
    External reply snapshots must never be inserted into that buffer.
    """
    original = message.reply_to_message
    if original and is_content(original):
        return normalize(original), original.chat.id == message.chat.id
    external = message.external_reply
    quote = message.quote
    if external:
        data = external.model_dump(mode="json", exclude_none=True)
        source = Message.model_validate(
            {
                **{key: value for key, value in data.items() if key in Message.model_fields},
                "message_id": external.message_id or -message.message_id,
                "chat": (external.chat or message.chat).model_dump(mode="json"),
                "date": external.origin.date,
                "forward_origin": external.origin.model_dump(mode="json"),
                "text": quote.text if quote else None,
                "entities": [e.model_dump(mode="json") for e in quote.entities or []]
                if quote
                else [],
            }
        )
        if is_content(source):
            return normalize(source), False
    if quote and quote.text:
        return QuoteMessage(
            message_id=-message.message_id,
            chat_id=message.chat.id,
            sent_at=message.date,
            author_name="Неизвестный автор",
            text=quote.text,
            entities=[e.model_dump(mode="json", exclude_none=True) for e in quote.entities or []],
        ), False
    logger.info(
        "Reply content unavailable: reply=%s external=%s quote=%s",
        original is not None,
        external is not None,
        quote is not None,
    )
    if message.chat.type in {"group", "supergroup"}:
        raise ValueError(
            "Telegram не передал исходное сообщение. Проверьте /status. "
            "Назначьте бота администратором или отключите Group Privacy в BotFather "
            "и заново добавьте бота в группу. Затем повторите /q на новом сообщении."
        )
    raise ValueError(
        "Telegram не передал текст исходного сообщения. Перешлите его боту или выберите текст через «Цитировать»."
    )


def key(message: Message, action: str) -> str:
    return f"telegram:{message.chat.id}:{message.message_id}:{action}"


def selection_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Создать", callback_data="collection:create"),
                InlineKeyboardButton(text="Отмена", callback_data="collection:cancel"),
            ]
        ]
    )


async def guest_error(message: Message, text: str) -> None:
    await message.bot.answer_guest_query(
        message.guest_query_id,
        InlineQueryResultArticle(
            id="error",
            title="Цитата",
            input_message_content=InputTextMessageContent(message_text=text),
        ),
    )


def create_router(app: Application) -> Router:
    router = Router(name="quotes")

    @router.message(Command("status"))
    async def status_command(message: Message) -> None:
        me = await message.bot.get_me()
        lines = [f"Сборка: {build_id()}", f"Бот: @{me.username}"]
        lines.append(
            "Group Privacy: выключен"
            if me.can_read_all_group_messages
            else "Group Privacy: включён"
        )
        if message.chat.type in {"group", "supergroup"}:
            member = await message.bot.get_chat_member(message.chat.id, me.id)
            admin = member.status in {"administrator", "creator"}
            lines.append("В этой группе: администратор" if admin else "В этой группе: участник")
            if not admin and not me.can_read_all_group_messages:
                lines.append(
                    "Для цитат назначьте бота администратором либо отключите Group Privacy и добавьте бота в группу заново."
                )
            elif not admin:
                lines.append(
                    "После отключения Group Privacy бота нужно заново добавить в существующую группу."
                )
        await message.answer("\n".join(lines))

    @router.message(Command("start", "help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "Ответьте на сообщение командой /q. /q 3 — оно и три следующих сообщения "
            "из буфера группы (до 10 всего).\n"
            "/qadd ответом — добавить реплику вручную; /qmake — создать; /qclear — очистить.\n"
            "В личке пересылайте сообщение для цитаты. /collect — собрать несколько, "
            "затем нажмите «Создать».\n"
            "Без добавления бота: ответьте на сообщение, отправив @имябота (Guest Mode)."
            "\n/status — версия кода и доступ к сообщениям."
        )

    @router.message(Command("q"))
    async def quote_command(message: Message, command: CommandObject) -> None:
        logger.info(
            "Quote request build=%s chat_type=%s reply=%s reply_text=%s reply_media=%s external=%s quote=%s reply_rich=%s reply_fields=%s",
            build_id(),
            message.chat.type,
            message.reply_to_message is not None,
            bool(
                message.reply_to_message
                and (message.reply_to_message.text or message.reply_to_message.caption)
            ),
            bool(
                message.reply_to_message
                and any(
                    getattr(message.reply_to_message, field, None)
                    for field in ("photo", "document", "sticker", "video", "voice")
                )
            ),
            message.external_reply is not None,
            message.quote is not None,
            bool(message.reply_to_message and message.reply_to_message.rich_message),
            sorted(message.reply_to_message.model_fields_set) if message.reply_to_message else [],
        )
        try:
            try:
                following = int(command.args or "0")
            except ValueError:
                raise ValueError("Используйте /q или /q N, где N — число от 0 до 9.") from None
            if not 0 <= following <= 9:
                raise ValueError("Укажите от 0 до 9 следующих сообщений.")
            original, same_chat = resolve_reply(message)
            if following and not same_chat:
                raise ValueError(
                    "Для /q N нужен обычный ответ на сообщение в этой группе. Внешняя цитата поддерживает только /q."
                )
            # Reply context remains usable even if the bot never saw the original update.
            if same_chat:
                await app.ingest(original)
                messages = await app.select_sequence(
                    message.chat.id, original.message_id, following
                )
            else:
                messages = [original]
            await app.enqueue(messages, {"chat_id": message.chat.id}, key(message, "q"))
        except ValueError as error:
            await message.answer(str(error))

    @router.message(Command("qadd"))
    async def add_command(message: Message) -> None:
        if not message.from_user:
            return
        try:
            original, _ = resolve_reply(message)
            count = await app.add_selection(message.chat.id, message.from_user.id, original)
            await message.answer(f"В подборке: {count}/10. /qmake — создать, /qclear — отменить.")
        except ValueError as error:
            await message.answer(str(error))

    @router.message(Command("qmake"))
    async def make_command(message: Message) -> None:
        if not message.from_user:
            return
        messages = await app.get_selection(message.chat.id, message.from_user.id)
        if not messages:
            await message.answer("Подборка пуста. Добавьте сообщения через /qadd или /collect.")
            return
        await app.enqueue(messages, {"chat_id": message.chat.id}, key(message, "make"))
        await app.clear_selection(message.chat.id, message.from_user.id)

    @router.message(Command("qclear"))
    async def clear_command(message: Message) -> None:
        if message.from_user:
            await app.clear_selection(message.chat.id, message.from_user.id)
            await message.answer("Подборка очищена.")

    @router.message(Command("collect"))
    async def collect_command(message: Message) -> None:
        if message.chat.type != "private" or not message.from_user:
            await message.answer("В группе используйте /qadd. /collect доступен в личке с ботом.")
            return
        await app.set_collecting(message.chat.id, message.from_user.id, True)
        await message.answer(
            "Пересылайте сообщения (до 10), затем нажмите «Создать».",
            reply_markup=selection_keyboard(),
        )

    @router.callback_query(F.data.in_({"collection:create", "collection:cancel"}))
    async def collection_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message) or callback.message.chat.type != "private":
            await callback.answer("Откройте личку с ботом.", show_alert=True)
            return
        chat_id, user_id = callback.message.chat.id, callback.from_user.id
        if callback.data == "collection:create":
            messages = await app.get_selection(chat_id, user_id)
            if not messages:
                await callback.answer("Подборка пуста.", show_alert=True)
                return
            # Keyboard message identity is stable across repeated button presses.
            fingerprint = sha256(repr([m.to_dict() for m in messages]).encode()).hexdigest()[:16]
            await app.enqueue(
                messages, {"chat_id": chat_id}, key(callback.message, f"collection:{fingerprint}")
            )
        await app.clear_selection(chat_id, user_id)
        await callback.answer(
            "Добавлено в очередь" if callback.data == "collection:create" else "Отменено"
        )
        await callback.message.edit_reply_markup(reply_markup=None)

    @router.guest_message()
    async def guest_message(message: Message) -> None:
        try:
            original, _ = resolve_reply(message)
        except ValueError as error:
            await guest_error(message, str(error))
            return
        # Guest contexts must never be merged into the ordinary chat buffer.
        await app.enqueue(
            [original],
            {"guest_query_id": message.guest_query_id},
            f"guest:{message.guest_query_id}",
        )

    @router.message()
    async def content_message(message: Message) -> None:
        if (
            not is_content(message)
            or is_command(message)
            or (message.from_user and message.from_user.is_bot)
        ):
            return
        normalized = normalize(message)
        if message.chat.type in {"group", "supergroup"}:
            await app.ingest(normalized)
        elif message.chat.type == "private" and message.from_user:
            if await app.is_collecting(message.chat.id, message.from_user.id):
                try:
                    count = await app.add_selection(
                        message.chat.id, message.from_user.id, normalized
                    )
                    await message.answer(
                        f"В подборке: {count}/10", reply_markup=selection_keyboard()
                    )
                except ValueError as error:
                    await message.answer(str(error))
            else:
                await app.enqueue(
                    [normalized], {"chat_id": message.chat.id}, key(message, "private")
                )

    @router.edited_message()
    async def edited_message(message: Message) -> None:
        if (
            message.chat.type in {"group", "supergroup"}
            and is_content(message)
            and not is_command(message)
        ):
            await app.ingest(normalize(message))

    return router
