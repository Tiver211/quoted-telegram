from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class QuoteMessage:
    message_id: int
    chat_id: int
    sent_at: datetime
    author_name: str
    text: str
    author_id: int | None = None
    avatar_path: str | None = None
    media_path: str | None = None
    media_label: str | None = None
    entities: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        result = asdict(self)
        result["sent_at"] = self.sent_at.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "QuoteMessage":
        return cls(**{**data, "sent_at": datetime.fromisoformat(data["sent_at"])})


@dataclass
class Quote:
    messages: list[QuoteMessage]

    def __post_init__(self) -> None:
        if not 1 <= len(self.messages) <= 10:
            raise ValueError("В цитате должно быть от 1 до 10 сообщений.")
