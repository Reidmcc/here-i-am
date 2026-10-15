"""
Every hook's worst case fits its hooks.json budget (issue #400).

The harness kills a hook that outlives its `timeout`, mid-notice, so the
one thing a hook must always manage, saying what happened, depends on its
requests running out before the harness's clock does. hook_util keeps each
request's deadlines (RequestTimeouts) and the requests each hook can make
in its worst case (TIMEOUTS_BY_HOOK); this sums them against hooks.json,
with a margin for the process start and the hook's own work (reading the
transcript, the session records).
"""
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402


def _budgets():
    config = json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))
    budgets = {}
    for event, groups in config["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                budgets[event] = hook["timeout"]
    return budgets


def _margin(budget):
    return max(1.0, 0.1 * budget)


def test_every_registered_hook_has_its_requests_named():
    assert set(_budgets()) == set(hook_util.TIMEOUTS_BY_HOOK)


@pytest.mark.parametrize("event", sorted(hook_util.TIMEOUTS_BY_HOOK))
def test_the_worst_case_fits_the_budget(event):
    budget = _budgets()[event]
    worst = sum(t.worst_case() for t in hook_util.TIMEOUTS_BY_HOOK[event])
    assert worst <= budget - _margin(budget), (
        f"{event}: worst case {worst:g} s does not fit {budget} s with a "
        f"{_margin(budget):g} s margin"
    )


def test_the_worst_case_counts_every_attempt_and_pause():
    timeouts = hook_util.RequestTimeouts(read=10, connect=3, attempts=2)
    assert timeouts.worst_case() == 2 * 3 + hook_util.RETRY_PAUSE + 10
    assert hook_util.RequestTimeouts(read=10, connect=3, attempts=1).worst_case() == 13


def test_a_check_after_a_failure_is_tried_once():
    # A backend that just failed to answer is not one to wait on twice
    assert hook_util.RECORDED_TIMEOUTS.attempts == 1
