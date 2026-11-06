"""SQLAlchemy persistence; works with sqlite+aiosqlite and postgresql+asyncpg."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Integer, String, delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from quoted_bot.domain.models import QuoteMessage


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class MessageRow(Base):
    __tablename__ = "messages"
    chat_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class SelectionRow(Base):
    __tablename__ = "selections"
    chat_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    messages: Mapped[list] = mapped_column(JSON, default=list)
    collecting: Mapped[bool] = mapped_column(default=False)


class JobRow(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    destination: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), index=True, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Database:
    def __init__(self, url: str):
        if not url.startswith(("sqlite+aiosqlite:", "postgresql+asyncpg:")):
            raise ValueError("DATABASE_URL must use sqlite+aiosqlite or postgresql+asyncpg")
        self.engine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        # Single worker deployment: replay rendering, never blindly replay delivery.
        async with self.sessions.begin() as session:
            await session.execute(
                update(JobRow).where(JobRow.state == "rendering").values(state="pending")
            )
            await session.execute(
                update(JobRow)
                .where(JobRow.state == "delivering")
                .values(
                    state="uncertain",
                    error="Process stopped during delivery; manual retry required",
                )
            )

    async def ingest(self, message: QuoteMessage) -> None:
        async with self.sessions.begin() as session:
            await session.merge(
                MessageRow(
                    chat_id=str(message.chat_id),
                    message_id=message.message_id,
                    received_at=now(),
                    payload=message.to_dict(),
                )
            )
            stale = (
                select(MessageRow.message_id)
                .where(MessageRow.chat_id == str(message.chat_id))
                .order_by(MessageRow.message_id.desc())
                .offset(1000)
            )
            await session.execute(
                delete(MessageRow).where(
                    MessageRow.chat_id == str(message.chat_id), MessageRow.message_id.in_(stale)
                )
            )

    async def sequence(self, chat_id: int, message_id: int, following: int) -> list[QuoteMessage]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(MessageRow)
                    .where(
                        MessageRow.chat_id == str(chat_id),
                        MessageRow.message_id >= message_id,
                        MessageRow.received_at >= now() - timedelta(hours=24),
                    )
                    .order_by(MessageRow.message_id)
                    .limit(following + 1)
                )
            ).all()
            if not rows or rows[0].message_id != message_id:
                return []
            return [QuoteMessage.from_dict(row.payload) for row in rows]

    async def _selection_row(self, session, chat_id: int, user_id: int) -> SelectionRow:
        row = await session.get(SelectionRow, (str(chat_id), str(user_id)))
        if row is None:
            row = SelectionRow(
                chat_id=str(chat_id),
                user_id=str(user_id),
                messages=[],
                collecting=False,
                updated_at=now(),
            )
            session.add(row)
        elif (now() - row.updated_at.replace(tzinfo=UTC)) > timedelta(hours=1):
            row.messages, row.collecting = [], False
        return row

    async def selection(self, chat_id: int, user_id: int) -> list[QuoteMessage]:
        async with self.sessions.begin() as session:
            row = await self._selection_row(session, chat_id, user_id)
            return [QuoteMessage.from_dict(m) for m in row.messages]

    async def save_selection(
        self, chat_id: int, user_id: int, messages: list[QuoteMessage]
    ) -> None:
        async with self.sessions.begin() as session:
            row = await self._selection_row(session, chat_id, user_id)
            row.messages, row.updated_at = [m.to_dict() for m in messages], now()

    async def set_collecting(self, chat_id: int, user_id: int, enabled: bool) -> None:
        async with self.sessions.begin() as session:
            row = await self._selection_row(session, chat_id, user_id)
            row.collecting, row.updated_at = enabled, now()

    async def is_collecting(self, chat_id: int, user_id: int) -> bool:
        async with self.sessions.begin() as session:
            return (await self._selection_row(session, chat_id, user_id)).collecting

    async def enqueue(self, payload: dict, destination: dict, dedupe_key: str) -> str:
        async with self.sessions.begin() as session:
            existing = await session.scalar(select(JobRow).where(JobRow.dedupe_key == dedupe_key))
            if existing:
                return existing.id
            row = JobRow(
                id=str(uuid4()),
                dedupe_key=dedupe_key,
                payload=payload,
                destination=destination,
                created_at=now(),
                updated_at=now(),
                state="pending",
            )
            session.add(row)
            return row.id

    async def claim(self) -> JobRow | None:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(JobRow)
                .where(JobRow.state == "pending")
                .order_by(JobRow.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row:
                row.state, row.updated_at = "rendering", now()
            return row

    async def finish(self, job_id: str, state: str, error: str | None = None) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(JobRow)
                .where(JobRow.id == job_id)
                .values(state=state, updated_at=now(), error=error[:500] if error else None)
            )

    async def cleanup(self, before: datetime) -> None:
        async with self.sessions.begin() as session:
            await session.execute(delete(MessageRow).where(MessageRow.received_at < before))
            await session.execute(
                delete(SelectionRow).where(SelectionRow.updated_at < now() - timedelta(hours=1))
            )
            await session.execute(
                delete(JobRow).where(
                    JobRow.updated_at < before, JobRow.state.in_(["done", "failed", "uncertain"])
                )
            )
