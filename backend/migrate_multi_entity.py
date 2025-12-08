#!/usr/bin/env python3
"""
Migration script to add multi-entity conversation support.

Adds:
- speaker_entity_id column to messages table
- conversation_entities table (if not exists)

Run from the backend directory:
    python migrate_multi_entity.py
"""
import asyncio
import sys
import os

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import settings


async def migrate():
    """Run the migration."""
    print(f"Connecting to database: {settings.here_i_am_database_url}")

    engine = create_async_engine(settings.here_i_am_database_url)

    async with engine.begin() as conn:
        # Check if messages table exists
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        ))
        if not result.fetchone():
            print("ERROR: 'messages' table not found. Please ensure the application has been run at least once to create the database schema.")
            return False

        # Check if speaker_entity_id column exists
        result = await conn.execute(text("PRAGMA table_info(messages)"))
        columns = [row[1] for row in result.fetchall()]

        if 'speaker_entity_id' not in columns:
            print("Adding 'speaker_entity_id' column to messages table...")
            await conn.execute(text(
                "ALTER TABLE messages ADD COLUMN speaker_entity_id VARCHAR(100)"
            ))
            print("  ✓ Added speaker_entity_id column")
        else:
            print("  ✓ speaker_entity_id column already exists")

        # Check if conversation_entities table exists
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_entities'"
        ))
        if not result.fetchone():
            print("Creating 'conversation_entities' table...")
            await conn.execute(text("""
                CREATE TABLE conversation_entities (
                    id VARCHAR(36) PRIMARY KEY,
                    conversation_id VARCHAR(36) NOT NULL,
                    entity_id VARCHAR(100) NOT NULL,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    display_order INTEGER DEFAULT 0,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                )
            """))
            print("  ✓ Created conversation_entities table")
        else:
            print("  ✓ conversation_entities table already exists")

        print("\nMigration complete!")
        return True

    await engine.dispose()


if __name__ == "__main__":
    success = asyncio.run(migrate())
    sys.exit(0 if success else 1)
