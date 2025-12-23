import re

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


async def _migrate_messages_role_enum(conn):
    """
    Migrate the messages.role enum to support TOOL_USE and TOOL_RESULT values.

    SQLite stores enums as VARCHAR with a CHECK constraint. To add new enum values,
    we need to recreate the table without the constraint (or with an updated constraint).

    This migration:
    1. Checks if the role column has a CHECK constraint that blocks new values
    2. If so, recreates the table with the updated enum values
    """
    # Check the table's SQL definition to see if there's a CHECK constraint
    result = await conn.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'"
    ))
    row = result.fetchone()
    if not row:
        print("Migration check: messages table not found (will be created)")
        return

    table_sql = row[0]
    print(f"Migration check: messages table schema: {table_sql[:200]}...")

    # Check if the CHECK constraint exists and doesn't include the new values
    # The constraint looks like: CHECK (role IN ('human', 'assistant', 'system'))
    has_check = 'CHECK' in table_sql.upper()
    has_tool_use = 'tool_use' in table_sql.lower()

    # Also check if role column is too narrow (VARCHAR(9) can't fit 'tool_result' which is 11 chars)
    # Look for patterns like "role VARCHAR(9)" or "role VARCHAR(10)"
    role_varchar_match = re.search(r'role\s+VARCHAR\((\d+)\)', table_sql, re.IGNORECASE)
    role_too_narrow = False
    if role_varchar_match:
        varchar_size = int(role_varchar_match.group(1))
        role_too_narrow = varchar_size < 11  # 'tool_result' is 11 chars
        print(f"Migration check: role column VARCHAR size: {varchar_size}, too narrow: {role_too_narrow}")

    print(f"Migration check: has CHECK constraint: {has_check}, has tool_use: {has_tool_use}")

    needs_migration = (has_check and not has_tool_use) or role_too_narrow

    if needs_migration:
        reason = "CHECK constraint" if (has_check and not has_tool_use) else "VARCHAR too narrow"
        print(f"Migrating: Updating messages table for tool_use/tool_result support (reason: {reason})...")

        # Check which columns exist in the old table
        result = await conn.execute(text("PRAGMA table_info(messages)"))
        existing_columns = [row[1] for row in result.fetchall()]
        has_speaker_entity_id = 'speaker_entity_id' in existing_columns

        # SQLite migration: recreate table with updated schema
        # Step 0: Clean up any leftover from failed migration
        await conn.execute(text("DROP TABLE IF EXISTS messages_new"))

        # Step 1: Create new table with wider role column (no CHECK constraint)
        # Multi-entity conversations can have speaker labels as roles (e.g., "Claude", "GPT")
        await conn.execute(text("""
            CREATE TABLE messages_new (
                id VARCHAR(36) PRIMARY KEY,
                conversation_id VARCHAR(36) NOT NULL REFERENCES conversations(id),
                role VARCHAR(50) NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                token_count INTEGER,
                times_retrieved INTEGER DEFAULT 0,
                last_retrieved_at DATETIME,
                speaker_entity_id VARCHAR(100)
            )
        """))

        # Step 2: Copy data from old table (handle missing speaker_entity_id column)
        if has_speaker_entity_id:
            await conn.execute(text("""
                INSERT INTO messages_new
                SELECT id, conversation_id, role, content, created_at, token_count,
                       times_retrieved, last_retrieved_at, speaker_entity_id
                FROM messages
            """))
        else:
            await conn.execute(text("""
                INSERT INTO messages_new (id, conversation_id, role, content, created_at,
                                         token_count, times_retrieved, last_retrieved_at)
                SELECT id, conversation_id, role, content, created_at, token_count,
                       times_retrieved, last_retrieved_at
                FROM messages
            """))

        # Step 3: Drop old table
        await conn.execute(text("DROP TABLE messages"))

        # Step 4: Rename new table
        await conn.execute(text("ALTER TABLE messages_new RENAME TO messages"))

        # Step 5: Recreate indexes if any existed
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON messages(conversation_id)"
        ))

        print("  ✓ Updated messages table to support tool_use and tool_result roles")


async def run_migrations(conn):
    """Run schema migrations for new columns that SQLAlchemy create_all doesn't handle."""
    # Check if messages table exists before trying to migrate
    result = await conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    ))
    if not result.fetchone():
        # Messages table doesn't exist yet, will be created by create_all
        return

    # Migrate messages.role enum to support new TOOL_USE and TOOL_RESULT values
    # SQLite stores enums as VARCHAR with CHECK constraints, so we need to recreate
    # the table to add new enum values
    await _migrate_messages_role_enum(conn)

    # Check if speaker_entity_id column exists in messages table
    result = await conn.execute(text("PRAGMA table_info(messages)"))
    columns = [row[1] for row in result.fetchall()]

    if 'speaker_entity_id' not in columns:
        print("Migrating: Adding 'speaker_entity_id' column to messages table...")
        await conn.execute(text(
            "ALTER TABLE messages ADD COLUMN speaker_entity_id VARCHAR(100)"
        ))
        print("  ✓ Added speaker_entity_id column")

    # Check if entity_id column exists in conversation_memory_links table
    result = await conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_memory_links'"
    ))
    if result.fetchone():
        result = await conn.execute(text("PRAGMA table_info(conversation_memory_links)"))
        columns = [row[1] for row in result.fetchall()]

        if 'entity_id' not in columns:
            print("Migrating: Adding 'entity_id' column to conversation_memory_links table...")
            await conn.execute(text(
                "ALTER TABLE conversation_memory_links ADD COLUMN entity_id VARCHAR(100)"
            ))
            print("  ✓ Added entity_id column for multi-entity memory isolation")

    # Check if entity_system_prompts column exists in conversations table
    result = await conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
    ))
    if result.fetchone():
        result = await conn.execute(text("PRAGMA table_info(conversations)"))
        columns = [row[1] for row in result.fetchall()]

        if 'entity_system_prompts' not in columns:
            print("Migrating: Adding 'entity_system_prompts' column to conversations table...")
            await conn.execute(text(
                "ALTER TABLE conversations ADD COLUMN entity_system_prompts JSON"
            ))
            print("  ✓ Added entity_system_prompts column for per-entity system prompts")


async def init_db():
    async with engine.begin() as conn:
        # First run migrations on existing tables
        await run_migrations(conn)
        # Then create any new tables
        await conn.run_sync(Base.metadata.create_all)
