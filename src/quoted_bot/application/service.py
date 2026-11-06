from quoted_bot.domain.models import Quote, QuoteMessage
from quoted_bot.domain.ports import Repository


class Application:
    def __init__(self, repository: Repository):
        self.repository = repository

    async def ingest(self, message: QuoteMessage) -> None:
        await self.repository.ingest(message)

    async def enqueue(
        self, messages: list[QuoteMessage], destination: dict, dedupe_key: str
    ) -> str:
        Quote(messages)
        return await self.repository.enqueue(
            {"messages": [m.to_dict() for m in messages]}, destination, dedupe_key
        )

    async def select_sequence(
        self, chat_id: int, message_id: int, following: int
    ) -> list[QuoteMessage]:
        if not 0 <= following <= 9:
            raise ValueError("Укажите от 0 до 9 следующих сообщений.")
        messages = await self.repository.sequence(chat_id, message_id, following)
        if len(messages) != following + 1:
            raise ValueError("Нужные сообщения отсутствуют в буфере. Выберите меньше сообщений.")
        return messages

    async def add_selection(self, chat_id: int, user_id: int, message: QuoteMessage) -> int:
        messages = await self.repository.selection(chat_id, user_id)
        if not any(
            m.message_id == message.message_id and m.chat_id == message.chat_id for m in messages
        ):
            messages.append(message)
        Quote(messages)
        messages.sort(key=lambda m: (m.sent_at, m.message_id))
        await self.repository.save_selection(chat_id, user_id, messages)
        return len(messages)

    async def get_selection(self, chat_id: int, user_id: int) -> list[QuoteMessage]:
        return await self.repository.selection(chat_id, user_id)

    async def clear_selection(self, chat_id: int, user_id: int) -> None:
        await self.repository.save_selection(chat_id, user_id, [])
        await self.repository.set_collecting(chat_id, user_id, False)

    async def set_collecting(self, chat_id: int, user_id: int, enabled: bool) -> None:
        await self.repository.set_collecting(chat_id, user_id, enabled)

    async def is_collecting(self, chat_id: int, user_id: int) -> bool:
        return await self.repository.is_collecting(chat_id, user_id)
