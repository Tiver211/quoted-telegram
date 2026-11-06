"""Static quote renderer inspired by QuoteD (ryotairi/quoted, AGPL-3.0-only).

Telegram entities use UTF-16 offsets; layout uses Python indices after conversion.
This implementation is independent of the Matrix transport and @napi-rs/canvas.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from quoted_bot.domain.models import Quote, QuoteMessage

SIZE = 512
FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
CARD = (35, 39, 46, 255)
TEXT = (242, 244, 247, 255)
ACCENT = (135, 178, 255, 255)
Image.MAX_IMAGE_PIXELS = 20_000_000


@lru_cache(maxsize=64)
def font(size: int, style: str = "regular") -> ImageFont.FreeTypeFont:
    noto = {"regular": "Regular", "bold": "Bold", "italic": "Italic", "mono": "Regular"}[style]
    family = "NotoSansMono" if style == "mono" else "NotoSans"
    dejavu = {
        "regular": "DejaVuSans",
        "bold": "DejaVuSans-Bold",
        "italic": "DejaVuSans-Oblique",
        "mono": "DejaVuSansMono",
    }[style]
    choices = [
        str(FONT_DIR / f"{family}-{noto}.ttf"),
        f"/usr/share/fonts/truetype/noto/{family}-{noto}.ttf",
        f"/usr/share/fonts/truetype/dejavu/{dejavu}.ttf",
    ]
    for candidate in choices:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def styles(message: QuoteMessage) -> list[set[str]]:
    result = [set() for _ in message.text]
    offsets = [0]
    for char in message.text:
        offsets.append(offsets[-1] + len(char.encode("utf-16-le")) // 2)
    for entity in message.entities:
        kind = entity.get("type")
        if kind not in {"bold", "italic", "code", "pre", "underline", "strikethrough", "spoiler"}:
            continue
        start = entity.get("offset", 0)
        end = start + entity.get("length", 0)
        for index in range(len(message.text)):
            if start <= offsets[index] < end:
                result[index].add(kind)
    return result


def char_font(size: int, formatting: set[str]) -> ImageFont.FreeTypeFont:
    style = (
        "mono"
        if formatting & {"code", "pre"}
        else "bold"
        if "bold" in formatting
        else "italic"
        if "italic" in formatting
        else "regular"
    )
    return font(size, style)


def layout(message: QuoteMessage, size: int, width: int) -> list[list[tuple[str, set[str]]]]:
    lines: list[list[tuple[str, set[str]]]] = [[]]
    width_used = 0.0
    formatting = styles(message)
    for index, char in enumerate(message.text):
        if char == "\n":
            lines.append([])
            width_used = 0
            continue
        flags = formatting[index]
        char_width = char_font(size, flags).getlength(char)
        if width_used + char_width > width and lines[-1]:
            # Prefer word boundaries; long unbroken tokens split without extra spaces.
            last_space = next(
                (i for i in range(len(lines[-1]) - 1, -1, -1) if lines[-1][i][0].isspace()), -1
            )
            if last_space > 0 and not char.isspace():
                tail = lines[-1][last_space + 1 :]
                lines[-1] = lines[-1][:last_space]
                lines.append(tail)
                width_used = sum(char_font(size, f).getlength(c) for c, f in tail)
            else:
                lines.append([])
                width_used = 0
        if not lines[-1] and char.isspace():
            continue
        lines[-1].append((char, flags))
        width_used += char_width
    return lines


def fit_image(path: str | None, size: tuple[int, int]) -> Image.Image | None:
    if not path:
        return None
    try:
        with Image.open(path) as source:
            return ImageOps.fit(
                ImageOps.exif_transpose(source).convert("RGBA"),
                size,
                method=Image.Resampling.LANCZOS,
            )
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def ellipsis(value: str, face: ImageFont.FreeTypeFont, width: int) -> str:
    if face.getlength(value) <= width:
        return value
    while value and face.getlength(value + "…") > width:
        value = value[:-1]
    return value + "…"


@lru_cache(maxsize=512)
def emoji_image(char: str, size: int) -> Image.Image | None:
    if ord(char) < 0x2300:
        return None
    path = FONT_DIR / "NotoColorEmoji.ttf"
    if not path.is_file():
        path = Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf")
    if not path.is_file():
        return None
    try:
        face = ImageFont.truetype(str(path), 109)
        tile = Image.new("RGBA", (160, 150))
        ImageDraw.Draw(tile).text((0, 0), char, font=face, embedded_color=True)
        bounds = tile.getbbox()
        if not bounds:
            return None
        tile = tile.crop(bounds)
        tile.thumbnail((size, size), Image.Resampling.LANCZOS)
        return tile
    except (OSError, ValueError):
        return None


def render_quote(quote: Quote, output_path: str) -> str:
    if not 1 <= len(quote.messages) <= 10:
        raise ValueError("В цитате должно быть от 1 до 10 сообщений.")
    if sum(len(m.text) for m in quote.messages) > 10000:
        raise ValueError("Слишком длинная цитата. Сократите подборку.")
    text_width = 390
    planned = None
    for size in (18, 16, 14, 12):
        gap, padding, heading = (8, 10, 19) if size >= 16 else (4, 6, 15)
        rows = []
        for message in quote.messages:
            lines = layout(message, size, text_width)
            media = fit_image(message.media_path, (text_width, 100))
            media_height = 106 if media else (26 if message.media_label else 0)
            height = padding * 2 + heading + len(lines) * (size + 3) + media_height
            rows.append((message, lines, media, height))
        total = 16 + sum(row[3] for row in rows) + gap * (len(rows) - 1)
        if total <= SIZE:
            planned = rows, size, gap, padding, heading, total
            break
    if planned is None:
        raise ValueError(
            "Цитата не помещается разборчиво. Уменьшите число сообщений или длину текста."
        )
    rows, size, gap, padding, heading, height = planned
    image = Image.new("RGBA", (SIZE, max(64, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    y = 8
    for message, lines, media, card_height in rows:
        draw.rounded_rectangle((58, y, 504, y + card_height), radius=12, fill=CARD)
        avatar = fit_image(message.avatar_path, (36, 36))
        if avatar:
            mask = Image.new("L", (36, 36))
            ImageDraw.Draw(mask).ellipse((0, 0, 35, 35), fill=255)
            image.paste(avatar, (10, y + 2), mask)
        else:
            draw.ellipse((10, y + 2, 46, y + 38), fill=(70, 80, 99, 255))
            draw.text(
                (21, y + 9),
                (message.author_name[:1] or "?").upper(),
                font=font(15, "bold"),
                fill=TEXT,
            )
        name_face = font(max(11, size - 2), "bold")
        draw.text(
            (68, y + padding),
            ellipsis(message.author_name, name_face, text_width),
            font=name_face,
            fill=ACCENT,
        )
        ty = y + padding + heading
        for line in lines:
            x = 68.0
            for char, flags in line:
                face = char_font(size, flags)
                width = face.getlength(char)
                emoji = emoji_image(char, size + 2)
                if "spoiler" in flags:
                    draw.rectangle((x, ty + 3, x + width, ty + size + 2), fill=(105, 112, 123, 255))
                elif emoji:
                    image.alpha_composite(emoji, (int(x), ty + 2))
                else:
                    draw.text((x, ty), char, font=face, fill=TEXT)
                if "underline" in flags:
                    draw.line((x, ty + size + 1, x + width, ty + size + 1), fill=TEXT)
                if "strikethrough" in flags:
                    draw.line((x, ty + size // 2 + 3, x + width, ty + size // 2 + 3), fill=TEXT)
                x += width
            ty += size + 3
        if media:
            image.alpha_composite(media, (68, ty + 3))
        elif message.media_label:
            draw.rounded_rectangle(
                (68, ty + 2, 68 + text_width, ty + 24), radius=5, fill=(51, 58, 69, 255)
            )
            draw.text(
                (74, ty + 5),
                ellipsis(message.media_label, font(11), text_width - 12),
                font=font(11),
                fill=TEXT,
            )
        y += card_height + gap
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    for quality in (90, 75, 60, 40):
        image.save(target, format="WEBP", quality=quality, method=6)
        if target.stat().st_size <= 512 * 1024:
            return str(target)
    raise ValueError("Не удалось уложить стикер в ограничение размера файла.")
