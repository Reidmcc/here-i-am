"""
What Claude Code does with content bound for a session's context — the
measured limits of every channel Here I Am pushes through, in one place.

Every producer of context in Claude Code mode budgets against the channel it
uses, lands whole units up to that limit, and points at the rest ("fit,
then point"). The alternative is the harness's own handling: a hook's
stdout or a tool result over its line is persisted to a file with a 2 KB
preview inline, which cuts mid-unit, announces itself only as "Output too
large", and costs Read calls to get back (issue #353 for the archive
readers; the same shape for the hooks and the other memory tools here).

Four channels, measured 2026-09-16 on this Claude Code build. Each constant
carries its bracket and the recipe that re-measures it; a future
re-measurement is a constant edit that test_harness_limits checks.

- HOOK STDOUT (SessionStart, UserPromptSubmit): persisted above 10,000
  CHARACTERS — characters, not bytes: 9,997 chars (10,069 UTF-8 bytes)
  landed and 10,009 chars (10,063 bytes) were persisted. Bracketed from
  1,531 real hook outputs across every transcript on the machine (150 of
  825 retrieval blocks had been persisted; the hooks' earlier 18 KB budget
  was set against an "observed ~20 KB cap" that was a tool-result number).
  The count is of the text as written: a text-mode stdout on Windows
  writes \\r\\n, the stored transcripts carry the carriage returns, and
  the hooks count them (hook_util.output_chars).
  Re-measure: scan <config dir>/projects/*/*.jsonl for "attachment" rows
  with a hook stdout, and compare len(stdout) against whether the row's
  content is a <persisted-output> notice.
- TOOL RESULT (Bash, MCP): persisted above about 50 KB in UTF-8 bytes
  (48,365 landed; 51,286 persisted); an MCP result above ~25k tokens by
  the harness's counter is refused outright ("exceeds maximum allowed
  tokens"), and that counter ran at 2.84 chars/token on real archive
  prose (93,521 chars counted as 32,982 tokens), about 1.45× tiktoken.
  Re-measure: one memory_read(scope="isolated", max_pages=1) per size;
  landed vs persisted is the readout, and Read on an over-cap spill file
  prints the counter's number.
- THE READ TOOL never persists: above ~25k tokens by the same counter it
  returns a PARTIAL VIEW with offset paging and a notice ("showing lines
  1-360 of 718, 42310 tokens, cap 25000"); a 56 KB file landed whole. So
  a spill file is read back with the Read tool, never a shell cat — a cat
  result is a tool result and goes to disk over 50 KB.

One more line bounds the context itself rather than a channel into it:

- AUTO-COMPACTION (issue #365): fires at the auto-compact WINDOW minus
  min(max output tokens, 20,000) minus a 13,000-token buffer — read from
  the Claude Code 2.1.280 binary (2026-09-24): the window is
  CLAUDE_CODE_AUTO_COMPACT_WINDOW, else the `autoCompactWindow` setting,
  else the model default, and never above the model's own context size;
  the effective window subtracts the output reserve, and the compact
  level starts 13,000 under that. Every current model's max output is
  over 20,000, so the line is the window less 33,000. Confirmed against
  the record: every auto compaction in the transcripts on this machine
  (`compactMetadata.preTokens`) fired between 964,200 and 972,158 tokens
  with the 1M default and between 466,252 and 473,829 in the rooms whose
  settings set 500,000 — within a few thousand either side of the line,
  since the harness compares its own estimate of the next prompt (one
  outlier at 997,467: the check runs between requests, so a large tool
  result can carry a turn past the line).
  Re-measure: grep the executable for "autoCompactWindow" and follow the
  functions that read it (the threshold subtracts a literal 13e3 from the
  effective window); then scan <config dir>/projects/*/*.jsonl for
  "compactMetadata" with "trigger":"auto" and compare preTokens with the
  line computed for that session's window.
"""
from typing import List, Sequence, Tuple

# --- Hook stdout ----------------------------------------------------------

HOOK_STDOUT_PERSIST_CHARS = 10_000
# The budget the hooks print against: 4% under the line. The backend hands
# it to the hooks in every SessionStart / retrieve response (inline_budget)
# so the two can't disagree; hook_util carries the same number as the
# fallback for a backend that predates the field, and HIM_INLINE_BUDGET
# overrides both.
HOOK_INLINE_BUDGET_CHARS = 9_600

# --- Tool results (Bash, MCP) ---------------------------------------------

HARNESS_PERSIST_BYTES = 51_200
HARNESS_RESULT_CAP_TOKENS = 25_000
HARNESS_CHARS_PER_TOKEN = 2.8
# The budget a fitted tool result is rendered to: an eighth under the
# persist line (the same ceiling memory_read's maximum page renders within,
# READ_PAGE_MAX_BYTES). memory_query and memory_neighbors fit to it.
TOOL_RESULT_BUDGET_BYTES = int(HARNESS_PERSIST_BYTES * 0.875)

# --- The Read tool --------------------------------------------------------

READ_TOOL_CAP_TOKENS = 25_000
# A spill file this size or smaller lands in one Read call (at the
# counter's ratio, with a margin for prose that tokenizes heavier); larger
# files still arrive whole, in partial views the entity pages through. A
# documented sizing target, not an enforced one: nothing splits a bulk
# part against it — the session-start pointer states each file's size, and
# a part over it (a notes index that has grown past ~60 KB) costs a second
# Read page rather than anything lost.
READ_TOOL_ONE_CALL_BYTES = int(READ_TOOL_CAP_TOKENS * HARNESS_CHARS_PER_TOKEN * 0.85)

# --- Auto-compaction ------------------------------------------------------

# The window when neither the environment nor the settings name one: the
# current models' 1M context (the 09-09 measurement, ~967k → ~10k)
AUTO_COMPACT_DEFAULT_WINDOW_TOKENS = 1_000_000
# What the harness holds back under the window before it compacts: the
# output reserve (min(max output, 20,000)) plus the compact buffer
AUTO_COMPACT_OUTPUT_RESERVE_TOKENS = 20_000
AUTO_COMPACT_BUFFER_TOKENS = 13_000
AUTO_COMPACT_RESERVE_TOKENS = AUTO_COMPACT_OUTPUT_RESERVE_TOKENS + AUTO_COMPACT_BUFFER_TOKENS


def auto_compact_line(window: int) -> int:
    """The prompt size at which auto-compaction fires for a given window."""
    return window - AUTO_COMPACT_RESERVE_TOKENS


def fit_prefix(full_sizes: Sequence[int], pointer_sizes: Sequence[int], budget: int) -> int:
    """
    How many leading items to render in full so that the whole rendering —
    those in full, the rest as pointers — fits `budget`.

    Items are in priority order; each is rendered whole or as its pointer,
    never cut. Returns the largest k such that sum(full[:k]) +
    sum(pointer[k:]) <= budget, or 0 when even the all-pointer rendering
    is over (the caller then renders pointers alone; a pointer is small,
    so that case means the budget is wrong, not the content). A pointer
    never costs more than the full rendering, so the total is monotonic
    in k and the answer is the first k where it stops fitting.
    """
    n = len(full_sizes)
    if n != len(pointer_sizes):
        raise ValueError("full_sizes and pointer_sizes must align")
    total = sum(pointer_sizes)
    if total > budget:
        return 0
    kept = 0
    for full, pointer in zip(full_sizes, pointer_sizes, strict=True):
        total += max(0, full - pointer)
        if total > budget:
            break
        kept += 1
    return kept


def fit_by_priority(
    full_sizes: Sequence[int],
    pointer_sizes: Sequence[int],
    priority: Sequence[int],
    budget: int,
) -> List[bool]:
    """
    fit_prefix for items whose priority differs from their display order:
    `priority` lists indexes in the order they should be promoted to full
    rendering (memory_neighbors promotes the target first, then outward).
    Returns a flag per item, True when it renders in full.
    """
    ordered_full = [full_sizes[i] for i in priority]
    ordered_pointer = [pointer_sizes[i] for i in priority]
    kept = fit_prefix(ordered_full, ordered_pointer, budget)
    flags = [False] * len(full_sizes)
    for i in priority[:kept]:
        flags[i] = True
    # An item whose full rendering is no larger than its pointer (a row the
    # reader already lists as in context, a one-line message) costs nothing
    # to show in full, so it always is — a pointer would send the entity to
    # open something smaller than the pointer
    for i, (full, pointer) in enumerate(zip(full_sizes, pointer_sizes, strict=True)):
        if full <= pointer:
            flags[i] = True
    return flags


def utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def fit_report(shown: int, total: int, unit: str = "memories") -> Tuple[int, str]:
    """(omitted count, one sentence) for a header when a fit left items as pointers."""
    omitted = total - shown
    if omitted <= 0:
        return 0, ""
    return omitted, (
        f" {omitted} of the {total} {unit} are listed by header only because the "
        "full result would exceed what this harness shows in one tool result; "
        "open any of them with memory_neighbors or memory_read."
    )
