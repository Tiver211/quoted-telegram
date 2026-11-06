from datetime import UTC, datetime
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerGuestQuery, SendMessage
from aiogram.types import ChatMemberMember, Update, User

from quoted_bot.diagnostics import build_id
from quoted_bot.presentation.telegram import create_router, normalize

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"


def raw_message(message_id=1, chat_id=42, **extra):
    return {
        "message_id": message_id,
        "date": int(datetime.now(UTC).timestamp()),
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": 42, "is_bot": False, "first_name": "Получатель"},
        "text": "Привет",
        **extra,
    }


async def dispatch(app, update):
    bot = Bot(TOKEN)
    bot.session = AsyncMock()
    bot.session.return_value = True
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(app))
    await dispatcher.feed_update(bot, Update.model_validate(update))
    return bot


async def test_guest_quotes_reply_and_does_not_buffer_it():
    app = AsyncMock()
    original = raw_message(10, text="Исходная реплика")
    await dispatch(
        app,
        {
            "update_id": 1,
            "guest_message": raw_message(
                20, text="@quotedbot", guest_query_id="guest:1", reply_to_message=original
            ),
        },
    )
    messages, destination, _ = app.enqueue.call_args.args
    assert messages[0].text == "Исходная реплика"
    assert destination == {"guest_query_id": "guest:1"}
    app.ingest.assert_not_awaited()


async def test_private_collect_accumulates_instead_of_enqueuing():
    app = AsyncMock()
    app.is_collecting.return_value = True
    app.add_selection.return_value = 1
    await dispatch(app, {"update_id": 1, "message": raw_message()})
    app.add_selection.assert_awaited_once()
    app.enqueue.assert_not_awaited()


async def test_guest_without_reply_uses_guest_error_method():
    bot = await dispatch(
        AsyncMock(), {"update_id": 1, "guest_message": raw_message(guest_query_id="g")}
    )
    assert isinstance(bot.session.call_args.args[1], AnswerGuestQuery)
    assert not isinstance(bot.session.call_args.args[1], SendMessage)


def test_hidden_forward_preserves_name_without_forwarder_avatar():
    update = Update.model_validate(
        {
            "update_id": 1,
            "message": raw_message(
                forward_origin={
                    "type": "hidden_user",
                    "date": 12345678,
                    "sender_user_name": "Автор цитаты",
                }
            ),
        }
    )
    message = normalize(update.message)
    assert message.author_name == "Автор цитаты"
    assert message.author_id is None
    assert not any(e["type"] == "avatar" for e in message.entities)


async def test_single_quote_uses_reply_even_without_original_update():
    app = AsyncMock()
    original = raw_message(10, text="Реплика")
    update = Update.model_validate({"update_id": 1, "message": original})
    app.select_sequence.return_value = [normalize(update.message)]
    await dispatch(
        app,
        {
            "update_id": 2,
            "message": raw_message(
                20,
                text="/q",
                entities=[{"type": "bot_command", "offset": 0, "length": 2}],
                reply_to_message=original,
            ),
        },
    )
    app.ingest.assert_awaited_once()
    app.select_sequence.assert_awaited_once_with(42, 10, 0)
    app.enqueue.assert_awaited_once()


async def test_quote_count_over_limit_is_rejected_not_silently_truncated():
    app = AsyncMock()
    await dispatch(
        app,
        {
            "update_id": 2,
            "message": raw_message(
                20,
                text="/q 99",
                entities=[{"type": "bot_command", "offset": 0, "length": 2}],
                reply_to_message=raw_message(10),
            ),
        },
    )
    app.enqueue.assert_not_awaited()
    app.ingest.assert_not_awaited()


def external_quote_command(text="/q"):
    return raw_message(
        20,
        text=text,
        entities=[{"type": "bot_command", "offset": 0, "length": 2}],
        external_reply={
            "origin": {
                "type": "user",
                "date": 12345678,
                "sender_user": {"id": 77, "is_bot": False, "first_name": "Tiver"},
            }
        },
        quote={"text": "sdf", "position": 0},
    )


async def test_external_text_quote_without_reply_to_message():
    app = AsyncMock()
    await dispatch(app, {"update_id": 1, "message": external_quote_command()})
    messages, destination, _ = app.enqueue.call_args.args
    assert messages[0].text == "sdf"
    assert messages[0].author_name == "Tiver"
    assert messages[0].author_id == 77
    assert destination == {"chat_id": 42}
    app.ingest.assert_not_awaited()
    app.select_sequence.assert_not_awaited()


async def test_external_sequence_does_not_use_unrelated_chat_buffer():
    app = AsyncMock()
    await dispatch(app, {"update_id": 1, "message": external_quote_command("/q 2")})
    app.enqueue.assert_not_awaited()
    app.ingest.assert_not_awaited()


async def test_guest_external_reply_and_manual_external_selection():
    app = AsyncMock()
    app.add_selection.return_value = 1
    data = external_quote_command("@quotedbot")
    data["guest_query_id"] = "guest:external"
    await dispatch(app, {"update_id": 1, "guest_message": data})
    assert app.enqueue.call_args.args[0][0].text == "sdf"
    assert app.enqueue.call_args.args[1] == {"guest_query_id": "guest:external"}
    data = external_quote_command("/qadd")
    data["entities"][0]["length"] = 5
    await dispatch(app, {"update_id": 2, "message": data})
    assert app.add_selection.call_args.args[2].author_name == "Tiver"


async def test_quote_without_origin_does_not_invent_author():
    app = AsyncMock()
    data = external_quote_command()
    del data["external_reply"]
    await dispatch(app, {"update_id": 1, "message": data})
    quoted = app.enqueue.call_args.args[0][0]
    assert quoted.author_name == "Неизвестный автор"
    assert quoted.author_id is None


async def test_missing_group_reply_explains_access_instead_of_inventing_content():
    app = AsyncMock()
    data = raw_message(20, text="/q", entities=[{"type": "bot_command", "offset": 0, "length": 2}])
    data["chat"] = {"id": -100, "type": "supergroup", "title": "Test"}
    bot = await dispatch(app, {"update_id": 1, "message": data})
    response = bot.session.call_args.args[1]
    assert "/status" in response.text
    assert "Group Privacy" in response.text
    app.enqueue.assert_not_awaited()


async def test_status_identifies_build_and_group_access():
    bot = Bot(TOKEN)
    bot.session = AsyncMock()
    me = User(
        id=123456,
        is_bot=True,
        first_name="Quoted",
        username="quotedtestbot",
        can_read_all_group_messages=False,
    )
    bot.session.side_effect = [me, ChatMemberMember(user=me), True]
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(AsyncMock()))
    data = raw_message(
        20, text="/status", entities=[{"type": "bot_command", "offset": 0, "length": 7}]
    )
    data["chat"] = {"id": -100, "type": "supergroup", "title": "Test"}
    await dispatcher.feed_update(bot, Update.model_validate({"update_id": 1, "message": data}))
    response = bot.session.call_args.args[1]
    assert build_id() in response.text
    assert "@quotedtestbot" in response.text
    assert "Group Privacy: включён" in response.text
    assert "В этой группе: участник" in response.text


def rich_message(message_id=10, text="Обычный текст"):
    return raw_message(
        message_id, text=None, rich_message={"blocks": [{"type": "paragraph", "text": text}]}
    )


async def test_q_accepts_rich_message_reply_without_classic_text():
    app = AsyncMock()
    original = rich_message(text="fsdfsd")
    normalized = normalize(Update.model_validate({"update_id": 1, "message": original}).message)
    app.select_sequence.return_value = [normalized]
    await dispatch(
        app,
        {
            "update_id": 2,
            "message": raw_message(
                20,
                text="/q",
                entities=[{"type": "bot_command", "offset": 0, "length": 2}],
                reply_to_message=original,
            ),
        },
    )
    assert app.ingest.call_args.args[0].text == "fsdfsd"
    assert app.enqueue.call_args.args[0][0].text == "fsdfsd"


async def test_private_rich_message_is_not_silently_ignored():
    app = AsyncMock()
    app.is_collecting.return_value = False
    await dispatch(app, {"update_id": 1, "message": rich_message()})
    assert app.enqueue.call_args.args[0][0].text == "Обычный текст"


async def test_guest_and_manual_selection_accept_rich_replies():
    app = AsyncMock()
    app.add_selection.return_value = 1
    await dispatch(
        app,
        {
            "update_id": 1,
            "guest_message": raw_message(
                20, text="@quotedbot", guest_query_id="rich-guest", reply_to_message=rich_message()
            ),
        },
    )
    assert app.enqueue.call_args.args[0][0].text == "Обычный текст"
    await dispatch(
        app,
        {
            "update_id": 2,
            "message": raw_message(
                21,
                text="/qadd",
                entities=[{"type": "bot_command", "offset": 0, "length": 5}],
                reply_to_message=rich_message(),
            ),
        },
    )
    assert app.add_selection.call_args.args[2].text == "Обычный текст"


def test_nested_rich_formatting_preserves_utf16_offsets_and_paragraphs():
    data = rich_message(
        text=["😀", {"type": "bold", "text": ["Жирный", {"type": "italic", "text": " текст"}]}]
    )
    data["rich_message"]["blocks"].append({"type": "paragraph", "text": "Дальше"})
    normalized = normalize(Update.model_validate({"update_id": 1, "message": data}).message)
    assert normalized.text == "😀Жирный текст\nДальше"
    assert {"type": "bold", "offset": 2, "length": 12} in normalized.entities
    assert {"type": "italic", "offset": 8, "length": 6} in normalized.entities
