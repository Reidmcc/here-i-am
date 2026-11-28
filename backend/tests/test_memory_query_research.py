"""
memory_query's research options (issue #421): scope="isolated", more
results under it, from/to by creation time, and exclude.

Two layers: the tool (memory_service mocked, so what it hands the search
and what it says can be asserted) and memory_service.search_memories
itself (the Pinecone index mocked), where the role exclusion becomes a
vector-store filter and the date bound is checked after the search with a
deepening fetch.
"""
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.memory_service import MemoryService
from app.services.memory_tools import (
    MAX_ISOLATED_QUERY_RESULTS,
    MemoryToolContext,
    _memory_query,
    is_isolated_read,
    query_memories,
    set_memory_tool_context,
)

pytestmark = pytest.mark.usefixtures("no_memory_links")

# The module, not the package attribute of the same name (the singleton)
memory_service_module = sys.modules[MemoryService.__module__]


# --- The tool -------------------------------------------------------------


def _db_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


def _row(memory_id, created_at="2026-09-10T12:00:00", role="assistant", sibling_session=None):
    return {
        "id": memory_id,
        "content": f"content of {memory_id}",
        "role": role,
        "created_at": created_at,
        "times_retrieved": 3,
        "sibling_session": sibling_session,
    }


def _ctx(**overrides):
    values = dict(
        entity_id="kira",
        conversation_id="this-conversation",
        extra_exclude_ids={"in-view-1"},
        exclude_conversation_after=datetime(2026, 11, 1),
        link_query_results=True,
    )
    values.update(overrides)
    return MemoryToolContext(**values)


class _Service:
    """A mocked memory_service wired with candidates and their SQL rows."""

    def __init__(self, rows, report=None):
        self.rows = {row["id"]: row for row in rows}
        self.report = report
        self.mock = MagicMock()
        self.mock.is_configured.return_value = True
        self.mock.get_archived_conversation_ids = AsyncMock(return_value=set())
        self.mock.update_retrieval_count = AsyncMock(return_value=True)
        self.mock.get_full_memory_content = AsyncMock(side_effect=lambda mid, db: self.rows.get(mid))

        async def search(**kwargs):
            if self.report is not None and kwargs.get("report") is not None:
                kwargs["report"].update(self.report)
            return [
                {"id": mid, "score": 0.8 - i * 0.01, "conversation_id": "other"}
                for i, mid in enumerate(self.rows)
            ]

        self.mock.search_memories = AsyncMock(side_effect=search)

    def search_kwargs(self):
        return self.mock.search_memories.call_args.kwargs


async def _run(service, ctx=None, **kwargs):
    ctx = ctx or _ctx()
    with patch("app.services.memory_tools.memory_service", service.mock), \
         patch("app.services.memory_tools.async_session_maker", return_value=_db_session()):
        result = await query_memories(ctx, **kwargs)
    return result, ctx


class TestIsolatedScope:

    @pytest.mark.asyncio
    async def test_isolated_leaves_nothing_out_and_records_nothing(self):
        service = _Service([_row("mem-a")])
        ctx = _ctx(turn_query_memory_ids={"earlier"})

        result, ctx = await _run(service, ctx, query="the beach", scope="isolated")

        kwargs = service.search_kwargs()
        assert kwargs["exclude_conversation_id"] is None
        assert kwargs["exclude_conversation_after"] is None
        assert kwargs["exclude_ids"] == set()
        assert kwargs["use_cache"] is False
        service.mock.update_retrieval_count.assert_not_called()
        assert ctx.last_query_memory_ids == []
        assert ctx.turn_query_memory_ids == {"earlier"}
        assert "Found 1 memories" in result
        assert "Isolated scope" in result
        assert f"similarity floor is {settings.query_similarity_threshold:g}" in result

    @pytest.mark.asyncio
    async def test_isolated_headers_carry_the_creation_time_not_an_age(self):
        service = _Service([_row("mem-a", created_at="2026-09-10T12:00:00")])

        result, _ = await _run(service, query="q", scope="isolated", tz="UTC")

        assert "--- Memory mem-a (" in result
        assert "2026-09-10 12:00:00 UTC" in result
        assert "days ago" not in result

    @pytest.mark.asyncio
    async def test_the_default_scope_still_tracks_and_excludes(self):
        service = _Service([_row("mem-a")])

        result, ctx = await _run(service, query="q")

        kwargs = service.search_kwargs()
        assert kwargs["exclude_conversation_id"] == "this-conversation"
        assert kwargs["exclude_ids"] == {"in-view-1"}
        assert kwargs["use_cache"] is True
        assert not {"exclude_roles", "created_from", "created_to", "report"} & set(kwargs)
        service.mock.update_retrieval_count.assert_called_once()
        assert ctx.last_query_memory_ids == ["mem-a"]
        assert "Isolated" not in result
        assert result.startswith('Found 1 memories matching: "q"\n')

    @pytest.mark.asyncio
    async def test_isolated_may_ask_for_more_than_ten(self):
        service = _Service([])
        await _run(service, query="q", scope="isolated", num_results=40)
        assert service.search_kwargs()["top_k"] == 80

        await _run(service, query="q", scope="isolated", num_results=500)
        assert service.search_kwargs()["top_k"] == MAX_ISOLATED_QUERY_RESULTS * 2

    @pytest.mark.asyncio
    async def test_more_than_ten_without_the_scope_is_refused_not_clamped(self):
        service = _Service([_row("mem-a")])

        result, ctx = await _run(service, query="q", num_results=40)

        assert result.startswith('Error: num_results above 10 needs scope="isolated"')
        service.mock.search_memories.assert_not_called()
        service.mock.update_retrieval_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_recent_mode_still_clamps(self):
        service = _Service([])
        with patch("app.services.memory_tools._recent_reflections", AsyncMock(return_value="ok")) as recent:
            result, _ = await _run(service, mode="recent", num_results=40)
        assert result == "ok"
        assert recent.call_args.args[1] == 10

    @pytest.mark.asyncio
    async def test_a_failed_search_is_an_error_not_a_zero(self):
        service = _Service([])

        async def failing(**kwargs):
            kwargs["report"]["error"] = "Pinecone unavailable"
            return []
        service.mock.search_memories = AsyncMock(side_effect=failing)

        result, _ = await _run(service, query="the beach", scope="isolated")

        assert result.startswith("Error: the search failed (Pinecone unavailable)")
        assert "No memories found" not in result

    @pytest.mark.asyncio
    async def test_header_only_rows_point_at_an_isolated_read(self):
        service = _Service([_row(f"mem-{i}") for i in range(3)])
        service.rows = {
            mid: {**row, "content": "x" * 5000} for mid, row in service.rows.items()
        }
        ctx = _ctx(result_budget_bytes=6000)

        result, _ = await _run(service, ctx, query="q", scope="isolated")

        assert 'with scope="isolated" so the read is not recorded either]' in result

    @pytest.mark.asyncio
    async def test_unknown_scope_is_refused(self):
        service = _Service([])
        result, _ = await _run(service, query="q", scope="private")
        assert result.startswith("Error: Unknown scope 'private'")
        service.mock.search_memories.assert_not_called()

    def test_reload_parser_sees_an_isolated_query(self):
        assert is_isolated_read({"query": "q", "scope": "isolated"})
        assert not is_isolated_read({"query": "q"})


class TestDateBound:

    @pytest.mark.asyncio
    async def test_bounds_are_read_like_the_readers(self):
        service = _Service([], report={"complete": True, "examined": 12, "depth": 20})

        await _run(service, query="q", **{"from_": "2026-09-01", "to": "2026-09-17"}, tz="UTC")

        kwargs = service.search_kwargs()
        assert kwargs["created_from"] == datetime(2026, 9, 1)
        # `to` is inclusive and a bare date is the end of that day
        assert kwargs["created_to"].date() == datetime(2026, 9, 17).date()
        assert kwargs["created_to"].hour == 23 and kwargs["created_to"].minute == 59
        assert kwargs["use_cache"] is False

    @pytest.mark.asyncio
    async def test_a_zero_under_a_bound_says_what_it_measured(self):
        service = _Service([], report={"complete": True, "examined": 7, "depth": 20})

        result, _ = await _run(service, query="the beach", to="2026-09-17", tz="UTC")

        assert result.startswith('No memories found matching: "the beach", created up to')
        # The default scope left out what is in view and this conversation
        # before searching, so the zero says it is about the rest
        assert (
            "none created in that span scored above the similarity floor "
            f"({settings.query_similarity_threshold:g}) outside your context and this conversation"
        ) in result
        assert "every candidate above the similarity floor" in result

    @pytest.mark.asyncio
    async def test_an_isolated_bounded_zero_claims_the_whole_span(self):
        service = _Service([], report={"complete": True, "examined": 7, "depth": 20})

        result, _ = await _run(service, query="q", to="2026-09-17", tz="UTC", scope="isolated")

        assert "none created in that span scored above the similarity floor" in result
        assert "outside your context" not in result

    @pytest.mark.asyncio
    async def test_a_search_that_stopped_short_says_so(self):
        service = _Service([], report={"complete": False, "examined": 1000, "depth": 1000})

        result, _ = await _run(service, query="q", to="2026-09-17", tz="UTC")

        assert "is among the 1000 nearest candidates" in result
        assert "only the 1000 nearest candidates were checked" in result

    @pytest.mark.asyncio
    async def test_the_row_is_checked_again_in_sql(self):
        service = _Service(
            [
                _row("inside", created_at="2026-09-10T12:00:00"),
                _row("outside", created_at="2026-10-02T12:00:00"),
            ],
            report={"complete": True, "examined": 2, "depth": 20},
        )

        result, _ = await _run(
            service, query="q", **{"from_": "2026-09-01"}, to="2026-09-30", tz="UTC",
        )

        assert "Memory inside" in result
        assert "Memory outside" not in result
        assert "Found 1 memories" in result
        assert ", created between 2026-09-01 00:00:00 UTC and 2026-09-30 23:59:59 UTC" in result

    @pytest.mark.asyncio
    async def test_to_before_from_is_refused(self):
        service = _Service([])
        result, _ = await _run(service, query="q", **{"from_": "2026-09-10"}, to="2026-09-01")
        assert result.startswith("Error: 'to'")
        service.mock.search_memories.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unparseable_bound_is_refused(self):
        service = _Service([])
        result, _ = await _run(service, query="q", to="last tuesday")
        assert result.startswith("Error: Could not parse 'last tuesday'")


class TestExclude:

    @pytest.mark.asyncio
    async def test_kinds_go_to_the_search_as_vector_roles(self):
        service = _Service([])

        result, _ = await _run(service, query="q", exclude=["reflection", "inter_session"])

        assert service.search_kwargs()["exclude_roles"] == ["reflection", "sibling"]
        assert service.search_kwargs()["use_cache"] is False
        assert "exclude on: reflections and inter-session letters left out" in result

    @pytest.mark.asyncio
    async def test_a_kind_source_already_rules_out_is_named_as_that(self):
        service = _Service([])

        result, _ = await _run(service, query="q", source="human", exclude=["reflection"])

        assert "exclude_roles" not in service.search_kwargs()
        assert 'source="human" already leaves out reflections' in result

    @pytest.mark.asyncio
    async def test_reflections_only_and_no_reflections_is_refused(self):
        service = _Service([])
        result, _ = await _run(service, query="q", source="reflection", exclude=["reflection"])
        assert result.startswith("Error: source 'reflection'")
        service.mock.search_memories.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unknown_kind_is_refused(self):
        service = _Service([])
        result, _ = await _run(service, query="q", exclude=["summaries"])
        assert result.startswith("Error: Unknown exclude value 'summaries'")

    @pytest.mark.asyncio
    async def test_the_row_is_checked_again_in_sql(self):
        service = _Service([
            _row("talk"),
            _row("account", role="reflection"),
            _row("letter", sibling_session="Porch"),
        ])

        result, _ = await _run(service, query="q", exclude=["reflection", "inter_session"])

        assert "Memory talk" in result
        assert "Memory account" not in result
        assert "Memory letter" not in result


class TestOtherModes:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["recent", "released"])
    async def test_research_options_are_semantic_only(self, mode):
        service = _Service([])
        result, _ = await _run(service, mode=mode, scope="isolated", exclude=["reflection"])
        assert result.startswith("Error: scope 'isolated', 'exclude' apply to semantic mode only")
        assert f"mode '{mode}' takes 'since'" in result

    @pytest.mark.asyncio
    async def test_since_in_semantic_mode_is_still_refused(self):
        service = _Service([])
        result, _ = await _run(service, query="q", since="2026-09-01")
        assert result == "Error: 'since' applies to modes 'recent' and 'released' only."


class TestNativeExecutor:

    @pytest.mark.asyncio
    async def test_from_arrives_through_the_tool_input(self):
        set_memory_tool_context("kira", "conv")
        service = _Service([], report={"complete": True, "examined": 0, "depth": 20})
        with patch("app.services.memory_tools.memory_service", service.mock):
            await _memory_query(
                **{"query": "q", "from": "2026-09-01", "to": "2026-09-02",
                   "tz": "UTC", "scope": "isolated", "exclude": ["reflection"]}
            )
        kwargs = service.search_kwargs()
        assert kwargs["created_from"] == datetime(2026, 9, 1)
        assert kwargs["exclude_roles"] == ["reflection"]
        assert kwargs["exclude_conversation_id"] is None


# --- memory_service.search_memories ---------------------------------------


def _hit(memory_id, score, created_at="2026-09-10T12:00:00", role="assistant"):
    hit = MagicMock()
    hit.to_dict.return_value = {
        "_id": memory_id,
        "_score": score,
        "fields": {
            "conversation_id": "conv",
            "created_at": created_at,
            "role": role,
            "content_preview": "preview",
            "times_retrieved": 0,
        },
    }
    return hit


def _service(index):
    service = MemoryService()
    cache = MagicMock()
    cache.get_search_results.return_value = None
    service._cache_service = cache
    service._indexes["default"] = index
    return service, cache


def _respond(hits_for_top_k):
    """An index.search whose hits depend on the requested top_k."""
    def search(namespace, query):
        result = MagicMock()
        result.result.hits = hits_for_top_k(query["top_k"])
        return result
    return search


@pytest.fixture
def pinecone_settings():
    with patch("app.services.memory_service.settings") as mock_settings:
        mock_settings.pinecone_api_key = "test-key"
        mock_settings.retrieval_top_k = 5
        mock_settings.similarity_threshold = 0.5
        mock_settings.get_default_entity.return_value = MagicMock(index_name="default")
        yield mock_settings


class TestSearchExcludeRoles:

    def _filter(self, index):
        return index.search.call_args.kwargs["query"].get("filter")

    @pytest.mark.asyncio
    async def test_no_role_filter(self, pinecone_settings):
        index = MagicMock()
        index.search = MagicMock(side_effect=_respond(lambda k: [
            _hit("talk", 0.9), _hit("letter", 0.8, role="sibling"),
        ]))
        service, cache = _service(index)

        results = await service.search_memories("q", exclude_roles=["reflection", "sibling"])

        assert self._filter(index) == {"role": {"$nin": ["reflection", "sibling"]}}
        # The Python fallback drops what the filter would have
        assert [r["id"] for r in results] == ["talk"]
        cache.get_search_results.assert_not_called()
        cache.set_search_results.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_the_ai_filter(self, pinecone_settings):
        index = MagicMock()
        index.search = MagicMock(side_effect=_respond(lambda k: []))
        service, _ = _service(index)

        await service.search_memories("q", role_filter="ai", exclude_roles=["reflection"])

        assert self._filter(index) == {"role": {"$nin": ["human", "reflection"]}}

    @pytest.mark.asyncio
    async def test_without_exclusion_the_filters_are_unchanged(self, pinecone_settings):
        index = MagicMock()
        index.search = MagicMock(side_effect=_respond(lambda k: []))
        service, _ = _service(index)

        await service.search_memories("q", role_filter="ai")

        assert self._filter(index) == {"role": {"$ne": "human"}}


class TestSearchDateBound:

    @pytest.mark.asyncio
    async def test_keeps_only_the_span(self, pinecone_settings):
        index = MagicMock()
        index.search = MagicMock(side_effect=_respond(lambda k: [
            _hit("later", 0.9, created_at="2026-10-05T00:00:00"),
            _hit("inside", 0.8, created_at="2026-09-10T00:00:00"),
            _hit("earlier", 0.7, created_at="2026-08-01T00:00:00"),
            _hit("unstamped", 0.6, created_at=None),
            _hit("faint", 0.1, created_at="2026-09-11T00:00:00"),
        ]))
        service, cache = _service(index)
        report = {}

        results = await service.search_memories(
            "q", top_k=5,
            created_from=datetime(2026, 9, 1), created_to=datetime(2026, 9, 30),
            report=report,
        )

        # An unreadable stamp is kept for the caller's SQL check
        assert [r["id"] for r in results] == ["inside", "unstamped"]
        assert report == {"depth": 10, "examined": 5, "complete": True}
        cache.set_search_results.assert_not_called()

    @pytest.mark.asyncio
    async def test_deepens_until_enough_fall_inside(self, pinecone_settings):
        def hits(k):
            # The nearest are all after the span; the span's rows rank lower
            out = [_hit(f"late-{i}", 0.9, created_at="2026-10-05T00:00:00") for i in range(min(k, 6))]
            inside = [_hit(f"in-{i}", 0.8, created_at="2026-09-10T00:00:00") for i in range(k - len(out))]
            return out + inside
        index = MagicMock()
        index.search = MagicMock(side_effect=_respond(hits))
        service, _ = _service(index)
        report = {}

        results = await service.search_memories(
            "q", top_k=2, created_to=datetime(2026, 9, 30), report=report,
        )

        depths = [c.kwargs["query"]["top_k"] for c in index.search.call_args_list]
        assert depths == [4, 16]
        assert [r["id"] for r in results] == ["in-0", "in-1"]
        assert report == {"depth": 16, "examined": 16, "complete": False}

    @pytest.mark.asyncio
    async def test_stops_at_the_floor(self, pinecone_settings):
        index = MagicMock()
        index.search = MagicMock(side_effect=_respond(lambda k: [
            _hit("late", 0.9, created_at="2026-10-05T00:00:00"),
            _hit("late-2", 0.8, created_at="2026-10-06T00:00:00"),
            _hit("late-3", 0.7, created_at="2026-10-07T00:00:00"),
            _hit("faint", 0.2, created_at="2026-10-08T00:00:00"),
        ]))
        service, _ = _service(index)
        report = {}

        results = await service.search_memories(
            "q", top_k=2, created_to=datetime(2026, 9, 30), report=report,
        )

        assert results == []
        assert index.search.call_count == 1
        assert report["complete"] is True

    @pytest.mark.asyncio
    async def test_stops_at_the_cap(self, pinecone_settings, monkeypatch):
        monkeypatch.setattr(memory_service_module, "MAX_BOUNDED_FETCH", 40)
        index = MagicMock()
        index.search = MagicMock(side_effect=_respond(
            lambda k: [_hit(f"late-{i}", 0.9, created_at="2026-10-05T00:00:00") for i in range(k)]
        ))
        service, _ = _service(index)
        report = {}

        results = await service.search_memories(
            "q", top_k=2, created_to=datetime(2026, 9, 30), report=report,
        )

        depths = [c.kwargs["query"]["top_k"] for c in index.search.call_args_list]
        assert depths == [4, 16, 40]
        assert results == []
        assert report == {"depth": 40, "examined": 40, "complete": False}

    @pytest.mark.asyncio
    async def test_the_floor_is_read_off_the_raw_hits(self, pinecone_settings):
        # The deepest hit falls to the role fallback; it still ranks, and it
        # is below the floor, so a deeper fetch could add nothing
        index = MagicMock()
        index.search = MagicMock(side_effect=_respond(lambda k: [
            _hit("late", 0.9, created_at="2026-10-05T00:00:00"),
            _hit("late-2", 0.8, created_at="2026-10-06T00:00:00"),
            _hit("late-3", 0.7, created_at="2026-10-07T00:00:00"),
            _hit("faint-letter", 0.2, created_at="2026-10-08T00:00:00", role="sibling"),
        ]))
        service, _ = _service(index)
        report = {}

        await service.search_memories(
            "q", top_k=2, created_to=datetime(2026, 9, 30),
            exclude_roles=["sibling"], report=report,
        )

        assert index.search.call_count == 1
        assert report["complete"] is True

    @pytest.mark.asyncio
    async def test_a_failure_is_reported_to_a_caller_that_asked(self, pinecone_settings):
        index = MagicMock()
        index.search = MagicMock(side_effect=RuntimeError("Pinecone unavailable"))
        service, _ = _service(index)
        report = {}

        assert await service.search_memories("q", report=report) == []
        assert report["error"] == "Pinecone unavailable"
        # Without a report it stays the empty list it always was
        assert await service.search_memories("q") == []

    @pytest.mark.asyncio
    async def test_an_unbounded_search_fetches_once(self, pinecone_settings):
        index = MagicMock()
        index.search = MagicMock(side_effect=_respond(
            lambda k: [_hit(f"m-{i}", 0.9) for i in range(k)]
        ))
        service, cache = _service(index)

        results = await service.search_memories("q", top_k=2)

        assert index.search.call_count == 1
        assert len(results) == 2
        cache.set_search_results.assert_called_once()
