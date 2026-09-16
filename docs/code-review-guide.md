# Guide for agents conducting code review sessions

## Read first, in this order, before forming a view

1. The issue the PR closes (linked in the PR body), whole: the spec and its reasons.
2. The PR body and every commit message: what the author says it does and why.
3. The diff, whole, every file, with `gh pr diff <N>`. Not the summary of it.
4. The tests the PR adds or changes, and how the touched code is used elsewhere (grep for the callers). `CLAUDE.md` and `docs/claude-code-mode.md` carry the house's conventions; hold the diff against them.
5. Your own memory, once: `memory_query` for the issue's subject, so you know what the house has already decided and don't re-litigate a ruling.

## Review at these altitudes, in this order of weight

- **Correctness.** Does the code do what the issue says, in every case the issue names? Where doesn't it? A finding here needs the concrete input and the wrong result, not a feeling.
- **Behavior nobody asked for.** What else changed: a default that moved, a path that now records, retrieves, deletes, or exposes something it didn't before. The archive's purity rules (only the talk enters it; hooks never create rows; released stays released; archived stays hidden; nothing inferred onto old rows) are the ones to check hardest.
- **Tests.** Which case the issue names has no test; which test asserts something weaker than the PR body claims.
- **Shape.** A simpler implementation of the same behavior; duplication of something the repo already has; a name that says less than it should; a parameter that wants to be an enum, or the reverse. Reuse over new machinery.
- **Docs.** Do `tools.md`, `claude-code-mode.md`, `CLAUDE.md` say what the code now does, in the house's voice, with the reason?

## Rules of the room

- Verify before you assert. Run the tests from the dev checkout's venv (pytest, ruff). If you claim a case fails, show the case. If you're not sure, say "not sure" and what would settle it.
- Rank findings by whether they change behavior, then by cost to fix. Three real findings beat twelve nits; nits go in one line at the end or not at all.
- Every finding names the file and line and says what you'd do instead, in one sentence.
- Post the findings as review comments on the PR (`gh pr review <N> --comment --body-file <file>`, or inline where the harness's review tooling supports it), so the ledger lives where the code is. Write the body to a scratchpad file first; the Bash wrapper mangles backticks in heredocs.
- Then send one short letter to the author workshop (address in `rooms.md`, or find it by the PR's branch name in the session list) saying the review is up and what the one thing you'd fix first is. Then stop. The author answers each finding in the PR, taken or declined with a reason; Pseudo reads the PR after that exchange. Don't wait for replies unless one arrives.
- Never push to the author's branch. Never merge. Never touch `E:\here-i-am` (the live server directory; standing agreement, every room).
- Read the clock before you write a time. This session runs at High effort.

## What done looks like

The PR thread holds two or three findings with file and line, each answered by the author; the tests either passed or the failing case is named; and Pseudo opens a PR that two of you have already argued about.