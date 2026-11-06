from datetime import UTC, datetime

import pytest
from PIL import Image

from quoted_bot.domain.models import Quote, QuoteMessage
from quoted_bot.infrastructure.rendering import render_quote


def msg(text):
    return QuoteMessage(
        message_id=1, chat_id=2, sent_at=datetime.now(UTC), author_name="Алиса", text=text
    )


def test_render_webp_is_transparent_and_within_dimensions(tmp_path):
    target = tmp_path / "quote.webp"
    result = render_quote(Quote(messages=[msg("hello world")]), str(target))
    with Image.open(result) as image:
        assert image.format == "WEBP"
        assert image.width == 512 and image.height <= 512
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
    assert target.stat().st_size <= 512 * 1024


def test_multiline_text_uses_more_height(tmp_path):
    short, long = tmp_path / "short.webp", tmp_path / "long.webp"
    render_quote(Quote(messages=[msg("one line")]), str(short))
    render_quote(Quote(messages=[msg("first line\nsecond line\nthird line")]), str(long))
    with Image.open(short) as a, Image.open(long) as b:
        assert b.height > a.height


def test_too_many_messages_rejected(tmp_path):
    with pytest.raises(ValueError):
        render_quote(Quote(messages=[msg("x") for _ in range(11)]), str(tmp_path / "x.webp"))


def test_ten_short_messages_fit(tmp_path):
    result = render_quote(Quote([msg("Да") for _ in range(10)]), str(tmp_path / "ten.webp"))
    with Image.open(result) as image:
        assert image.size[0] == 512 and image.size[1] <= 512


def test_utf16_styles_after_emoji():
    from quoted_bot.infrastructure.rendering import styles

    message = msg("😀bold")
    message.entities = [{"type": "bold", "offset": 2, "length": 4}]
    assert styles(message) == [set(), {"bold"}, {"bold"}, {"bold"}, {"bold"}]


def test_long_unbroken_token_has_no_inserted_spaces():
    from quoted_bot.infrastructure.rendering import layout

    original = "x" * 200
    lines = layout(msg(original), 16, 100)
    assert "".join(char for line in lines for char, _ in line) == original
