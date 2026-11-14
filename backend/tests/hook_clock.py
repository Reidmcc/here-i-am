"""
Shared by the UserPromptSubmit hook tests: the clock line the hook prints
first on every path (hook_util.clock_line).

The hook tests run the hook as a subprocess and compare what it printed.
The clock line is the one part of that output that differs from run to
run, so the tests pin it (PIN_CLOCK, a line for the subprocess's stub
code) and take it off the front with without_clock — which also asserts it
was there, so every hook test doubles as a check that its path carries the
time.
"""

PINNED_CLOCK = (
    "[HERE I AM CLOCK] Saturday 2026-11-14 15:03 EST (UTC-05:00), when this "
    "prompt arrived."
)

# Goes after `import hook_util` in a test's stub code. One pinned line
# whatever the event: the SessionStart hook prints it too, after a
# compaction, and the tests that pin compare two runs of the same hook
PIN_CLOCK = f"hook_util.clock_line = lambda now=None, event=None: {PINNED_CLOCK!r}\n"


def without_clock(out: str) -> str:
    """
    The hook's stdout after its clock line, asserting the pinned line opens
    the output with a blank line after it.
    """
    out = out.replace("\r\n", "\n")
    opening = PINNED_CLOCK + "\n\n"
    assert out.startswith(opening), out[:300]
    return out[len(opening):]
