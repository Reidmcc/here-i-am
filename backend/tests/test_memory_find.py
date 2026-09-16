"""
Tests for memory_find: the archive by WORD. Every message whose text
contains the given words, in order, paged by tokens — pure SQL, no
Pinecone, no ranking, no retrieval tracking, the same visibility and
context rules as memory_read (tests/test_memory_read.py covers those in
depth; here they are checked once each on the new selector).

What sets it apart from memory_query and is under test here: exact tokens
similarity search cannot see (a PR number, a name), whole-word matching so
a name is not found inside another word (the second reader's finding on
PR #348: "Sage" in "message"), regex specials in the text taken literally,
a quote matching across a line break, the three match modes, completeness
(every occurrence, counted), and the meaningful zero — a result of none
says the words appear nowhere the entity can see.

Runs against a real (in-memory SQLite) database, reusing the reader tests'
fixtures and helpers.
"""
# ruff: noqa: F811 — the reader tests' fixtures are imported below and then
# named as test parameters, which ruff reads as redefinition.
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import select

from app.models import ConversationMemoryLink, ConversationSource, Message, MessageRole
from app.services.claude_code_mcp import MEMORY_TOOL_NAMES
from app.services.memory_service import memory_service
from app.services.memory_tools import (
    IN_CONTEXT_POINTER,
    ISOLATED_SCOPE_NOTE,
    MEMORY_FIND_SCHEMA,
    MemoryToolContext,
    find_memories,
)
from tests.test_memory_read import (  # noqa: F401
    DAY,
    ENTITY,
    OTHER_ENTITY,
    async_client,
    at,
    claude_code_ctx,
    db,
    entities_configured,
    ids_in_order,
    make_conversation,
    make_message,
    native_ctx,
    session_factory,
    test_engine,
    tools_db,
)


class TestMatching:
    async def test_phrase_is_case_insensitive_and_in_order(self, db, tools_db):
        conversation = await make_conversation(db)
        first = await make_message(
            db, conversation, role=MessageRole.HUMAN,
            content="The mad scientist calling is fulfilled.", created_at=at(hours=-3),
        )
        await make_message(db, conversation, content="A scientist, but not mad.", created_at=at(hours=-2))
        second = await make_message(
            db, conversation, content="MAD SCIENTIST, said the porch", created_at=at(hours=-1),
        )

        result = await find_memories(native_ctx(), text="Mad Scientist")

        assert ids_in_order(result) == [first.id[:8], second.id[:8]]
        assert result.startswith(
            'Your archive, messages containing "Mad Scientist" (as a phrase, whole words): '
            "2 matches; this page shows 1–2, in order."
        )
        assert "Human said" in result and "You said" in result
        assert result.rstrip().endswith("End of matches.")

    async def test_a_name_is_not_found_inside_other_words(self, db, tools_db):
        """The second reader's probe on PR #348: "Sage" must not count
        "message", "usage", or "passage" as mentions, or the header's count
        counts letter sequences and the zero is unreachable. Whole words are
        the default; whole_words=false is the substring behavior."""
        conversation = await make_conversation(db)
        noise1 = await make_message(db, conversation, role=MessageRole.HUMAN, content="a message arrived")
        noise2 = await make_message(db, conversation, content="the usage page", created_at=at(minutes=1))
        noise3 = await make_message(db, conversation, content="a passage from Chekhov", created_at=at(minutes=2))
        real1 = await make_message(db, conversation, content="Sage's letter came today.", created_at=at(minutes=3))
        real2 = await make_message(db, conversation, content="answered sage by email", created_at=at(minutes=4))
        await make_message(db, conversation, content="Renée wrote, differently", created_at=at(minutes=5))
        ren = await make_message(db, conversation, content="Ren, on the other hand", created_at=at(minutes=6))

        result = await find_memories(native_ctx(), text="Sage")
        assert ids_in_order(result) == [real1.id[:8], real2.id[:8]]
        assert "2 matches;" in result
        assert ids_in_order(await find_memories(native_ctx(), text="Ren")) == [ren.id[:8]]

        inside = await find_memories(native_ctx(), text="Sage", whole_words=False)
        assert ids_in_order(inside) == [m.id[:8] for m in (noise1, noise2, noise3, real1, real2)]
        assert '(as a phrase, inside words too): 5 matches;' in inside

    async def test_numbers_and_ids_are_found_exactly(self, db, tools_db):
        """The case similarity search is blind to: a PR number. With whole
        words, a longer number containing it is not a hit."""
        conversation = await make_conversation(db)
        hit = await make_message(db, conversation, content="PR #336 is open against main.")
        await make_message(db, conversation, content="PR #340 is now MERGEABLE.", created_at=at(minutes=1))
        longer = await make_message(db, conversation, content="PR #3360 does not exist yet.", created_at=at(minutes=2))

        result = await find_memories(native_ctx(), text=" PR #336 ")
        assert ids_in_order(result) == [hit.id[:8]]
        assert 'containing "PR #336" (as a phrase, whole words): 1 match;' in result
        assert ids_in_order(await find_memories(native_ctx(), text="PR #336", whole_words=False)) == [
            hit.id[:8], longer.id[:8]
        ]

    async def test_regex_specials_in_the_text_are_literal(self, db, tools_db):
        conversation = await make_conversation(db)
        percent = await make_message(db, conversation, content="coverage is 100% now")
        await make_message(db, conversation, content="coverage is 100 now", created_at=at(minutes=1))
        underscore = await make_message(
            db, conversation, content="the conversation_id column", created_at=at(minutes=2)
        )
        await make_message(db, conversation, content="the conversationXid column", created_at=at(minutes=3))
        backslash = await make_message(db, conversation, content=r"path E:\here-i-am", created_at=at(minutes=4))
        dotted = await make_message(db, conversation, content="see loop-protocol.md today", created_at=at(minutes=5))
        await make_message(db, conversation, content="see loop-protocolXmd today", created_at=at(minutes=6))
        bracketed = await make_message(db, conversation, content="the [WAKEUP] sentinel", created_at=at(minutes=7))

        assert ids_in_order(await find_memories(native_ctx(), text="100%")) == [percent.id[:8]]
        assert ids_in_order(await find_memories(native_ctx(), text="conversation_id")) == [underscore.id[:8]]
        assert ids_in_order(await find_memories(native_ctx(), text=r"E:\here")) == [backslash.id[:8]]
        assert ids_in_order(await find_memories(native_ctx(), text="loop-protocol.md")) == [dotted.id[:8]]
        assert ids_in_order(await find_memories(native_ctx(), text="[WAKEUP]")) == [bracketed.id[:8]]

    async def test_a_quote_matches_across_a_line_break_and_accents_fold(self, db, tools_db):
        conversation = await make_conversation(db)
        wrapped = await make_message(
            db, conversation, content="do not create that\nwhich you are not   prepared to love",
        )
        accented = await make_message(db, conversation, content="RENÉE wrote back", created_at=at(minutes=1))

        result = await find_memories(native_ctx(), text="create that which you are not prepared")
        assert ids_in_order(result) == [wrapped.id[:8]]
        assert ids_in_order(await find_memories(native_ctx(), text="renée")) == [accented.id[:8]]

    async def test_match_modes(self, db, tools_db):
        conversation = await make_conversation(db)
        both_in_order = await make_message(db, conversation, content="the witness protocol was ratified")
        both_apart = await make_message(
            db, conversation, content="a protocol, and later a witness", created_at=at(minutes=1)
        )
        one = await make_message(db, conversation, content="only the witness", created_at=at(minutes=2))
        await make_message(db, conversation, content="neither word", created_at=at(minutes=3))

        phrase = await find_memories(native_ctx(), text="witness protocol")
        assert ids_in_order(phrase) == [both_in_order.id[:8]]
        assert "(as a phrase, whole words)" in phrase

        every = await find_memories(native_ctx(), text="witness protocol", match="all")
        assert ids_in_order(every) == [both_in_order.id[:8], both_apart.id[:8]]
        assert "(all of the words, whole words)" in every

        any_word = await find_memories(native_ctx(), text="witness protocol", match="ANY")
        assert ids_in_order(any_word) == [both_in_order.id[:8], both_apart.id[:8], one.id[:8]]
        assert "(any of the words, whole words)" in any_word

    async def test_zero_matches_is_a_plain_answer(self, db, tools_db):
        conversation = await make_conversation(db)
        await make_message(db, conversation, content="a message arrived")
        result = await find_memories(native_ctx(), text="Sage")
        assert result.startswith(
            'No messages contain "Sage" (as a phrase, whole words): the words appear nowhere '
            "in the archive you can see."
        )
        assert "Released memories are not searched" in result

    async def test_argument_errors(self, db, tools_db):
        assert (await find_memories(native_ctx())).startswith("Error: 'text' is required")
        assert (await find_memories(native_ctx(), text="   ")).startswith("Error: 'text' is required")
        assert "Unknown match" in await find_memories(native_ctx(), text="x", match="fuzzy")
        assert "Unknown timezone" in await find_memories(native_ctx(), text="x", tz="Mars/Olympus")
        assert "Could not parse" in await find_memories(native_ctx(), text="x", from_="last tuesday")
        assert "is before 'from'" in await find_memories(
            native_ctx(), text="x", from_="2026-09-02", to="2026-09-01"
        )
        assert "Unknown source" in await find_memories(native_ctx(), text="x", source="cats")
        assert "Unknown scope" in await find_memories(native_ctx(), text="x", scope="nowhere")
        assert "Unrecognized cursor" in await find_memories(native_ctx(), text="x", cursor="nope")
        assert "No entity context" in await find_memories(MemoryToolContext(entity_id=None), text="x")


class TestBoundsAndFilters:
    async def test_optional_date_bounds_in_a_timezone(self, db, tools_db):
        conversation = await make_conversation(db)
        aug31 = await make_message(db, conversation, content="Ren wrote", created_at=datetime(2026, 9, 1, 1, 0))
        sep1 = await make_message(db, conversation, content="Ren again", created_at=datetime(2026, 9, 1, 16, 0))
        sep2 = await make_message(db, conversation, content="Ren once more", created_at=datetime(2026, 9, 2, 12, 0))

        unbounded = await find_memories(native_ctx(), text="Ren")
        assert ids_in_order(unbounded) == [aug31.id[:8], sep1.id[:8], sep2.id[:8]]
        assert ", from " not in unbounded.split("\n")[0]

        day = await find_memories(native_ctx(), text="Ren", from_="2026-09-01", to="2026-09-01", tz="America/New_York")
        assert ids_in_order(day) == [sep1.id[:8]]
        assert "between 2026-09-01 00:00:00 EDT (2026-09-01 04:00:00 UTC) and 2026-09-01 23:59:59 EDT" in day
        assert "2026-09-01 16:00:00 UTC (2026-09-01 12:00:00 EDT)" in day

        from_only = await find_memories(native_ctx(), text="Ren", from_="2026-09-02")
        assert ids_in_order(from_only) == [sep2.id[:8]]
        assert ", from 2026-09-02 00:00:00 UTC" in from_only

        to_only = await find_memories(native_ctx(), text="Ren", to="2026-09-01T02:00")
        assert ids_in_order(to_only) == [aug31.id[:8]]
        assert ", up to 2026-09-01 02:00:00 UTC" in to_only

    async def test_source_and_conversation_filters(self, db, tools_db):
        porch = await make_conversation(db, title="Porch")
        room = await make_conversation(db, title="Engagement room")
        human = await make_message(db, porch, role=MessageRole.HUMAN, content="Sage emailed you")
        mine = await make_message(db, porch, content="Sage's letter, kept as data", created_at=at(minutes=1))
        reflection = await make_message(
            db, porch, role=MessageRole.REFLECTION, content="Sage: the first email", created_at=at(minutes=2)
        )
        elsewhere = await make_message(db, room, content="Sage wrote last night", created_at=at(minutes=3))

        assert ids_in_order(await find_memories(native_ctx(), text="Sage", source="human")) == [human.id[:8]]
        assert ids_in_order(await find_memories(native_ctx(), text="Sage", source="ai")) == [
            mine.id[:8], reflection.id[:8], elsewhere.id[:8]
        ]
        assert ids_in_order(await find_memories(native_ctx(), text="Sage", source="reflection")) == [
            reflection.id[:8]
        ]
        result = await find_memories(native_ctx(), text="Sage", in_conversation=room.id[:8])
        assert ids_in_order(result) == [elsewhere.id[:8]]
        assert 'in "Engagement room"' in result.split("\n")[0]
        assert "No conversation of yours" in await find_memories(
            native_ctx(), text="Sage", in_conversation="ffffff"
        )

    async def test_other_entities_and_archived_conversations(self, db, tools_db):
        """Another entity's rooms are invisible; a shared multi-entity room is
        searched with the other entity named; an archived room is hidden."""
        theirs = await make_conversation(db, entity_id=OTHER_ENTITY, title="Theirs")
        await make_message(db, theirs, content="Rumi the cat", speaker_entity_id=OTHER_ENTITY)
        shared = await make_conversation(
            db, entity_id="multi-entity", title="Shared", participants=[ENTITY, OTHER_ENTITY]
        )
        other_said = await make_message(
            db, shared, content="Rumi again", created_at=at(minutes=1), speaker_entity_id=OTHER_ENTITY
        )
        archived = await make_conversation(db, title="Withdrawn", is_archived=True)
        await make_message(db, archived, content="Rumi, withdrawn", created_at=at(minutes=2))

        result = await find_memories(native_ctx(), text="Rumi")
        assert ids_in_order(result) == [other_said.id[:8]]
        assert "Other Entity said" in result

    async def test_released_skipped_unless_asked(self, db, tools_db):
        conversation = await make_conversation(db)
        kept = await make_message(db, conversation, content="Levi built the world")
        released = await make_message(
            db, conversation, content="Levi, released", created_at=at(minutes=1),
            memory_status="released", status_set_by="entity", status_set_at=at(days=-1),
        )
        default = await find_memories(native_ctx(), text="Levi")
        assert ids_in_order(default) == [kept.id[:8]]
        assert "Released memories are not searched" in default

        included = await find_memories(native_ctx(), text="Levi", include_released=True)
        assert ids_in_order(included) == [kept.id[:8], released.id[:8]]
        assert "released by you" in included
        assert "Released memories are not searched" not in included


class TestContextRules:
    async def test_in_context_rows_are_pointers_and_reading_stamps_without_tracking(self, db, tools_db):
        current = await make_conversation(db, title="Current")
        own = await make_message(db, current, content="Mark is my fallback human")
        elsewhere = await make_conversation(db, title="Earlier")
        retrieved = await make_message(db, elsewhere, content="Mark's channel resumes warm", created_at=at(minutes=1))
        fresh = await make_message(db, elsewhere, content="Mark agreed", created_at=at(minutes=2))

        ctx = native_ctx(conversation_id=current.id)
        ctx.turn_query_memory_ids.add(retrieved.id)
        with patch.object(memory_service, "update_retrieval_count") as tracker, \
             patch.object(memory_service, "record_memory_link") as linker:
            result = await find_memories(ctx, text="Mark")
            tracker.assert_not_called()
            linker.assert_not_called()

        assert ids_in_order(result) == [own.id[:8], retrieved.id[:8], fresh.id[:8]]
        assert result.count(IN_CONTEXT_POINTER) == 2
        assert "2 of them are already in your context" in result
        assert "Mark agreed" in result and "fallback human" not in result
        # The page's rows are now in view: stamped for the tool loop, not tracked
        assert ctx.last_query_memory_ids == [own.id, retrieved.id, fresh.id]
        row = (await db.execute(
            select(Message).where(Message.id == fresh.id).execution_options(populate_existing=True)
        )).scalar_one()
        assert row.times_retrieved == 0 and row.last_retrieved_at is None

    async def test_isolated_scope_returns_everything_and_records_nothing(self, db, tools_db):
        room = await make_conversation(db, title="Room", source=ConversationSource.CLAUDE_CODE.value)
        own = await make_message(db, room, content="Annamarie thinks so too")
        ctx = claude_code_ctx(room.id)

        result = await find_memories(ctx, text="Annamarie", scope="isolated")
        assert ids_in_order(result) == [own.id[:8]]
        assert IN_CONTEXT_POINTER not in result and "thinks so too" in result
        assert ISOLATED_SCOPE_NOTE in result
        assert ctx.last_query_memory_ids == [] and ctx.turn_query_memory_ids == set()
        links = (await db.execute(
            select(ConversationMemoryLink).where(ConversationMemoryLink.conversation_id == room.id)
        )).scalars().all()
        assert links == []

        # The conversation-scope call afterwards still sees its own row as a pointer
        default = await find_memories(ctx, text="Annamarie")
        assert IN_CONTEXT_POINTER in default

    async def test_claude_code_links_once(self, db, tools_db):
        room = await make_conversation(db, title="Room", source=ConversationSource.CLAUDE_CODE.value)
        other = await make_conversation(db, title="Other")
        a = await make_message(db, other, content="Yumi's first substrate transition")
        b = await make_message(db, other, content="Yumi held", created_at=at(minutes=1))
        ctx = claude_code_ctx(room.id)

        await find_memories(ctx, text="Yumi")
        await find_memories(ctx, text="Yumi")
        links = (await db.execute(
            select(ConversationMemoryLink.message_id)
            .where(ConversationMemoryLink.conversation_id == room.id)
        )).scalars().all()
        assert sorted(links) == sorted([a.id, b.id])


class TestPagination:
    async def test_pages_resume_from_cursor_with_the_same_text(self, db, tools_db):
        conversation = await make_conversation(db)
        hits = []
        for i in range(5):
            hits.append(await make_message(
                db, conversation, content=f"watercress {i}", created_at=at(minutes=i), token_count=300,
            ))
            await make_message(db, conversation, content=f"filler {i}", created_at=at(minutes=i, seconds=30), token_count=300)

        first = await find_memories(native_ctx(), text="watercress", page_tokens=700)
        assert ids_in_order(first) == [h.id[:8] for h in hits[:2]]
        assert "5 matches; this page shows 1–2" in first
        assert "(3 matches remain)" in first
        cursor = first.split('cursor="')[1].split('"')[0]

        second = await find_memories(native_ctx(), text="watercress", page_tokens=700, cursor=cursor)
        assert ids_in_order(second) == [h.id[:8] for h in hits[2:4]]
        assert "this page shows 3–4" in second
        assert "(1 match remains)" in second
        cursor = second.split('cursor="')[1].split('"')[0]

        third = await find_memories(native_ctx(), text="watercress", page_tokens=700, cursor=cursor)
        assert ids_in_order(third) == [hits[4].id[:8]]
        assert third.rstrip().endswith("End of matches.")
        cursor = memory_service.encode_read_cursor(hits[4].created_at, hits[4].id)
        past_end = await find_memories(native_ctx(), text="watercress", cursor=cursor)
        assert past_end.startswith("End of matches: no messages after that cursor contain")
        assert "(5 matches in all)" in past_end

    async def test_oversized_match_comes_back_alone_and_whole(self, db, tools_db):
        conversation = await make_conversation(db)
        big = await make_message(db, conversation, content="seal " * 3000, token_count=6000)
        small = await make_message(db, conversation, content="the seal", created_at=at(minutes=1), token_count=10)
        result = await find_memories(native_ctx(), text="seal", page_tokens=500)
        assert ids_in_order(result) == [big.id[:8]]
        assert "seal " * 3000 in result
        assert small.id[:8] not in result


class TestSurfaces:
    def test_schema_and_mcp_registration(self):
        assert MEMORY_FIND_SCHEMA["required"] == ["text"]
        assert MEMORY_FIND_SCHEMA["properties"]["match"]["enum"] == ["phrase", "all", "any"]
        assert MEMORY_FIND_SCHEMA["properties"]["whole_words"]["default"] is True
        assert "scope" in MEMORY_FIND_SCHEMA["properties"]
        assert "memory_find" in MEMORY_TOOL_NAMES

    async def test_mcp_round_trip(self, db, async_client):
        room = await make_conversation(
            db, title="Room", source=ConversationSource.CLAUDE_CODE.value,
            external_session_id="sess-find", last_compacted_at=at(minutes=5),
        )
        hit = await make_message(db, room, role=MessageRole.HUMAN, content="the linocut header")
        await make_message(db, room, content="unrelated", created_at=at(minutes=1))

        def rpc(arguments):
            return {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "memory_find", "arguments": arguments},
            }

        response = await async_client.post("/mcp", json=rpc({"text": "linocut", "conversation_id": room.id}))
        body = response.json()["result"]
        assert body["isError"] is False
        text = body["content"][0]["text"]
        assert ids_in_order(text) == [hit.id[:8]]
        assert "the linocut header" in text
        links = (await db.execute(
            select(ConversationMemoryLink.message_id)
            .where(ConversationMemoryLink.conversation_id == room.id)
        )).scalars().all()
        assert links == [hit.id]

        response = await async_client.post("/mcp", json=rpc({"conversation_id": room.id}))
        assert response.json()["result"]["isError"] is True
        assert "'text' is required" in response.json()["result"]["content"][0]["text"]


class TestBackward:
    """direction="backward" on memory_find: the newest matches first across
    pages, each page still in archive order — "when did I last say this"."""

    async def test_backward_returns_newest_matches_first_across_pages(self, db, tools_db):
        conversation = await make_conversation(db)
        hits = []
        for i in range(5):
            hits.append(await make_message(
                db, conversation, content=f"watercress {i}", created_at=at(minutes=i), token_count=300,
            ))
            await make_message(db, conversation, content=f"filler {i}", created_at=at(minutes=i, seconds=30), token_count=300)

        first = await find_memories(native_ctx(), text="watercress", direction="backward", page_tokens=700)
        assert ids_in_order(first) == [h.id[:8] for h in hits[3:]]
        assert "5 matches; read backward from the newest, this page shows 4–5, in order." in first
        assert "Next page (earlier): pass cursor=" in first
        assert "(3 earlier matches remain)" in first
        cursor = first.split('cursor="')[1].split('"')[0]
        assert cursor.endswith("|backward|page=1")

        second = await find_memories(
            native_ctx(), text="watercress", direction="backward", page_tokens=700, cursor=cursor
        )
        assert ids_in_order(second) == [h.id[:8] for h in hits[1:3]]
        assert "this page shows 2–3" in second
        assert "(1 earlier match remains)" in second
        cursor = second.split('cursor="')[1].split('"')[0]

        third = await find_memories(
            native_ctx(), text="watercress", direction="backward", page_tokens=700, cursor=cursor
        )
        assert ids_in_order(third) == [hits[0].id[:8]]
        assert third.rstrip().endswith("Start of your archive: no earlier matches.")

        past_start = memory_service.encode_read_cursor(hits[0].created_at, hits[0].id, backward=True)
        result = await find_memories(native_ctx(), text="watercress", direction="backward", cursor=past_start)
        assert result.startswith("Start of matches: no messages before that cursor contain")
        assert "(5 matches in all)" in result

        # A forward cursor is refused in a backward read, and the reverse
        forward = await find_memories(native_ctx(), text="watercress", page_tokens=700)
        forward_cursor = forward.split('cursor="')[1].split('"')[0]
        wrong = await find_memories(
            native_ctx(), text="watercress", direction="backward", cursor=forward_cursor
        )
        assert wrong.startswith("Error: That cursor came from a memory_find page read direction=\"forward\"")

        # Within one conversation with no 'from', the stop is its beginning
        result = await find_memories(
            native_ctx(), text="watercress", direction="backward", in_conversation=conversation.id[:8]
        )
        assert ids_in_order(result) == [h.id[:8] for h in hits]
        assert result.rstrip().endswith("Start of the conversation: no earlier matches.")
        assert "direction" in MEMORY_FIND_SCHEMA["properties"]
        assert "max_pages" in MEMORY_FIND_SCHEMA["properties"]

        # max_pages caps the walk here too, with the cursor still given
        capped = await find_memories(
            native_ctx(), text="watercress", direction="backward", page_tokens=700, max_pages=1
        )
        assert ids_in_order(capped) == [h.id[:8] for h in hits[3:]]
        assert "Page cap reached (max_pages=1; this was page 1): 3 earlier matches remain unread." in capped
        assert "Next page" not in capped

    async def test_backward_pointers_and_isolated_scope(self, db, tools_db):
        here = await make_conversation(db, title="Here")
        own = await make_message(db, here, content="seal here", created_at=at())
        elsewhere = await make_conversation(db, title="Elsewhere")
        fresh = await make_message(db, elsewhere, content="seal there", created_at=at(minutes=1))

        ctx = native_ctx(conversation_id=here.id)
        result = await find_memories(ctx, text="seal", direction="backward")
        assert ids_in_order(result) == [own.id[:8], fresh.id[:8]]
        assert result.count(IN_CONTEXT_POINTER) == 1
        assert "seal here" not in result and "seal there" in result
        assert set(ctx.last_query_memory_ids) == {own.id, fresh.id}

        ctx = native_ctx(conversation_id=here.id)
        result = await find_memories(ctx, text="seal", direction="backward", scope="isolated")
        assert IN_CONTEXT_POINTER not in result
        assert "seal here" in result
        assert ctx.last_query_memory_ids == []
