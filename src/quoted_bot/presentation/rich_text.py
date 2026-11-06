"""Convert Telegram RichMessage text into the same text/entities as classic messages."""

from aiogram.types import RichMessage


def extract_rich_text(message: RichMessage) -> tuple[str, list[dict]]:
    parts: list[str] = []
    entities: list[dict] = []
    position = 0

    def append(text: str) -> None:
        nonlocal position
        parts.append(text)
        position += len(text.encode("utf-16-le")) // 2

    def inline(value) -> None:
        if isinstance(value, str):
            append(value)
        elif isinstance(value, list):
            for child in value:
                inline(child)
        elif isinstance(value, dict):
            start = position
            kind = value.get("type")
            if kind == "custom_emoji":
                append(value.get("alternative_text", ""))
            elif kind == "mathematical_expression":
                append(value.get("expression", ""))
            elif kind == "button":
                inline(value.get("button", {}).get("text", ""))
            else:
                inline(value.get("text", ""))
            if (
                kind in {"bold", "italic", "underline", "strikethrough", "code", "spoiler"}
                and position > start
            ):
                entities.append({"type": kind, "offset": start, "length": position - start})

    def blocks(values: list[dict]) -> None:
        for value in values:
            before = position
            inline(value.get("text", ""))
            if value.get("expression"):
                append(value["expression"])
            for key in ("title", "summary", "caption", "credit"):
                if value.get(key):
                    inline(value[key])
            if value.get("blocks"):
                blocks(value["blocks"])
            for item in value.get("items", []):
                blocks(item.get("blocks", []))
            if position > before and (not parts or not parts[-1].endswith("\n")):
                append("\n")

    blocks(message.model_dump(mode="json", exclude_none=True)["blocks"])
    # Remove only the separator introduced after the last block, not original whitespace.
    if parts and parts[-1] == "\n":
        parts.pop()
    return "".join(parts), entities
