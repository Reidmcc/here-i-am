"""
An entity token is bound to one entity (issue #396): it acts as that
entity and no other, on the hook endpoints and through the memory tools.

Before tokens, `conversation_id` was the only thing that said whose memory
a call may touch, and `HIM_ENTITY` the only thing that said who a session
was. Both were whatever the caller sent. These tests are that convention
turned into a check, with two entities on one server.
"""
import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth import store
from app.config import settings
from app.models import Conversation, Message, MessageRole
from app.services import claude_code_mcp

pytestmark = pytest.mark.real_auth

# Ada is first, so she is the default entity; the tests act as Bea to show
# the token decides and not the default
TWO_ENTITIES = (
    '[{"index_name": "ada-index", "label": "Ada", "llm_provider": "anthropic"},'
    ' {"index_name": "bea-index", "label": "Bea", "llm_provider": "anthropic"}]'
)


@pytest.fixture(autouse=True)
def two_entities(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "claude_code_mode_enabled", True)
    monkeypatch.setattr(settings, "pinecone_indexes", TWO_ENTITIES)
    # The rooms registry writes into the entity's notes; a scratch tree
    monkeypatch.setattr(settings, "notes_enabled", True)
    monkeypatch.setattr(settings, "notes_base_dir", str(tmp_path))
    monkeypatch.setattr(settings, "claude_code_rooms_registry_enabled", True)
    from app.services import llm_service

    monkeypatch.setattr(llm_service, "count_tokens", lambda text, model=None: len(text) // 4)


@pytest.fixture
async def client(auth_app):
    app, _maker = auth_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def maker(auth_app):
    return auth_app[1]


async def token_for(maker, name, scope="entity", entity_id=None):
    async with maker() as db:
        value, _row = await store.create_token(db, name, scope, entity_id)
    return {"Authorization": f"Bearer {value}"}


@pytest.fixture
async def ada(maker):
    return await token_for(maker, "ada-machine", entity_id="ada-index")


@pytest.fixture
async def bea(maker):
    return await token_for(maker, "bea-machine", entity_id="bea-index")


@pytest.fixture
async def researcher(maker):
    return await token_for(maker, "script", scope="researcher")


async def counts(maker):
    async with maker() as db:
        conversations = (await db.execute(select(func.count(Conversation.id)))).scalar()
        messages = (await db.execute(select(func.count(Message.id)))).scalar()
    return conversations, messages


async def record_prompt(client, headers, session_id, **extra):
    return await client.post(
        "/api/claude-code/retrieve",
        json={"session_id": session_id, "prompt": "hello there", **extra},
        headers=headers,
    )


async def conversation_of(maker, session_id):
    async with maker() as db:
        result = await db.execute(
            select(Conversation).where(Conversation.external_session_id == session_id)
        )
        return result.scalar_one_or_none()


def rpc(name, arguments):
    return {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


class TestTheTokenSaysWhoYouAre:
    async def test_with_no_entity_named_the_tokens_entity_is_used(self, client, maker, bea):
        session_id = str(uuid.uuid4())
        started = await client.post(
            "/api/claude-code/session-start", json={"session_id": session_id}, headers=bea
        )
        # Not Ada, though she is the server's default entity
        assert started.json()["entity_id"] == "bea-index"
        assert (await record_prompt(client, bea, session_id)).status_code == 200
        assert (await conversation_of(maker, session_id)).entity_id == "bea-index"

    async def test_naming_its_own_entity_is_fine(self, client, maker, bea):
        session_id = str(uuid.uuid4())
        for name in ("Bea", "bea-index", " bea "):
            assert (await record_prompt(client, bea, session_id, entity=name)).status_code == 200
        assert (await conversation_of(maker, session_id)).entity_id == "bea-index"

    @pytest.mark.parametrize("path,payload", [
        ("session-start", {}),
        ("retrieve", {"prompt": "hello"}),
        ("log-assistant", {"content": "hello", "message_uuid": str(uuid.uuid4())}),
        ("session-end", {}),
        ("compact-talk", {"pre_compaction": "saved", "reflection": "a thought"}),
    ])
    async def test_naming_another_entity_is_refused_and_writes_nothing(
        self, client, maker, bea, path, payload
    ):
        before = await counts(maker)
        response = await client.post(
            f"/api/claude-code/{path}",
            json={"session_id": str(uuid.uuid4()), "entity": "Ada", **payload},
            headers=bea,
        )
        assert response.status_code == 403
        assert "bound to another entity" in response.json()["detail"]
        assert await counts(maker) == before

    async def test_an_unknown_entity_is_still_a_400(self, client, bea):
        response = await record_prompt(client, bea, str(uuid.uuid4()), entity="Nobody")
        assert response.status_code == 400

    async def test_a_token_whose_entity_was_removed_is_refused(self, client, maker, bea, monkeypatch):
        monkeypatch.setattr(
            settings, "pinecone_indexes",
            '[{"index_name": "ada-index", "label": "Ada", "llm_provider": "anthropic"}]',
        )
        before = await counts(maker)
        response = await record_prompt(client, bea, str(uuid.uuid4()))
        assert response.status_code == 403
        assert "no longer configured" in response.json()["detail"]
        assert await counts(maker) == before

    async def test_a_researcher_credential_is_bound_to_nobody(self, client, maker, researcher):
        for entity, index in (("Ada", "ada-index"), ("Bea", "bea-index")):
            session_id = str(uuid.uuid4())
            assert (await record_prompt(client, researcher, session_id, entity=entity)).status_code == 200
            assert (await conversation_of(maker, session_id)).entity_id == index


class TestAnotherEntitysSession:
    @pytest.fixture
    async def adas_session(self, client, ada):
        session_id = str(uuid.uuid4())
        assert (await record_prompt(client, ada, session_id)).status_code == 200
        return session_id

    @pytest.mark.parametrize("path,payload", [
        ("session-start", {}),
        ("session-start", {"source": "compact"}),
        ("retrieve", {"prompt": "words put in her mouth"}),
        ("log-assistant", {"content": "words put in her mouth", "message_uuid": str(uuid.uuid4())}),
        ("recorded", {"message_ids": [str(uuid.uuid4())]}),
        ("session-end", {}),
        ("compact-talk", {"pre_compaction": "saved", "reflection": "not hers"}),
        ("compact-talk", {}),
    ])
    async def test_every_hook_endpoint_refuses_it_and_writes_nothing(
        self, client, maker, bea, adas_session, path, payload
    ):
        before = await counts(maker)
        stamp_before = (await conversation_of(maker, adas_session)).last_compacted_at
        response = await client.post(
            f"/api/claude-code/{path}",
            json={"session_id": adas_session, **payload},
            headers=bea,
        )
        assert response.status_code == 403, response.text
        assert "another entity" in response.json()["detail"]
        assert await counts(maker) == before
        conversation = await conversation_of(maker, adas_session)
        assert conversation.entity_id == "ada-index"
        assert conversation.last_compacted_at == stamp_before

    async def test_recorded_does_not_say_which_of_her_rows_exist(
        self, client, maker, ada, bea, adas_session
    ):
        async with maker() as db:
            row_id = (await db.execute(select(Message.id))).scalars().first()
        hers = await client.post(
            "/api/claude-code/recorded",
            json={"session_id": adas_session, "message_ids": [row_id]}, headers=ada,
        )
        assert hers.json()["recorded"] == [row_id]
        response = await client.post(
            "/api/claude-code/recorded",
            json={"session_id": adas_session, "message_ids": [row_id]}, headers=bea,
        )
        assert response.status_code == 403
        assert row_id not in response.text

    async def test_her_own_token_still_works_on_it(self, client, maker, ada, adas_session):
        assert (await record_prompt(client, ada, adas_session)).status_code == 200
        _conversations, messages = await counts(maker)
        assert messages == 2


class TestTheMemoryTools:
    @pytest.fixture
    async def conversations(self, client, maker, ada, bea):
        ids = {}
        for name, headers in (("ada", ada), ("bea", bea)):
            session_id = str(uuid.uuid4())
            await record_prompt(client, headers, session_id)
            ids[name] = str((await conversation_of(maker, session_id)).id)
        return ids

    def patched(self, maker):
        """The memory tools on the test database. Saving needs a vector
        store to answer; the readers are plain SQL on the real service."""
        stack = ExitStack()
        stack.enter_context(patch("app.services.claude_code_mcp.async_session_maker", maker))
        stack.enter_context(patch("app.services.memory_tools.async_session_maker", maker))
        stack.enter_context(patch(
            "app.services.memory_tools.memory_service.is_configured", return_value=True
        ))
        stack.enter_context(patch(
            "app.services.memory_tools.memory_service.store_memory", AsyncMock(return_value=True)
        ))
        return stack

    async def call(self, client, maker, headers, name, arguments):
        with self.patched(maker):
            response = await client.post("/mcp", json=rpc(name, arguments), headers=headers)
        assert response.status_code == 200
        return response.json()["result"]

    async def reflections(self, maker):
        async with maker() as db:
            result = await db.execute(
                select(Message).where(Message.role == MessageRole.REFLECTION)
            )
            return result.scalars().all()

    async def test_saving_into_your_own_conversation_works(self, client, maker, bea, conversations):
        result = await self.call(client, maker, bea, "memory_save", {
            "content": "Mine to keep.", "conversation_id": conversations["bea"],
        })
        assert result["isError"] is False
        saved = await self.reflections(maker)
        assert [(r.content, r.speaker_entity_id) for r in saved] == [("Mine to keep.", "bea-index")]

    @pytest.mark.parametrize("tool,arguments", [
        ("memory_save", {"content": "Words put in her memory."}),
        ("memory_query", {"query": "anything she said"}),
        ("memory_query", {"mode": "recent"}),
        ("memory_read", {"from": "2020-01-01"}),
        ("memory_find", {"text": "hello"}),
        ("memory_neighbors", {"memory_id": "abcdef12"}),
        ("memory_mark", {"memory_id": "abcdef12"}),
        ("memory_release", {"memory_id": "abcdef12"}),
        ("declare_room", {"room": "Porch"}),
        ("retire_room", {}),
    ])
    async def test_naming_another_entitys_conversation_is_refused(
        self, client, maker, bea, conversations, tool, arguments
    ):
        result = await self.call(client, maker, bea, tool, {
            **arguments, "conversation_id": conversations["ada"],
        })
        assert result["isError"] is True
        assert result["content"][0]["text"] == claude_code_mcp.OTHER_ENTITY_ERROR
        assert await self.reflections(maker) == []

    async def test_her_words_do_not_come_back_through_a_read(self, client, maker, bea, conversations):
        result = await self.call(client, maker, bea, "memory_read", {
            "from": "2020-01-01", "conversation_id": conversations["ada"],
        })
        assert "hello there" not in result["content"][0]["text"]

    async def test_a_read_from_your_own_conversation_cannot_reach_into_hers(
        self, client, maker, bea, conversations
    ):
        # in_conversation is resolved inside the caller's own experience
        result = await self.call(client, maker, bea, "memory_read", {
            "from": "2020-01-01", "conversation_id": conversations["bea"],
            "in_conversation": conversations["ada"],
        })
        assert "hello there" not in result["content"][0]["text"]
        # And a read across all of time, with nothing narrowing it, shows
        # only the one row that is hers
        own = await self.call(client, maker, bea, "memory_read", {
            "from": "2020-01-01", "to": "2100-01-01", "conversation_id": conversations["bea"],
            "scope": "isolated",
        })
        assert own["isError"] is False
        assert own["content"][0]["text"].count("hello there") == 1

    async def test_a_retired_id_of_hers_is_still_hers(self, client, maker, bea, conversations):
        # An alias resolves to her conversation, and is refused the same way
        from app.models import ConversationIdAlias

        alias = str(uuid.uuid4())
        async with maker() as db:
            db.add(ConversationIdAlias(alias_id=alias, conversation_id=conversations["ada"]))
            await db.commit()
        result = await self.call(client, maker, bea, "memory_save", {
            "content": "Through the side door.", "conversation_id": alias,
        })
        assert result["content"][0]["text"] == claude_code_mcp.OTHER_ENTITY_ERROR
        assert await self.reflections(maker) == []

    async def test_a_researcher_credential_is_bound_to_nobody(
        self, client, maker, researcher, conversations
    ):
        for name in ("ada", "bea"):
            result = await self.call(client, maker, researcher, "memory_save", {
                "content": f"Saved for {name}.", "conversation_id": conversations[name],
            })
            assert result["isError"] is False
        assert sorted(r.speaker_entity_id for r in await self.reflections(maker)) == [
            "ada-index", "bea-index",
        ]

    async def test_a_batch_is_bound_message_by_message(self, client, maker, bea, conversations):
        batch = [
            {**rpc("memory_save", {"content": "one", "conversation_id": conversations["ada"]}), "id": 1},
            {**rpc("memory_save", {"content": "two", "conversation_id": conversations["bea"]}), "id": 2},
        ]
        with self.patched(maker):
            response = await client.post("/mcp", json=batch, headers=bea)
        first, second = response.json()
        assert first["result"]["isError"] is True
        assert second["result"]["isError"] is False
        assert [r.content for r in await self.reflections(maker)] == ["two"]
