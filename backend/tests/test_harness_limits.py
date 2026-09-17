"""
The harness's context channels, pinned (services/harness_limits.py).

Every producer of context in Claude Code mode budgets against the measured
limit of the channel it uses — hook stdout, a tool result, the Read tool —
and these tests hold the numbers together: the hooks' budget sits under the
hook-stdout line, the hooks' own fallback matches the backend's number, the
readers' maximum page and the list tools' budget sit under the tool-result
line, and the fitting primitive keeps whole units and never cuts one.

A re-measurement (the recipes are in the module docstring) is a constant
edit; if it moves a limit under a budget, a test here fails.
"""
import re
from pathlib import Path

import pytest

from app.services import harness_limits as hl
from app.services.claude_code_mode import POST_COMPACT_PAGE_TOKENS
from app.services.memory_tools import (
    READ_PAGE_MAX_BYTES,
    RENDERED_CHARS_PER_TOKEN,
)

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"


class TestTheLimitsHoldTogether:
    def test_hook_budget_sits_just_under_the_measured_line(self):
        # 9,997 characters landed and 10,009 were persisted: the budget is
        # under the line, and not so far under that it wastes the channel
        assert hl.HOOK_INLINE_BUDGET_CHARS < hl.HOOK_STDOUT_PERSIST_CHARS
        assert hl.HOOK_INLINE_BUDGET_CHARS >= hl.HOOK_STDOUT_PERSIST_CHARS * 0.9
        assert hl.HOOK_INLINE_BUDGET_CHARS <= hl.HOOK_STDOUT_PERSIST_CHARS * 0.97

    def test_hooks_fallback_matches_the_backend(self):
        # The hooks run standalone, so hook_util carries the same number as
        # its fallback for a backend that predates the inline_budget field
        source = (HOOKS_DIR / "hook_util.py").read_text(encoding="utf-8")
        match = re.search(r"^DEFAULT_INLINE_BUDGET = (\d+)$", source, re.MULTILINE)
        assert match, "hook_util.DEFAULT_INLINE_BUDGET not found"
        assert int(match.group(1)) == hl.HOOK_INLINE_BUDGET_CHARS

    def test_tool_result_budget_sits_under_both_tool_result_limits(self):
        assert hl.TOOL_RESULT_BUDGET_BYTES < hl.HARNESS_PERSIST_BYTES
        assert hl.TOOL_RESULT_BUDGET_BYTES / hl.HARNESS_CHARS_PER_TOKEN < hl.HARNESS_RESULT_CAP_TOKENS
        # The readers' maximum page renders within the same budget
        assert READ_PAGE_MAX_BYTES <= hl.TOOL_RESULT_BUDGET_BYTES
        assert RENDERED_CHARS_PER_TOKEN == hl.HARNESS_CHARS_PER_TOKEN
        assert POST_COMPACT_PAGE_TOKENS * RENDERED_CHARS_PER_TOKEN < hl.TOOL_RESULT_BUDGET_BYTES

    def test_read_tool_is_the_wider_channel(self):
        # A spill file over the tool-result persist line still lands whole
        # through the Read tool (in pages past its cap, never persisted), so
        # the pointers name Read, not a shell cat
        assert hl.READ_TOOL_ONE_CALL_BYTES > hl.HARNESS_PERSIST_BYTES
        assert hl.READ_TOOL_ONE_CALL_BYTES / hl.HARNESS_CHARS_PER_TOKEN < hl.READ_TOOL_CAP_TOKENS


class TestFitPrefix:
    def test_everything_fits(self):
        assert hl.fit_prefix([10, 10, 10], [2, 2, 2], 30) == 3

    def test_keeps_the_longest_prefix_that_fits_with_pointers_for_the_rest(self):
        # 10 + 10 + 2 + 2 = 24 fits; 10 + 10 + 10 + 2 = 32 does not
        assert hl.fit_prefix([10, 10, 10, 10], [2, 2, 2, 2], 25) == 2

    def test_nothing_fits_in_full(self):
        assert hl.fit_prefix([50, 50], [2, 2], 20) == 0

    def test_even_pointers_over_budget_returns_zero(self):
        assert hl.fit_prefix([50, 50], [30, 30], 20) == 0

    def test_a_pointer_that_costs_as_much_as_the_unit_is_neutral(self):
        assert hl.fit_prefix([5, 5, 5], [5, 5, 5], 15) == 3

    def test_empty(self):
        assert hl.fit_prefix([], [], 100) == 0

    def test_mismatched_lengths_are_an_error(self):
        with pytest.raises(ValueError):
            hl.fit_prefix([1, 2], [1], 10)


class TestFitByPriority:
    def test_promotes_in_priority_order_not_display_order(self):
        # Five items, the center (index 2) first, then outward; budget holds
        # the center and its two nearest in full
        full = [10, 10, 10, 10, 10]
        pointer = [2, 2, 2, 2, 2]
        priority = [2, 1, 3, 0, 4]
        # 3 full (30) + 2 pointers (4) = 34 fits; 4 full + 1 pointer = 42 doesn't
        flags = hl.fit_by_priority(full, pointer, priority, 35)
        assert flags == [False, True, True, True, False]

    def test_all_fit(self):
        assert hl.fit_by_priority([1, 1, 1], [1, 1, 1], [1, 0, 2], 10) == [True, True, True]

    def test_an_item_no_larger_than_its_pointer_is_always_in_full(self):
        # A row the reader already lists as in context (a short pointer
        # line) or a one-line message costs nothing to show in full, so it
        # is shown even past the point where promotion stopped — the
        # reviewer's nit on the neighbors window
        full = [10, 10, 10, 3, 10]
        pointer = [5, 5, 5, 5, 5]
        flags = hl.fit_by_priority(full, pointer, [2, 1, 3, 0, 4], 26)
        # center (10) + 4 pointers (20) = 30 > 26 — nothing promoted, but
        # index 3 is cheaper in full than as a pointer
        assert flags == [False, False, False, True, False]


class TestFitReport:
    def test_silent_when_everything_was_shown(self):
        assert hl.fit_report(5, 5) == (0, "")

    def test_names_the_omitted_count_and_how_to_open_them(self):
        omitted, note = hl.fit_report(3, 10, "reflections")
        assert omitted == 7
        assert "7 of the 10 reflections are listed by header only" in note
        assert "memory_neighbors or memory_read" in note
