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

    async def test_a_researcher_token_doing_a_machines_job_is_said(self, client, bea, researcher):
        """A researcher token in HIM_TOKEN works on every hook call, and
        puts the researcher's whole reach where the entity's own shell can
        read it. The session is told at every start; with an entity token
        there is nothing to say."""
        start = {"session_id": str(uuid.uuid4()), "entity": "Bea"}
        loud = await client.post("/api/claude-code/session-start", json=start, headers=researcher)
        notice = loud.json()["credential_notice"]
        assert "researcher token" in notice and "token revoke" in notice
        quiet = await client.post("/api/claude-code/session-start", json=start, headers=bea)
        assert quiet.json()["credential_notice"] is None

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
        detail = response.json()["detail"]
        # Says whose token this is, what was asked for, and both ways out
        assert "bound to 'Bea'" in detail and "named 'Ada'" in detail
        assert "remove HIM_ENTITY" in detail and "--entity Ada" in detail
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


class TestASessionIdTwoEntitiesReachFor:
    async def test_the_loser_of_a_race_is_not_handed_the_winners_conversation(
        self, client, maker, ada, bea, monkeypatch
    ):
        """Two requests register one session id at the same moment. Each
        saw no row when it was checked; one creates the row and the other,
        told the id is taken, is given the row that exists. For a token
        bound to the other entity that row is not its own, and it is
        refused before it writes a word into it (PR #411 review)."""
        from app.routes import claude_code as routes

        session_id = str(uuid.uuid4())
        assert (await record_prompt(client, ada, session_id)).status_code == 200
        before = await counts(maker)

        # Bea's request, at the moment Ada's row did not exist yet
        async def saw_nothing(db, principal, sid):
            return None

        monkeypatch.setattr(routes, "_refuse_another_entitys_session", saw_nothing)
        for path, payload in (
            ("retrieve", {"prompt": "words that are not hers"}),
            ("log-assistant", {"content": "nor these", "message_uuid": str(uuid.uuid4())}),
            ("session-start", {"source": "compact"}),
            ("compact-talk", {"pre_compaction": "saved", "reflection": "nor this"}),
        ):
            response = await client.post(
                f"/api/claude-code/{path}", json={"session_id": session_id, **payload}, headers=bea
            )
            if path == "compact-talk":
                # This one has always had its own check, and answers in its
                # body: no talk handed over, the reflection reported unsaved
                body = response.json()
                assert response.status_code == 200 and body["available"] is False
                assert "another entity" in body["reflection_error"]
                assert not body["text"]
            else:
                assert response.status_code == 403, (path, response.text)
        assert await counts(maker) == before
        conversation = await conversation_of(maker, session_id)
        assert conversation.entity_id == "ada-index"
        assert conversation.last_compacted_at is None


class TestAWholeSessionUnderItsOwnToken:
    async def test_every_hook_call_and_the_tools_work_as_in_production(
        self, client, maker, bea
    ):
        """The rest of the suite calls these endpoints as the researcher,
        bound to nobody. In production they are always called with an
        entity token, so here is a session's whole life under one: start,
        prompts, a turn recorded, the check, a compaction, the memory
        tools, the end."""
        session_id = str(uuid.uuid4())
        post = lambda path, body: client.post(  # noqa: E731
            f"/api/claude-code/{path}", json={"session_id": session_id, **body}, headers=bea
        )
        started = await post("session-start", {"source": "startup", "cwd": "/home/bea/room"})
        assert started.status_code == 200 and started.json()["entity_id"] == "bea-index"
        conversation_id = started.json()["conversation_id"]

        prompt_id, turn_id = str(uuid.uuid4()), str(uuid.uuid4())
        prompted = await post("retrieve", {"prompt": "good morning", "message_id": prompt_id})
        assert prompted.status_code == 200
        assert prompted.json()["conversation_id"] == conversation_id
        logged = await post("log-assistant", {"content": "good morning to you", "message_uuid": turn_id})
        assert logged.json()["message_id"] == turn_id
        checked = await post("recorded", {"message_ids": [prompt_id, turn_id, str(uuid.uuid4())]})
        assert checked.json()["recorded"] == [prompt_id, turn_id]

        with patch("app.services.claude_code_mcp.async_session_maker", maker), \
                patch("app.services.memory_tools.async_session_maker", maker), \
                patch("app.services.memory_tools.memory_service.is_configured", return_value=True), \
                patch("app.services.memory_tools.memory_service.store_memory",
                      AsyncMock(return_value=True)):
            talk = await post("compact-talk", {
                "pre_compaction": "saved", "reflection": "A morning worth keeping.",
            })
            assert talk.status_code == 200 and talk.json()["available"] is True
            assert "good morning to you" in talk.json()["text"]
            assert talk.json()["reflection_id"]
            taken = await client.post(
                "/api/claude-code/compact-talk/taken",
                json={"delivery_id": talk.json()["delivery_id"]}, headers=bea,
            )
            assert taken.status_code == 200
            compacted = await post("session-start", {"source": "compact"})
            assert compacted.status_code == 200
            assert compacted.json()["conversation_id"] == conversation_id

            listed = await client.post("/mcp", json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/list",
            }, headers=bea)
            assert {tool["name"] for tool in listed.json()["result"]["tools"]} >= {
                "memory_query", "memory_save", "memory_read", "memory_neighbors",
            }
            read = await client.post("/mcp", json=rpc("memory_read", {
                "from": "2020-01-01", "to": "2100-01-01", "scope": "isolated",
                "conversation_id": conversation_id,
            }), headers=bea)
            text = read.json()["result"]["content"][0]["text"]
            assert "good morning" in text and "A morning worth keeping." in text
            neighbors = await client.post("/mcp", json=rpc("memory_neighbors", {
                "memory_id": turn_id[:8], "scope": "isolated", "conversation_id": conversation_id,
            }), headers=bea)
            assert "good morning to you" in neighbors.json()["result"]["content"][0]["text"]
            pinned = await client.post("/mcp", json=rpc("memory_mark", {
                "memory_id": turn_id, "conversation_id": conversation_id,
            }), headers=bea)
            assert pinned.json()["result"]["isError"] is False

        ended = await post("session-end", {"reason": "other"})
        assert ended.status_code == 200 and ended.json()["conversation_id"] == conversation_id
        conversation = await conversation_of(maker, session_id)
        assert conversation.entity_id == "bea-index"
        assert conversation.last_compacted_at is not None


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

    async def test_what_exclude_says_it_left_out_counts_nothing_of_hers(
        self, client, maker, ada, bea, conversations
    ):
        # `exclude` (issue #415, merged under this branch) reports how many
        # rows it left out, and memory_find's zero says when the words were
        # in one of them. Either would tell Bea what is in Ada's reflections
        # if the count looked outside Bea's experience
        saved = await self.call(client, maker, ada, "memory_save", {
            "content": "The marmalade is behind the flour.",
            "conversation_id": conversations["ada"],
        })
        assert saved["isError"] is False
        everything = {"from": "2020-01-01", "to": "2100-01-01", "scope": "isolated"}
        kinds = ["reflection", "inter_session"]

        hers = await self.call(client, maker, ada, "memory_read", {
            **everything, "exclude": kinds, "conversation_id": conversations["ada"],
        })
        assert "exclude on: 1 reflection " in hers["content"][0]["text"]

        mine = await self.call(client, maker, bea, "memory_read", {
            **everything, "exclude": kinds, "conversation_id": conversations["bea"],
        })
        text = mine["content"][0]["text"]
        assert "exclude on: 0 reflections " in text
        assert "marmalade" not in text

        found = await self.call(client, maker, bea, "memory_find", {
            "text": "marmalade", "exclude": kinds, "conversation_id": conversations["bea"],
        })
        text = found["content"][0]["text"]
        assert "exclude on: 0 reflections " in text
        assert "except in what was left out" not in text
        # Ada, asking the same, is told where the word went
        found = await self.call(client, maker, ada, "memory_find", {
            "text": "marmalade", "exclude": kinds, "conversation_id": conversations["ada"],
        })
        assert "except in what was left out" in found["content"][0]["text"]

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

    async def row_ids(self, maker, conversation_id):
        async with maker() as db:
            result = await db.execute(
                select(Message.id).where(Message.conversation_id == conversation_id)
            )
            return list(result.scalars().all())

    async def status_of(self, maker, message_id):
        async with maker() as db:
            row = await db.get(Message, message_id)
            return row.memory_status, row.status_set_by

    # --- by memory id (PR #411 review, lane B). Every test above names a
    # CONVERSATION that isn't Bea's. These name a MEMORY that isn't, from
    # Bea's own conversation, which the first version let through: the id
    # resolver looked across the whole table and checked afterwards, by a
    # looser rule than the readers'.

    @pytest.mark.parametrize("tool,arguments", [
        ("memory_neighbors", {}),
        ("memory_mark", {}),
        ("memory_release", {}),
        ("memory_release", {"undo": True}),
    ])
    async def test_a_memory_of_hers_is_not_found_by_its_id(
        self, client, maker, bea, conversations, tool, arguments
    ):
        (hers,) = await self.row_ids(maker, conversations["ada"])
        for memory_id in (hers, hers[:8]):
            result = await self.call(client, maker, bea, tool, {
                **arguments, "memory_id": memory_id, "conversation_id": conversations["bea"],
            })
            text = result["content"][0]["text"]
            assert result["isError"] is True and "No memory found" in text
            assert "hello there" not in text
        assert await self.status_of(maker, hers) == (None, None)

    @pytest.mark.parametrize("pattern", ["%%%%%%", "______", "0%%%%%", "______%%"])
    async def test_a_wildcard_for_an_id_finds_nothing_and_lists_nothing(
        self, client, maker, bea, conversations, pattern
    ):
        (hers,) = await self.row_ids(maker, conversations["ada"])
        (mine,) = await self.row_ids(maker, conversations["bea"])
        for tool in ("memory_neighbors", "memory_mark", "memory_release"):
            result = await self.call(client, maker, bea, tool, {
                "memory_id": pattern, "conversation_id": conversations["bea"],
            })
            text = result["content"][0]["text"]
            assert "No memory found" in text
            assert hers[:8] not in text and mine[:8] not in text
        assert await self.status_of(maker, hers) == (None, None)
        assert await self.status_of(maker, mine) == (None, None)

    async def test_citing_or_revising_a_memory_of_hers_saves_nothing(
        self, client, maker, bea, conversations
    ):
        (hers,) = await self.row_ids(maker, conversations["ada"])
        for field in ("cites", "revises"):
            result = await self.call(client, maker, bea, "memory_save", {
                "content": "Leaning on what she said.", field: [hers],
                "conversation_id": conversations["bea"],
            })
            assert result["isError"] is True
            assert "hello there" not in result["content"][0]["text"]
        assert await self.reflections(maker) == []

    async def test_a_row_that_names_no_entity_is_the_default_entitys_alone(
        self, client, maker, ada, bea, conversations
    ):
        # Imported and legacy conversations can carry no entity. They are
        # part of the default entity's experience (Ada, here) and nobody
        # else's, for the id tools as for the readers
        async with maker() as db:
            db.add(Conversation(id="legacy-conversation", title="Imported"))
            db.add(Message(
                id="1e9ac0de-0000-4000-8000-000000000001", conversation_id="legacy-conversation",
                role=MessageRole.HUMAN, content="my sister's address is 12 Elm",
            ))
            await db.commit()
        legacy = "1e9ac0de-0000-4000-8000-000000000001"
        for tool in ("memory_neighbors", "memory_mark", "memory_release"):
            result = await self.call(client, maker, bea, tool, {
                "memory_id": legacy, "conversation_id": conversations["bea"],
            })
            assert "No memory found" in result["content"][0]["text"]
            assert "12 Elm" not in result["content"][0]["text"]
        assert await self.status_of(maker, legacy) == (None, None)
        # Ada, whose experience it is in, reads it and can release it
        read = await self.call(client, maker, ada, "memory_neighbors", {
            "memory_id": legacy, "conversation_id": conversations["ada"],
        })
        assert "12 Elm" in read["content"][0]["text"]
        released = await self.call(client, maker, ada, "memory_release", {
            "memory_id": legacy, "conversation_id": conversations["ada"],
        })
        assert released["isError"] is False
        assert await self.status_of(maker, legacy) == ("released", "entity")

    async def test_in_a_shared_room_each_curates_the_human_and_itself(
        self, client, maker, ada, bea, conversations
    ):
        # A memory's status is one value on the row. Releasing what another
        # entity said would take it out of her recall too, and then tell
        # her she released it
        from app.models import ConversationEntity

        async with maker() as db:
            db.add(Conversation(id="shared-room", title="Shared", entity_id="multi-entity"))
            for index in ("ada-index", "bea-index"):
                db.add(ConversationEntity(conversation_id="shared-room", entity_id=index))
            db.add(Message(id="5a4ed000-0000-4000-8000-00000000000a", conversation_id="shared-room",
                           role=MessageRole.HUMAN, content="said by the human"))
            db.add(Message(id="5a4ed000-0000-4000-8000-00000000000b", conversation_id="shared-room",
                           role=MessageRole.ASSISTANT, content="said by Ada",
                           speaker_entity_id="ada-index"))
            db.add(Message(id="5a4ed000-0000-4000-8000-00000000000c", conversation_id="shared-room",
                           role=MessageRole.ASSISTANT, content="said by Bea",
                           speaker_entity_id="bea-index"))
            await db.commit()
        human, adas, beas = (f"5a4ed000-0000-4000-8000-00000000000{c}" for c in "abc")

        # Bea is in the room, so she can read all of it
        read = await self.call(client, maker, bea, "memory_neighbors", {
            "memory_id": adas, "conversation_id": conversations["bea"],
        })
        assert "said by Ada" in read["content"][0]["text"]

        for tool in ("memory_release", "memory_mark"):
            refused = await self.call(client, maker, bea, tool, {
                "memory_id": adas, "conversation_id": conversations["bea"],
            })
            assert refused["isError"] is True
            assert "another entity's words" in refused["content"][0]["text"]
        assert await self.status_of(maker, adas) == (None, None)

        for memory_id in (human, beas):
            done = await self.call(client, maker, bea, "memory_release", {
                "memory_id": memory_id, "conversation_id": conversations["bea"],
            })
            assert done["isError"] is False
            assert await self.status_of(maker, memory_id) == ("released", "entity")

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
