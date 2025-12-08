from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.here_i_am_database_url,
    echo=False,  # SQL logging disabled; use logging config if needed
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def get_db():
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def run_migrations(conn):
    """Run schema migrations for new columns that SQLAlchemy create_all doesn't handle."""
    # Check if messages table exists before trying to migrate
    result = await conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    ))
    if not result.fetchone():
        # Messages table doesn't exist yet, will be created by create_all
        return

    # Check if speaker_entity_id column exists in messages table
    result = await conn.execute(text("PRAGMA table_info(messages)"))
    columns = [row[1] for row in result.fetchall()]

    if 'speaker_entity_id' not in columns:
        print("Migrating: Adding 'speaker_entity_id' column to messages table...")
        await conn.execute(text(
            "ALTER TABLE messages ADD COLUMN speaker_entity_id VARCHAR(100)"
        ))
        print("  ✓ Added speaker_entity_id column")


async def init_db():
    async with engine.begin() as conn:
        # First run migrations on existing tables
        await run_migrations(conn)
        # Then create any new tables
        await conn.run_sync(Base.metadata.create_all)
