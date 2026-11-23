"""
Tests for the archive readers' `exclude` (issue #415): leaving reflections
and inter-session letters out of memory_read, memory_find, and
memory_neighbors, so a span reads as the talk without the accounts of it.

Rules under test: each kind and both; excluded rows take no page budget and
are in no count (total, offset, remaining, memory_find's match total,
memory_neighbors' before/after); the first line always says the filter is on
and how many rows it left out, zero included, on every shape of result;
nothing left out is recorded as seen; visibility rules decide what is
counted; an unknown kind is an error, never ignored; and with `exclude`
absent, every page is what it was.
"""
# ruff: noqa: F811 — the reader tests' fixtures are imported below and then
# named as test parameters, which ruff reads as redefinition.
import pytest
from sqlalchemy import select

from app.models import ConversationMemoryLink, ConversationSource, MessageRole
from app.services import memory_tools
from app.services.memory_service import VALID_EXCLUDE_KINDS
from app.services.memory_tools import (
    EXCLUDE_FRAME_TOKENS,
    MEMORY_FIND_SCHEMA,
    MEMORY_NEIGHBORS_SCHEMA,
    MEMORY_QUERY_SCHEMA,
    MEMORY_READ_SCHEMA,
    PAGE_FRAME_TOKENS,
    READ_PAGE_MAX_BYTES,
    READ_PAGE_TOKENS_MAX,
    READ_PAGE_TOKENS_MIN,
    RENDERED_CHARS_PER_TOKEN,
    _exclude_suffix,
    _rows_budget,
    find_memories,
    neighbor_memories,
    read_memories,
    rendered_tokens,
    set_memory_tool_context,
)
from tests.test_memory_read import (  # noqa: F401
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
    prose,
    session_factory,
    test_engine,
    tools_db,
)

BOTH = ["reflection", "inter_session"]
DAY = "2026-09-01"


async def make_day(db, conversation, words="the harbor"):
    """human, reply, reflection, letter, human, reflection, reply — by name."""
    rows = {}
    plan = [
        ("ask", MessageRole.HUMAN, {}),
        ("reply", MessageRole.ASSISTANT, {}),
        ("reflection", MessageRole.REFLECTION, {"speaker_entity_id": ENTITY}),
        ("letter", MessageRole.ASSISTANT, {"sibling_session": "Porch"}),
        ("ask2", MessageRole.HUMAN, {}),
        ("reflection2", MessageRole.REFLECTION, {"speaker_entity_id": ENTITY}),
        ("reply2", MessageRole.ASSISTANT, {}),
    ]
    for i, (name, role, extra) in enumerate(plan):
        rows[name] = await make_message(
            db, conversation, role=role, content=f"{name} about {words}",
            created_at=at(minutes=i), **extra,
        )
    return rows


def short(rows, *names):
    return [rows[name].id[:8] for name in names]


def first_line(text):
    return text.split("\n", 1)[0]


def cursor_of(page):
    return page.split('cursor="', 1)[1].split('"', 1)[0] if 'cursor="' in page else None


async def walk(reader, **kwargs):
    """Every page of a read, following cursors."""
    pages, cursor = [], None
    while True:
        page = await reader(cursor=cursor, **kwargs)
        pages.append(page)
        cursor = cursor_of(page)
        if cursor is None:
            return pages


# ============================================================
# memory_read
# ============================================================

class TestRead:
    @pytest.mark.parametrize("exclude, names, note", [
        (["reflection"], ("ask", "reply", "letter", "ask2", "reply2"),
         "(exclude on: 2 reflections left out of the span)"),
        (["inter_session"], ("ask", "reply", "reflection", "ask2", "reflection2", "reply2"),
         "(exclude on: 1 inter-session letter left out of the span)"),
        (BOTH, ("ask", "reply", "ask2", "reply2"),
         "(exclude on: 2 reflections and 1 inter-session letter left out of the span)"),
    ])
    async def test_each_kind_and_both(self, db, tools_db, exclude, names, note):
        rows = await make_day(db, await make_conversation(db))
        result = await read_memories(native_ctx(), from_=DAY, exclude=exclude)

        assert ids_in_order(result) == short(rows, *names)
        assert first_line(result).startswith(
            "Your archive, 2026-09-01 00:00:00 UTC to 2026-09-01 23:59:59 UTC "
            f"{note}: {len(names)} messages in the span; this page shows 1–{len(names)}, in order."
        )
        for name in set(rows) - set(names):
            assert rows[name].content not in result

    async def test_absent_or_empty_changes_nothing(self, db, tools_db):
        rows = await make_day(db, await make_conversation(db))
        plain = await read_memories(native_ctx(), from_=DAY)
        assert ids_in_order(plain) == short(rows, *rows)
        assert "exclude" not in plain
        for nothing in (None, [], "", "  ", [""], [None]):
            assert await read_memories(native_ctx(), from_=DAY, exclude=nothing) == plain

    async def test_the_note_is_there_when_nothing_was_left_out(self, db, tools_db):
        conversation = await make_conversation(db)
        await make_message(db, conversation, role=MessageRole.HUMAN, content="only this")
        result = await read_memories(native_ctx(), from_=DAY, exclude=BOTH)
        assert (
            "(exclude on: 0 reflections and 0 inter-session letters left out of the span): "
            "1 messages in the span"
        ) in first_line(result)

    async def test_every_shape_of_result_says_so(self, db, tools_db):
        """An empty span, and a cursor past the last row in either direction."""
        conversation = await make_conversation(db)
        await make_message(
            db, conversation, role=MessageRole.REFLECTION, content="r", speaker_entity_id=ENTITY,
        )
        note = "(exclude on: 1 reflection left out of the span)"
        empty = await read_memories(native_ctx(), from_=DAY, exclude=["reflection"])
        assert ids_in_order(empty) == []
        assert first_line(empty).startswith("No messages between") and f"{note}." in first_line(empty)

        talk = await make_message(db, conversation, content="talk", created_at=at(minutes=1))
        for direction, edge in (
            ("forward", "2026-09-01T23:00:00+00:00|zzz|page=1"),
            ("backward", "2026-09-01T01:00:00+00:00|000|backward|page=1"),
        ):
            past = await read_memories(
                native_ctx(), from_=DAY, to=DAY, exclude=["reflection"],
                direction=direction, cursor=edge,
            )
            assert talk.content not in past
            assert f"{note} (1 in the span)" in first_line(past)

    async def test_note_sits_with_the_other_filters(self, db, tools_db):
        room = await make_conversation(db, title="Room")
        rows = await make_day(db, room)
        result = await read_memories(
            native_ctx(), from_=DAY, source="ai", in_conversation=room.id[:8],
            exclude=["reflection"],
        )
        assert ids_in_order(result) == short(rows, "reply", "letter", "reply2")
        assert (
            '(AI-authored messages only), in "Room" '
            "(exclude on: 2 reflections left out of the span): 3 messages"
        ) in first_line(result)

    async def test_a_kind_the_source_already_rules_out_is_a_no_op_and_not_a_zero(self, db, tools_db):
        """A "0 reflections" under source=human would be read as there being
        none in the span."""
        rows = await make_day(db, await make_conversation(db))
        result = await read_memories(native_ctx(), from_=DAY, source="human", exclude=BOTH)
        assert ids_in_order(result) == short(rows, "ask", "ask2")
        assert (
            '(exclude on: source="human" already leaves out reflections and inter-session letters)'
        ) in first_line(result)
        assert "0 reflections" not in result

        result = await read_memories(native_ctx(), from_=DAY, source="reflection", exclude=["inter_session"])
        assert ids_in_order(result) == short(rows, "reflection", "reflection2")
        assert '(exclude on: source="reflection" already leaves out inter-session letters)' in first_line(result)

    async def test_excluding_the_one_kind_source_keeps_is_an_explained_empty_page(self, db, tools_db):
        await make_day(db, await make_conversation(db))
        result = await read_memories(native_ctx(), from_=DAY, source="reflection", exclude=["reflection"])
        assert ids_in_order(result) == []
        assert "(exclude on: 2 reflections left out of the span)" in first_line(result)

    async def test_counts_only_rows_the_reader_could_see(self, db, tools_db):
        """A released reflection, one in an archived conversation, another
        entity's, and one outside the span are in no count."""
        conversation = await make_conversation(db)
        await make_day(db, conversation)

        async def reflection(where, **kwargs):
            return await make_message(
                db, where, role=MessageRole.REFLECTION, content="unseen",
                speaker_entity_id=ENTITY, **{"created_at": at(minutes=30), **kwargs},
            )

        await reflection(conversation, memory_status="released")
        await reflection(await make_conversation(db, is_archived=True))
        await reflection(await make_conversation(db, entity_id=OTHER_ENTITY))
        await reflection(conversation, created_at=at(days=2))

        result = await read_memories(native_ctx(), from_=DAY, exclude=["reflection"])
        assert "(exclude on: 2 reflections left out of the span): 5 messages" in first_line(result)
        result = await read_memories(
            native_ctx(), from_=DAY, exclude=["reflection"], include_released=True
        )
        assert "(exclude on: 3 reflections left out of the span): 5 messages" in first_line(result)

    async def test_another_entitys_reflections_go_too(self, db, tools_db):
        shared = await make_conversation(
            db, entity_id="multi-entity", participants=[ENTITY, OTHER_ENTITY]
        )
        said = await make_message(db, shared, content="theirs", speaker_entity_id=OTHER_ENTITY)
        await make_message(
            db, shared, role=MessageRole.REFLECTION, content="their account",
            speaker_entity_id=OTHER_ENTITY, created_at=at(minutes=1),
        )
        result = await read_memories(native_ctx(), from_=DAY, exclude=["reflection"])
        assert ids_in_order(result) == [said.id[:8]]
        assert "1 reflection left out" in first_line(result)

    async def test_an_empty_sibling_name_is_not_a_letter(self, db, tools_db):
        """The headers call a row a letter when sibling_session is non-empty;
        the filter must mean the same rows."""
        conversation = await make_conversation(db)
        row = await make_message(db, conversation, content="mine", sibling_session="")
        result = await read_memories(native_ctx(), from_=DAY, exclude=["inter_session"])
        assert ids_in_order(result) == [row.id[:8]]
        assert "You said," in result
        assert "0 inter-session letters" in first_line(result)

    @pytest.mark.parametrize("given", [
        ["reflection", "inter_session"],
        ["inter_session", "reflection", "reflection"],
        [" Reflection ", "INTER_SESSION"],
        "reflection,inter_session",
        '["reflection", "inter_session"]',
    ])
    async def test_accepted_shapes(self, db, tools_db, given):
        rows = await make_day(db, await make_conversation(db))
        result = await read_memories(native_ctx(), from_=DAY, exclude=given)
        assert ids_in_order(result) == short(rows, "ask", "reply", "ask2", "reply2")

    @pytest.mark.parametrize("given", [
        ["reflections"], "letters", ["reflection", "human"], '["reflection"', [["reflection"]],
    ])
    async def test_an_unknown_kind_is_an_error_on_every_reader(self, db, tools_db, given):
        """Ignored, the caller would take the page for one with the kind left out."""
        rows = await make_day(db, await make_conversation(db))
        ctx = native_ctx()
        for result in (
            await read_memories(ctx, from_=DAY, exclude=given),
            await find_memories(ctx, text="harbor", exclude=given),
            await neighbor_memories(ctx, rows["ask"].id, exclude=given),
        ):
            assert result.startswith("Error: Unknown exclude value")
            assert result.endswith("Valid values: reflection, inter_session.")
        assert (await read_memories(ctx, from_=DAY, exclude=5)).startswith("Error: exclude must be a list")
        assert (await read_memories(ctx, from_=DAY, exclude={"reflection": True})).startswith("Error: exclude must be")


class TestPaging:
    async def make_long_day(self, db, turns=8):
        """Each short turn followed by a reflection bigger than a whole page,
        and a run of letters in the middle."""
        conversation = await make_conversation(db)
        talk = []
        for i in range(turns):
            talk.append(await make_message(
                db, conversation, content=prose(30, f"turn {i}"), created_at=at(minutes=10 * i)
            ))
            await make_message(
                db, conversation, role=MessageRole.REFLECTION, content=prose(900, f"account {i}"),
                created_at=at(minutes=10 * i + 1), speaker_entity_id=ENTITY,
            )
        for i in range(5):
            await make_message(
                db, conversation, content=prose(300, f"letter {i}"), sibling_session="Porch",
                created_at=at(minutes=45, seconds=i),
            )
        return talk

    @pytest.mark.parametrize("direction", ["forward", "backward"])
    async def test_excluded_rows_take_no_budget_across_pages(self, db, tools_db, direction):
        talk = await self.make_long_day(db)

        async def read(cursor):
            return await read_memories(
                native_ctx(), from_=DAY, to=DAY, direction=direction, exclude=BOTH,
                page_tokens=READ_PAGE_TOKENS_MIN, cursor=cursor,
            )

        pages = await walk(read)
        assert len(pages) > 1
        # More than one row a page: a counted reflection would fill one alone
        assert all(len(ids_in_order(page)) > 1 for page in pages[:-1])
        ordered = pages if direction == "forward" else list(reversed(pages))
        assert [i for page in ordered for i in ids_in_order(page)] == [m.id[:8] for m in talk]
        for page in pages:
            assert (
                "(exclude on: 8 reflections and 5 inter-session letters left out of the span): "
                "8 messages in the span"
            ) in first_line(page)
            assert len(page.encode("utf-8")) <= READ_PAGE_TOKENS_MIN * RENDERED_CHARS_PER_TOKEN
        shown = len(ids_in_order(pages[0]))
        assert f"{8 - shown} " in pages[0].rsplit("(", 1)[1] and "remain" in pages[0]
        assert f"this page shows {'1–' + str(shown) if direction == 'forward' else f'{9 - shown}–8'}" in first_line(
            pages[0]
        )

    async def test_a_run_of_excluded_rows_longer_than_a_batch(self, db, tools_db):
        conversation = await make_conversation(db)
        first = await make_message(db, conversation, content="before", created_at=at())
        for i in range(230):  # the paging core reads 200 rows at a time
            await make_message(
                db, conversation, role=MessageRole.REFLECTION, content="r",
                created_at=at(seconds=i + 1), speaker_entity_id=ENTITY,
            )
        last = await make_message(db, conversation, content="after", created_at=at(minutes=10))
        result = await read_memories(native_ctx(), from_=DAY, exclude=["reflection"])
        assert ids_in_order(result) == [first.id[:8], last.id[:8]]
        assert "230 reflections left out" in first_line(result)
        assert result.endswith("End of span.")

    def test_the_note_has_its_own_reserve_in_the_page_frame(self):
        """Held back only when the note is there, so a page read without
        exclude holds the rows it always held."""
        counts = {kind: 999_999 for kind in VALID_EXCLUDE_KINDS}
        for role_filter in (None, "human", "ai", "reflection"):
            note = _exclude_suffix(VALID_EXCLUDE_KINDS, counts, "matches", role_filter)
            assert 0 < rendered_tokens(note) <= EXCLUDE_FRAME_TOKENS
        assert _rows_budget(8000, ()) == 8000 - PAGE_FRAME_TOKENS
        assert _rows_budget(8000, VALID_EXCLUDE_KINDS) == 8000 - PAGE_FRAME_TOKENS - EXCLUDE_FRAME_TOKENS

    async def test_a_full_page_with_the_note_stays_within_the_byte_ceiling(self, db, tools_db):
        conversation = await make_conversation(db)
        for i in range(60):
            await make_message(db, conversation, content="prose " * 300, created_at=at(minutes=i))
        page = await read_memories(
            native_ctx(), from_=DAY, exclude=BOTH, page_tokens=READ_PAGE_TOKENS_MAX,
        )
        assert 'cursor="' in page
        assert len(page.encode("utf-8")) <= READ_PAGE_MAX_BYTES


# ============================================================
# memory_find
# ============================================================

class TestFind:
    async def test_the_total_counts_only_what_passes(self, db, tools_db):
        rows = await make_day(db, await make_conversation(db))
        await make_message(
            db, await make_conversation(db), role=MessageRole.REFLECTION, content="about something else",
            speaker_entity_id=ENTITY,
        )
        everything = await find_memories(native_ctx(), text="harbor")
        assert "7 matches;" in first_line(everything) and "exclude" not in everything

        result = await find_memories(native_ctx(), text="harbor", exclude=BOTH)
        assert ids_in_order(result) == short(rows, "ask", "reply", "ask2", "reply2")
        # The reflection without the word is not a match that was left out
        assert first_line(result).startswith(
            'Your archive, messages containing "harbor" (as a phrase, whole words) '
            "(exclude on: 2 reflections and 1 inter-session letter left out of the matches): "
            "4 matches; this page shows 1–4, in order."
        )

    async def test_a_zero_says_whether_it_is_whole(self, db, tools_db):
        conversation = await make_conversation(db)
        await make_message(
            db, conversation, role=MessageRole.REFLECTION, content="only the account says kiln",
            speaker_entity_id=ENTITY,
        )
        result = await find_memories(native_ctx(), text="kiln", exclude=["reflection"])
        assert (
            "(exclude on: 1 reflection left out of the matches): the words appear nowhere in "
            "the archive you can see except in what was left out."
        ) in result
        result = await find_memories(native_ctx(), text="zeppelin", exclude=["reflection"])
        assert (
            "(exclude on: 0 reflections left out of the matches): the words appear nowhere in "
            "the archive you can see."
        ) in result
        plain = await find_memories(native_ctx(), text="zeppelin")
        assert "nowhere in the archive you can see." in plain and "exclude" not in plain

    async def test_pages_resume_with_the_same_exclude(self, db, tools_db):
        conversation = await make_conversation(db)
        talk = []
        for i in range(6):
            talk.append(await make_message(
                db, conversation, content=prose(90, f"harbor {i}"), created_at=at(minutes=2 * i)
            ))
            await make_message(
                db, conversation, role=MessageRole.REFLECTION, content=prose(600, "harbor"),
                created_at=at(minutes=2 * i + 1), speaker_entity_id=ENTITY,
            )

        async def find(cursor):
            return await find_memories(
                native_ctx(), text="harbor", exclude=["reflection"],
                page_tokens=READ_PAGE_TOKENS_MIN, cursor=cursor,
            )

        pages = await walk(find)
        assert len(pages) > 1
        assert [i for page in pages for i in ids_in_order(page)] == [m.id[:8] for m in talk]
        assert all("6 reflections left out of the matches): 6 matches;" in first_line(p) for p in pages)

        past = await find_memories(
            native_ctx(), text="harbor", exclude=["reflection"],
            cursor="2026-09-01T23:00:00+00:00|zzz|page=1",
        )
        assert "(exclude on: 6 reflections left out of the matches) (6 matches in all)" in past


# ============================================================
# memory_neighbors
# ============================================================

class TestNeighbors:
    async def test_before_and_after_count_rows_that_pass(self, db, tools_db):
        rows = await make_day(db, await make_conversation(db))
        result = await neighbor_memories(
            native_ctx(), rows["ask2"].id, before=2, after=1, exclude=BOTH
        )
        assert ids_in_order(result) == short(rows, "ask", "reply", "ask2", "reply2")
        assert "with 2 messages before and 1 after, in order" in first_line(result)
        assert (
            "(exclude on: 2 reflections and 1 inter-session letter left out of the window)."
        ) in first_line(result)
        assert "reached the start and end of the conversation" in first_line(result)

    async def test_default_window_is_unchanged(self, db, tools_db):
        rows = await make_day(db, await make_conversation(db))
        plain = await neighbor_memories(native_ctx(), rows["ask2"].id)
        assert ids_in_order(plain) == short(rows, "reflection", "letter", "ask2", "reflection2", "reply2")
        assert "exclude" not in plain
        assert await neighbor_memories(native_ctx(), rows["ask2"].id, exclude=[]) == plain

    async def test_counts_the_window_not_the_conversation(self, db, tools_db):
        """Rows left out beyond the window's outermost rows are not in its
        count, unless the window reached that edge of the conversation."""
        rows = await make_day(db, await make_conversation(db))
        # reply..ask2 holds one reflection; reflection2 lies past ask2
        result = await neighbor_memories(
            native_ctx(), rows["ask2"].id, before=1, after=0, exclude=["reflection"]
        )
        assert ids_in_order(result) == short(rows, "letter", "ask2")
        assert "0 reflections left out of the window" in first_line(result)
        result = await neighbor_memories(
            native_ctx(), rows["ask2"].id, before=2, after=0, exclude=["reflection"]
        )
        assert ids_in_order(result) == short(rows, "reply", "letter", "ask2")
        assert "1 reflection left out of the window" in first_line(result)
        assert "reached" not in first_line(result)

    async def test_the_edge_reached_counts_through_to_the_conversations_own(self, db, tools_db):
        conversation = await make_conversation(db)
        await make_message(
            db, conversation, role=MessageRole.REFLECTION, content="first of all",
            created_at=at(minutes=-5), speaker_entity_id=ENTITY,
        )
        rows = await make_day(db, conversation)
        result = await neighbor_memories(
            native_ctx(), rows["ask"].id, before=2, after=1, exclude=["reflection"]
        )
        assert ids_in_order(result) == short(rows, "ask", "reply")
        assert "1 reflection left out of the window" in first_line(result)
        assert "reached the start of the conversation" in first_line(result)

        await make_message(
            db, conversation, role=MessageRole.REFLECTION, content="last of all",
            created_at=at(minutes=30), speaker_entity_id=ENTITY,
        )
        result = await neighbor_memories(
            native_ctx(), rows["reply2"].id, before=1, after=2, exclude=["reflection"]
        )
        # reflection2, between the two rows shown, and the one past the last
        assert ids_in_order(result) == short(rows, "ask2", "reply2")
        assert "2 reflections left out of the window" in first_line(result)
        assert "reached the end of the conversation" in first_line(result)

    async def test_the_memory_asked_for_is_shown_whatever_its_kind(self, db, tools_db):
        """A reflection opened onto the exchange it was saved about, without
        the other accounts around it."""
        rows = await make_day(db, await make_conversation(db))
        result = await neighbor_memories(
            native_ctx(), rows["reflection"].id, before=2, after=1, exclude=BOTH
        )
        assert ids_in_order(result) == short(rows, "ask", "reply", "reflection", "ask2")
        assert f"--- >> Memory {rows['reflection'].id[:8]} (You reflected," in result
        # It isn't in its own count
        assert "(exclude on: 0 reflections and 1 inter-session letter left out of the window)" in first_line(result)
        assert "The memory you asked for is a reflection; it is shown whatever exclude leaves out." in first_line(result)

        result = await neighbor_memories(native_ctx(), rows["letter"].id, exclude=["inter_session"])
        assert "The memory you asked for is an inter-session letter;" in first_line(result)
        result = await neighbor_memories(native_ctx(), rows["letter"].id, exclude=["reflection"])
        assert "The memory you asked for" not in result

    async def test_released_rows_are_not_counted_unless_asked(self, db, tools_db):
        rows = await make_day(db, await make_conversation(db))
        rows["reflection"].memory_status = "released"
        db.add(rows["reflection"])
        await db.commit()
        for include_released, count in ((False, "1 reflection "), (True, "2 reflections ")):
            result = await neighbor_memories(
                native_ctx(), rows["ask2"].id, before=10, after=10, exclude=["reflection"],
                include_released=include_released,
            )
            assert f"exclude on: {count}left out" in first_line(result)

    async def test_the_note_counts_against_the_result_budget(self, db, tools_db):
        conversation = await make_conversation(db)
        rows = [
            await make_message(db, conversation, content="x" * 3000, created_at=at(minutes=i))
            for i in range(9)
        ]
        ctx = native_ctx()
        ctx.result_budget_bytes = 12_000
        result = await neighbor_memories(ctx, rows[4].id, before=4, after=4, exclude=BOTH)
        assert len(result.encode("utf-8")) <= 12_000
        assert "exclude on:" in first_line(result)


# ============================================================
# What a filtered read records, and the surfaces that expose it
# ============================================================

class TestRecordingAndSurfaces:
    async def test_rows_left_out_are_not_recorded_as_seen(self, db, tools_db):
        room = await make_conversation(
            db, source=ConversationSource.CLAUDE_CODE.value, external_session_id="sess-1"
        )
        rows = await make_day(db, await make_conversation(db))
        ctx = claude_code_ctx(room.id)
        await read_memories(ctx, from_=DAY, exclude=BOTH)
        talk = {rows[name].id for name in ("ask", "reply", "ask2", "reply2")}
        assert set(ctx.last_query_memory_ids) == talk
        links = (await db.execute(
            select(ConversationMemoryLink.message_id)
            .where(ConversationMemoryLink.conversation_id == room.id)
        )).scalars().all()
        assert set(links) == talk

    async def test_isolated_scope_takes_exclude(self, db, tools_db):
        rows = await make_day(db, await make_conversation(db))
        ctx = native_ctx()
        result = await read_memories(ctx, from_=DAY, exclude=BOTH, scope="isolated")
        assert ids_in_order(result) == short(rows, "ask", "reply", "ask2", "reply2")
        assert not ctx.last_query_memory_ids

    def test_schemas(self):
        for schema in (MEMORY_READ_SCHEMA, MEMORY_FIND_SCHEMA, MEMORY_NEIGHBORS_SCHEMA):
            prop = schema["properties"]["exclude"]
            assert prop["type"] == "array"
            assert prop["items"]["enum"] == list(VALID_EXCLUDE_KINDS)
            assert "default" not in prop and "exclude" not in schema["required"]
        assert "exclude" not in MEMORY_QUERY_SCHEMA["properties"]

    async def test_native_executors_pass_it_through(self, db, tools_db):
        rows = await make_day(db, await make_conversation(db))
        set_memory_tool_context(ENTITY, "current-conversation")
        for result in (
            await memory_tools._memory_read(**{"from": DAY, "exclude": BOTH}),
            await memory_tools._memory_find(text="harbor", exclude=BOTH),
            await memory_tools._memory_neighbors(
                memory_id=rows["ask2"].id, before=10, after=10, exclude=BOTH
            ),
        ):
            assert ids_in_order(result) == short(rows, "ask", "reply", "ask2", "reply2")

    async def test_mcp_round_trip(self, db, async_client):
        room = await make_conversation(
            db, source=ConversationSource.CLAUDE_CODE.value, external_session_id="sess-1"
        )
        rows = await make_day(db, await make_conversation(db))

        for name, arguments in (
            ("memory_read", {"from": DAY}),
            ("memory_find", {"text": "harbor"}),
            ("memory_neighbors", {"memory_id": rows["ask2"].id, "before": 10, "after": 10}),
        ):
            response = await async_client.post("/mcp", json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": {
                    **arguments, "conversation_id": room.id, "scope": "isolated",
                    "exclude": BOTH,
                }},
            })
            payload = response.json()["result"]
            text = payload["content"][0]["text"]
            assert payload["isError"] is False
            assert ids_in_order(text) == short(rows, "ask", "reply", "ask2", "reply2")
            assert "exclude on: 2 reflections and 1 inter-session letter left out" in first_line(text)
