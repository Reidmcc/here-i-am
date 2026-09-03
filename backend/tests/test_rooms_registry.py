"""
Rooms registry (issue #323): the phone book for the entity's standing
Claude Code sessions.

Service level: declare / retire / observe and the rendered rooms.md.
Route level: the SessionStart and UserPromptSubmit hooks' snapshots refresh
declared rows and come back as notices; a write failure is a loud
rooms_error, never a 500 and never silence. MCP level: declare_room and
retire_room resolve the session through its Claude Code conversation and
refuse native ones.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import Conversation, ConversationSource, ConversationType
from app.services import claude_code_mcp
from app.services.notes_service import notes_service
from app.services.rooms_registry import (
    GENERATED_MARKER,
    ROOMS_MANUAL_MD,
    RegistryWriteError,
    RoomsRegistry,
    SessionObservation,
    rooms_registry,
)

ENTITY = "Test Entity"
T0 = datetime(2026, 9, 3, 3, 0, tzinfo=timezone.utc)

TEST_ENTITY_INDEXES = (
    '[{"index_name": "test-entity", "label": "Test Entity", '
    '"description": "Test entity", "llm_provider": "anthropic"}]'
)


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    """Point the notes service (and so the registry) at a temp directory."""
    monkeypatch.setattr(settings, "notes_base_dir", str(tmp_path))
    monkeypatch.setattr(settings, "notes_enabled", True)
    monkeypatch.setattr(settings, "claude_code_rooms_registry_enabled", True)
    monkeypatch.setattr(notes_service, "_base_dir", None)
    yield tmp_path / ENTITY
    monkeypatch.setattr(notes_service, "_base_dir", None)


def obs(session_id, **fields):
    return SessionObservation(session_id=session_id, **fields)


# ---------------------------------------------------------------- service


class TestDeclare:
    def test_declare_creates_row_and_renders_both_files(self, notes_dir):
        row, superseded = rooms_registry.declare(
            ENTITY, "sess-A-0000", "conv-A", "Porch",
            note="conversation with Pseudo", ref="a46590",
            observation=obs("sess-A-0000", name="Porch chats", name_source="user"),
            now=T0,
        )
        assert superseded == []
        assert row["room"] == "Porch"
        assert row["conversation_id"] == "conv-A"
        assert row["name"] == "Porch chats"
        assert row["ref"] == "a46590"
        assert row["last_seen"] == "2026-09-03T03:00:00+00:00"

        data = rooms_registry.load(ENTITY)
        assert [r["session_id"] for r in data["rooms"]] == ["sess-A-0000"]
        md = (notes_dir / "rooms.md").read_text(encoding="utf-8")
        assert md.startswith(GENERATED_MARKER)
        assert "| Porch | Porch chats | user | a46590 | sess-A-0 |" in md
        assert "conversation with Pseudo" in md
        assert "_None._" in md  # no retired rows yet

    def test_unobserved_fields_render_as_not_recorded(self, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        md = (notes_dir / "rooms.md").read_text(encoding="utf-8")
        assert "| Porch | — | — | — | sess-A-0 |" in md

    def test_same_room_from_new_session_supersedes_old_row(self, notes_dir):
        rooms_registry.declare(
            ENTITY, "sess-A-0000", "conv-A", "Porch",
            observation=obs("sess-A-0000", name="here-i-am-notes-97"), now=T0,
        )
        row, superseded = rooms_registry.declare(
            ENTITY, "sess-B-0000", "conv-B", "porch", now=T0 + timedelta(hours=1)
        )
        assert [r["session_id"] for r in superseded] == ["sess-A-0000"]
        data = rooms_registry.load(ENTITY)
        old = rooms_registry.find_row(data, "sess-A-0000")
        assert old["retired_at"] == "2026-09-03T04:00:00+00:00"
        assert old["retired_reason"] == "superseded by session sess-B-0"
        assert [r["session_id"] for r in rooms_registry.live_rows(data)] == ["sess-B-0000"]
        md = (notes_dir / "rooms.md").read_text(encoding="utf-8")
        assert "| Porch | here-i-am-notes-97 | sess-A-0 |" in md  # retired table
        assert "superseded by session sess-B-0" in md

    def test_redeclare_updates_own_row_in_place(self, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", note="first", now=T0)
        row, superseded = rooms_registry.declare(
            ENTITY, "sess-A-0000", "conv-A", "The World", now=T0 + timedelta(minutes=5)
        )
        assert superseded == []
        assert row["room"] == "The World"
        assert row["note"] == "first"  # untouched when not passed
        assert len(rooms_registry.load(ENTITY)["rooms"]) == 1

    def test_declare_revives_a_retired_row(self, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        rooms_registry.retire(ENTITY, "sess-A-0000", reason="done", now=T0)
        row, _ = rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        assert row["retired_at"] is None
        assert row["retired_reason"] is None

    def test_declare_requires_room(self, notes_dir):
        with pytest.raises(ValueError):
            rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "  ")


class TestRetire:
    def test_retire_marks_not_removes(self, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        row = rooms_registry.retire(ENTITY, "sess-A-0000", reason="moved rooms", now=T0)
        assert row["retired_reason"] == "moved rooms"
        data = rooms_registry.load(ENTITY)
        assert len(data["rooms"]) == 1
        assert rooms_registry.live_rows(data) == []
        md = (notes_dir / "rooms.md").read_text(encoding="utf-8")
        assert "_No rooms declared yet._" in md
        assert "moved rooms" in md

    def test_retire_unknown_session_returns_none(self, notes_dir):
        assert rooms_registry.retire(ENTITY, "never-declared") is None
        assert not (notes_dir / "rooms.json").exists()


class TestObserve:
    def test_only_declared_rows_are_touched(self, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        outcome = rooms_registry.observe(
            ENTITY, "sess-X-0000",
            [obs("sess-A-0000", name="Porch chats", name_source="user"),
             obs("sess-X-0000", name="workshop-1", name_source="derived")],
            session_start=True, now=T0 + timedelta(minutes=1),
        )
        assert outcome.changed_session_ids == ["sess-A-0000"]
        assert outcome.own_row is None  # the observer never declared
        data = rooms_registry.load(ENTITY)
        assert [r["session_id"] for r in data["rooms"]] == ["sess-A-0000"]
        assert rooms_registry.find_row(data, "sess-A-0000")["name"] == "Porch chats"

    def test_rename_is_reported(self, notes_dir):
        rooms_registry.declare(
            ENTITY, "sess-A-0000", "conv-A", "Porch",
            observation=obs("sess-A-0000", name="here-i-am-notes-97", name_source="derived"),
            now=T0,
        )
        outcome = rooms_registry.observe(
            ENTITY, "sess-A-0000",
            [obs("sess-A-0000", name="Porch chats", name_source="user")],
            session_start=False, now=T0 + timedelta(minutes=1),
        )
        assert outcome.renamed == {"sess-A-0000": ("here-i-am-notes-97", "Porch chats")}
        assert outcome.wrote
        assert rooms_registry.find_row(rooms_registry.load(ENTITY), "sess-A-0000")["name_source"] == "user"

    def test_absent_observation_field_does_not_erase(self, notes_dir):
        rooms_registry.declare(
            ENTITY, "sess-A-0000", "conv-A", "Porch",
            observation=obs("sess-A-0000", name="Porch chats", messaging_socket="uds:x"),
            now=T0,
        )
        outcome = rooms_registry.observe(
            ENTITY, "sess-A-0000", [obs("sess-A-0000", cwd="E:/notes")],
            session_start=False, now=T0 + timedelta(minutes=1),
        )
        assert outcome.renamed == {}
        row = rooms_registry.find_row(rooms_registry.load(ENTITY), "sess-A-0000")
        assert row["name"] == "Porch chats"
        assert row["messaging_socket"] == "uds:x"
        assert row["cwd"] == "E:/notes"

    def test_prompt_time_liveness_is_hourly(self, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        soon = rooms_registry.observe(
            ENTITY, "sess-A-0000", [obs("sess-A-0000")],
            session_start=False, now=T0 + timedelta(minutes=10),
        )
        assert not soon.wrote
        assert soon.own_row["last_seen"] == "2026-09-03T03:00:00+00:00"
        later = rooms_registry.observe(
            ENTITY, "sess-A-0000", [obs("sess-A-0000")],
            session_start=False, now=T0 + timedelta(minutes=61),
        )
        assert later.wrote
        assert later.own_row["last_seen"] == "2026-09-03T04:01:00+00:00"

    def test_session_start_always_bumps_own_last_seen(self, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        outcome = rooms_registry.observe(
            ENTITY, "sess-A-0000", [obs("sess-A-0000")],
            session_start=True, now=T0 + timedelta(minutes=1),
        )
        assert outcome.wrote
        assert outcome.own_row["last_seen"] == "2026-09-03T03:01:00+00:00"

    def test_session_start_does_not_force_sibling_liveness(self, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        rooms_registry.declare(ENTITY, "sess-B-0000", "conv-B", "The World", now=T0)
        outcome = rooms_registry.observe(
            ENTITY, "sess-A-0000", [obs("sess-A-0000"), obs("sess-B-0000")],
            session_start=True, now=T0 + timedelta(minutes=1),
        )
        assert outcome.changed_session_ids == ["sess-A-0000"]

    def test_own_row_refreshed_even_without_snapshot_entry(self, notes_dir):
        # The registry directory may be unreadable: the observing session
        # still reports itself alive from its stdin alone
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        outcome = rooms_registry.observe(
            ENTITY, "sess-A-0000", [obs("sess-A-0000", cwd="E:/x")],
            session_start=True, now=T0 + timedelta(hours=2),
        )
        assert outcome.own_row["last_seen"] == "2026-09-03T05:00:00+00:00"
        assert outcome.own_row["name"] is None

    def test_retired_rows_are_not_refreshed(self, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        rooms_registry.retire(ENTITY, "sess-A-0000", now=T0)
        outcome = rooms_registry.observe(
            ENTITY, "sess-A-0000", [obs("sess-A-0000", name="Porch chats")],
            session_start=True, now=T0 + timedelta(hours=2),
        )
        assert not outcome.wrote
        assert outcome.own_row is None

    def test_empty_registry_observes_nothing(self, notes_dir):
        outcome = rooms_registry.observe(
            ENTITY, "sess-A-0000", [obs("sess-A-0000", name="x")], session_start=True
        )
        assert not outcome.wrote
        assert not (notes_dir / "rooms.json").exists()


class TestFiles:
    def test_hand_written_rooms_md_is_moved_aside_not_overwritten(self, notes_dir):
        notes_dir.mkdir(parents=True)
        manual = "# Rooms registry\n\n| Room | Address |\n| Porch | here-i-am-notes-97 |\n"
        (notes_dir / "rooms.md").write_text(manual, encoding="utf-8")
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        assert (notes_dir / ROOMS_MANUAL_MD).read_text(encoding="utf-8") == manual
        assert (notes_dir / "rooms.md").read_text(encoding="utf-8").startswith(GENERATED_MARKER)

    def test_generated_rooms_md_is_overwritten_in_place(self, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        rooms_registry.declare(ENTITY, "sess-B-0000", "conv-B", "The World", now=T0)
        assert not (notes_dir / ROOMS_MANUAL_MD).exists()
        md = (notes_dir / "rooms.md").read_text(encoding="utf-8")
        assert "| Porch |" in md and "| The World |" in md

    def test_markdown_is_rendered_from_json_never_the_reverse(self, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        md_path = notes_dir / "rooms.md"
        md_path.write_text(
            GENERATED_MARKER + " -->\n| Porch | HAND EDIT |\n", encoding="utf-8"
        )
        rooms_registry.observe(
            ENTITY, "sess-A-0000", [obs("sess-A-0000", name="Porch chats")],
            session_start=True, now=T0 + timedelta(minutes=1),
        )
        assert "HAND EDIT" not in md_path.read_text(encoding="utf-8")

    def test_malformed_json_is_treated_as_empty(self, notes_dir):
        notes_dir.mkdir(parents=True)
        (notes_dir / "rooms.json").write_text("{nope", encoding="utf-8")
        assert rooms_registry.load(ENTITY)["rooms"] == []
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        assert len(rooms_registry.load(ENTITY)["rooms"]) == 1

    def test_pipes_and_newlines_are_escaped_in_cells(self, notes_dir):
        rooms_registry.declare(
            ENTITY, "sess-A-0000", "conv-A", "Porch", note="a|b\nc", now=T0
        )
        md = (notes_dir / "rooms.md").read_text(encoding="utf-8")
        assert "a\\|b c" in md

    def test_write_failure_raises_with_row_and_path(self, notes_dir, monkeypatch):
        def boom(path, text):
            raise PermissionError("read-only notes")

        monkeypatch.setattr("app.services.rooms_registry._atomic_write", boom)
        with pytest.raises(RegistryWriteError) as excinfo:
            rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        assert excinfo.value.path == notes_dir / "rooms.json"
        assert excinfo.value.row["room"] == "Porch"
        assert "PermissionError" in str(excinfo.value)
        assert "sess-A-0000" in RoomsRegistry.describe_row(excinfo.value.row)


# ---------------------------------------------------------------- routes


@pytest.fixture
async def test_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def cc_mode(monkeypatch, notes_dir):
    monkeypatch.setattr(settings, "claude_code_mode_enabled", True)
    monkeypatch.setattr(settings, "pinecone_indexes", TEST_ENTITY_INDEXES)
    from app.services import llm_service

    monkeypatch.setattr(llm_service, "count_tokens", lambda text, model=None: len(text) // 4)


@pytest.fixture
async def async_client(test_engine, cc_mode):
    async_session = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


SNAPSHOT_A = {
    "session_id": "sess-A-0000",
    "name": "Porch chats",
    "name_source": "user",
    "name_since": "2026-09-03T02:53:58+00:00",
    "messaging_socket": "\\\\.\\pipe\\LOCAL\\cc-msg-2db4",
    "cwd": "E:\\here-i-am-notes",
    "started_at": "2026-09-03T00:24:42+00:00",
}


class TestRoutes:
    async def test_session_start_refreshes_declared_row_and_notices(self, async_client, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        response = await async_client.post(
            "/api/claude-code/session-start",
            json={
                "session_id": "sess-A-0000",
                "source": "resume",
                "cwd": "E:\\here-i-am-notes",
                "transcript_path": "C:\\t\\sess-A.jsonl",
                "sessions": [SNAPSHOT_A, {"session_id": "sess-X-0000", "name": "workshop"}],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["rooms_error"] == ""
        assert "registered as the Porch" in body["rooms_notice"]
        assert 'roster name now "Porch chats" (user)' in body["rooms_notice"]
        row = rooms_registry.find_row(rooms_registry.load(ENTITY), "sess-A-0000")
        assert row["name"] == "Porch chats"
        assert row["messaging_socket"] == SNAPSHOT_A["messaging_socket"]
        assert row["transcript_path"] == "C:\\t\\sess-A.jsonl"
        assert row["last_seen"] > "2026-09-03T03:00:00"
        assert len(rooms_registry.load(ENTITY)["rooms"]) == 1  # no row for sess-X

    async def test_session_start_without_row_stays_quiet(self, async_client, notes_dir):
        response = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": "sess-A-0000", "sessions": [SNAPSHOT_A]},
        )
        assert response.status_code == 200
        assert response.json()["rooms_notice"] == ""
        assert response.json()["rooms_error"] == ""
        assert not (notes_dir / "rooms.json").exists()

    async def test_session_start_notice_names_unobserved_roster_name(self, async_client, notes_dir):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        response = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": "sess-A-0000", "sessions": []},
        )
        assert "roster name not observed" in response.json()["rooms_notice"]

    async def test_retrieve_reports_own_and_sibling_renames(self, async_client, notes_dir):
        rooms_registry.declare(
            ENTITY, "sess-A-0000", "conv-A", "Porch",
            observation=obs("sess-A-0000", name="here-i-am-notes-97"), now=T0,
        )
        rooms_registry.declare(
            ENTITY, "sess-B-0000", "conv-B", "The World",
            observation=obs("sess-B-0000", name="here-i-am-notes-a1"), now=T0,
        )
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={
                "session_id": "sess-A-0000",
                "prompt": "hello",
                "sessions": [
                    SNAPSHOT_A,
                    {"session_id": "sess-B-0000", "name": "The World explorations", "name_source": "user"},
                ],
            },
        )
        assert response.status_code == 200
        notice = response.json()["rooms_notice"]
        assert 'this session: now "Porch chats" (was "here-i-am-notes-97")' in notice
        assert 'The World: now "The World explorations" (was "here-i-am-notes-a1")' in notice

    async def test_retrieve_without_rename_has_no_notice(self, async_client, notes_dir):
        rooms_registry.declare(
            ENTITY, "sess-A-0000", "conv-A", "Porch",
            observation=obs("sess-A-0000", name="Porch chats"), now=T0,
        )
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={"session_id": "sess-A-0000", "prompt": "hello", "sessions": [SNAPSHOT_A]},
        )
        assert response.json()["rooms_notice"] == ""

    async def test_wakeup_tick_path_still_observes(self, async_client, notes_dir):
        rooms_registry.declare(
            ENTITY, "sess-A-0000", "conv-A", "Engagement room",
            observation=obs("sess-A-0000", name="here-i-am-notes-9b"), now=T0,
        )
        response = await async_client.post(
            "/api/claude-code/retrieve",
            json={
                "session_id": "sess-A-0000",
                "prompt": "",
                "sessions": [{"session_id": "sess-A-0000", "name": "Substack engagements", "name_source": "user"}],
            },
        )
        assert response.status_code == 200
        assert 'now "Substack engagements"' in response.json()["rooms_notice"]

    async def test_write_failure_is_loud_not_fatal(self, async_client, notes_dir, monkeypatch):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)

        def boom(path, text):
            raise PermissionError("read-only notes")

        monkeypatch.setattr("app.services.rooms_registry._atomic_write", boom)
        response = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": "sess-A-0000", "sessions": [SNAPSHOT_A]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["context"]  # the identity block still arrives
        assert body["rooms_notice"] == ""
        assert "could not be written" in body["rooms_error"]
        assert "rooms.json" in body["rooms_error"]
        assert "room=Porch" in body["rooms_error"]
        assert "sess-A-0000" in body["rooms_error"]

    async def test_registry_flag_off_writes_nothing(self, async_client, notes_dir, monkeypatch):
        rooms_registry.declare(ENTITY, "sess-A-0000", "conv-A", "Porch", now=T0)
        monkeypatch.setattr(settings, "claude_code_rooms_registry_enabled", False)
        before = (notes_dir / "rooms.json").read_text(encoding="utf-8")
        response = await async_client.post(
            "/api/claude-code/session-start",
            json={"session_id": "sess-A-0000", "sessions": [SNAPSHOT_A]},
        )
        assert response.json()["rooms_notice"] == ""
        assert (notes_dir / "rooms.json").read_text(encoding="utf-8") == before
        assert "[ROOMS REGISTRY]" not in response.json()["context"]

    async def test_fresh_session_context_mentions_registry(self, async_client, notes_dir):
        response = await async_client.post(
            "/api/claude-code/session-start", json={"session_id": "sess-N-0000"}
        )
        assert "[ROOMS REGISTRY]" in response.json()["context"]
        assert "declare_room" in response.json()["context"]


# ---------------------------------------------------------------- MCP


@pytest.fixture
async def mcp_db(test_engine, cc_mode, monkeypatch):
    async_session = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(claude_code_mcp, "async_session_maker", async_session)
    yield async_session


async def make_conversation(sessionmaker, *, source, session_id=None):
    conversation = Conversation(
        title="t",
        conversation_type=ConversationType.NORMAL,
        llm_model_used="claude-code",
        entity_id="test-entity",
        source=source,
        external_session_id=session_id,
    )
    async with sessionmaker() as db:
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
    return conversation


async def call_tool(name, arguments):
    response = await claude_code_mcp.handle_jsonrpc_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}}
    )
    return response["result"]["content"][0]["text"], response["result"]["isError"]


class TestMcpRoomTools:
    async def test_tools_list_includes_room_tools(self, mcp_db):
        response = await claude_code_mcp.handle_jsonrpc_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        names = [t["name"] for t in response["result"]["tools"]]
        assert "declare_room" in names and "retire_room" in names
        declare = next(t for t in response["result"]["tools"] if t["name"] == "declare_room")
        assert set(declare["inputSchema"]["required"]) == {"room", "conversation_id"}

    async def test_declare_room_writes_row_for_the_session(self, mcp_db, notes_dir):
        conversation = await make_conversation(
            mcp_db, source=ConversationSource.CLAUDE_CODE.value, session_id="sess-A-0000"
        )
        text, is_error = await call_tool(
            "declare_room",
            {"conversation_id": conversation.id, "room": "Porch", "note": "with Pseudo", "ref": "a46590"},
        )
        assert not is_error, text
        assert "Declared this session as the Porch" in text
        assert "not yet observed this session's roster name" in text
        assert str(notes_dir / "rooms.md") in text
        row = rooms_registry.find_row(rooms_registry.load(ENTITY), "sess-A-0000")
        assert row["conversation_id"] == conversation.id
        assert row["ref"] == "a46590"
        assert row["note"] == "with Pseudo"

    async def test_declare_room_reports_superseded_row(self, mcp_db, notes_dir):
        rooms_registry.declare(
            ENTITY, "sess-OLD-00", "conv-old", "Porch",
            observation=obs("sess-OLD-00", name="here-i-am-notes-97"), now=T0,
        )
        conversation = await make_conversation(
            mcp_db, source=ConversationSource.CLAUDE_CODE.value, session_id="sess-A-0000"
        )
        text, is_error = await call_tool(
            "declare_room", {"conversation_id": conversation.id, "room": "Porch"}
        )
        assert not is_error
        assert "Superseded the previous Porch row (session sess-OLD" in text
        assert '"here-i-am-notes-97"' in text

    async def test_declare_room_refuses_native_conversation(self, mcp_db, notes_dir):
        conversation = await make_conversation(mcp_db, source=ConversationSource.NATIVE.value)
        text, is_error = await call_tool(
            "declare_room", {"conversation_id": conversation.id, "room": "Porch"}
        )
        assert is_error
        assert "native Here I Am experience" in text
        assert not (notes_dir / "rooms.json").exists()

    async def test_declare_room_requires_conversation_id_and_room(self, mcp_db, notes_dir):
        text, is_error = await call_tool("declare_room", {"room": "Porch"})
        assert is_error and "conversation_id is required" in text
        conversation = await make_conversation(
            mcp_db, source=ConversationSource.CLAUDE_CODE.value, session_id="sess-A-0000"
        )
        text, is_error = await call_tool("declare_room", {"conversation_id": conversation.id})
        assert is_error and "room is required" in text

    async def test_unknown_conversation_points_at_lazy_registration(self, mcp_db, notes_dir):
        text, is_error = await call_tool(
            "declare_room", {"conversation_id": "nope", "room": "Porch"}
        )
        assert is_error
        assert "first recorded prompt" in text

    async def test_retire_room_via_mcp(self, mcp_db, notes_dir):
        conversation = await make_conversation(
            mcp_db, source=ConversationSource.CLAUDE_CODE.value, session_id="sess-A-0000"
        )
        await call_tool("declare_room", {"conversation_id": conversation.id, "room": "Porch"})
        text, is_error = await call_tool(
            "retire_room", {"conversation_id": conversation.id, "reason": "moving"}
        )
        assert not is_error
        assert "Retired this session's row (Porch; reason: moving)" in text
        assert rooms_registry.live_rows(rooms_registry.load(ENTITY)) == []

    async def test_retire_room_without_row(self, mcp_db, notes_dir):
        conversation = await make_conversation(
            mcp_db, source=ConversationSource.CLAUDE_CODE.value, session_id="sess-A-0000"
        )
        text, is_error = await call_tool("retire_room", {"conversation_id": conversation.id})
        assert not is_error
        assert "no rooms-registry row to retire" in text

    async def test_write_failure_returns_error_with_row(self, mcp_db, notes_dir, monkeypatch):
        conversation = await make_conversation(
            mcp_db, source=ConversationSource.CLAUDE_CODE.value, session_id="sess-A-0000"
        )

        def boom(path, text):
            raise PermissionError("read-only notes")

        monkeypatch.setattr("app.services.rooms_registry._atomic_write", boom)
        text, is_error = await call_tool(
            "declare_room", {"conversation_id": conversation.id, "room": "Porch"}
        )
        assert is_error
        assert "could not be written" in text
        assert "room=Porch" in text

    async def test_registry_disabled(self, mcp_db, notes_dir, monkeypatch):
        monkeypatch.setattr(settings, "claude_code_rooms_registry_enabled", False)
        text, is_error = await call_tool("declare_room", {"conversation_id": "x", "room": "Porch"})
        assert is_error and "disabled" in text
