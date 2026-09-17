"""
The hooks fit their stdout to the harness's hook-stdout line (fit, then
point).

Claude Code persists hook stdout over 10,000 characters to a file behind a
2 KB preview (measured 2026-09-16 from 1,531 real hook outputs; the hooks'
earlier 18 KB budget sat above the line, so a retrieval block in the
10–18 KB band was printed whole, persisted by the harness, and the
summary-and-pointer design never fired — 150 of 825 retrieval blocks went
that way). Now:

- UserPromptSubmit measures its WHOLE stdout (block plus the lines after
  it) and, when the block is over, prints the memories in rank order whole
  while they fit and the rest as summary lines, with a pointer to the file
  holding the full block. Nothing is cut mid-memory.
- SessionStart spills each bulk part (notes index, reflections) to its own
  file, sized for one Read call, and the pointer names the files, their
  sizes, and the Read tool.
- The budget comes from the backend (inline_budget), HIM_INLINE_BUDGET
  overrides it, and the hooks' default is the fallback.

Both hooks run as subprocesses with hook_util.post_backend stubbed,
mirroring the other hook tests.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
SESSION = "fit-test"
DEFAULT_BUDGET = 9600
HEADER = (
    "[HERE I AM MEMORY RETRIEVAL] Memories from your past conversations "
    "that surfaced as relevant to this prompt:"
)


def _run(script: str, stdin_payload: dict, body: dict, extra_env=None) -> str:
    # The bodies here are tens of kilobytes — past Windows' command-line
    # limit — so the stubbed response travels by file, not inside -c
    env = {**os.environ, **(extra_env or {})}
    body_path = Path(env["TMPDIR"]) / "stub-body.json"
    body_path.write_text(json.dumps(body), encoding="utf-8")
    code = (
        "import io, json, sys\n"
        "import hook_util\n"
        f"body = json.load(open({str(body_path)!r}, encoding='utf-8'))\n"
        "hook_util.post_backend = lambda path, payload, timeout=30: body\n"
        f"sys.stdin = io.StringIO({json.dumps(json.dumps(stdin_payload))})\n"
        f"import {script}\n"
        f"{script}.main()\n"
    )
    env.pop("HIM_DISABLE", None)
    if "HIM_INLINE_BUDGET" not in (extra_env or {}):
        env.pop("HIM_INLINE_BUDGET", None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        cwd=HOOKS_DIR,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    # Windows text-mode stdout writes \r\n; the hook measured \n
    return result.stdout.decode("utf-8").replace("\r\n", "\n")


def harness_len(out: str) -> int:
    """The length the harness measures: as written by a text-mode stdout on
    this platform (the transcripts carry Windows' carriage returns)."""
    return len(out) + (len(os.linesep) - 1) * out.count("\n")


def tmp_env(tmp_path) -> dict:
    return {"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}


def run_prompt_hook(body: dict, tmp_path, extra_env=None) -> str:
    return _run(
        "user_prompt_submit",
        {"session_id": SESSION, "prompt": "hello"},
        body,
        {**tmp_env(tmp_path), **(extra_env or {})},
    )


def run_session_start(body: dict, tmp_path, extra_env=None, source="startup") -> str:
    return _run(
        "session_start",
        {"session_id": SESSION, "source": source, "cwd": str(tmp_path)},
        body,
        {**tmp_env(tmp_path), **(extra_env or {})},
    )


def memory_items(count: int, size: int) -> list:
    items = []
    for index in range(count):
        marker = f"MEMORY-{index}-" + ("m" * (size - 40))
        items.append({
            "id": f"{index:08d}-aaaa-bbbb-cccc-ddddeeeeffff",
            "text": f"[MEMORY {index:08d} from 2026-09-01 - originally from you - via Claude Code]\n{marker}\n[/MEMORY]",
            "summary": f"- {index:08d} (2026-09-01 - originally from you - via Claude Code): MEMORY-{index} snippet",
        })
    return items


def retrieval_body(items: list, **extra) -> dict:
    context = HEADER + "\n\n" + "\n\n".join(item["text"] for item in items)
    return {
        "context": context,
        "context_header": HEADER,
        "context_items": items,
        "context_summary": "(old-style summary)",
        "retrieval_status": "ran",
        **extra,
    }


def spill_files(tmp_path) -> list:
    return sorted(p.name for p in (tmp_path / "here-i-am-sessions").glob("*"))


# --- UserPromptSubmit: fit, then point


def test_small_block_prints_whole_with_no_spill(tmp_path):
    items = memory_items(3, 800)
    out = run_prompt_hook(retrieval_body(items), tmp_path)
    for item in items:
        assert item["text"] in out
    assert "listed by summary line" not in out
    assert spill_files(tmp_path) == []


def test_oversized_block_lands_leading_memories_whole_and_the_rest_by_summary(tmp_path):
    items = memory_items(6, 2800)  # ~17 KB, the band the old budget let through
    out = run_prompt_hook(
        retrieval_body(items, new_sibling_reflections=2, already_in_context=1), tmp_path
    )
    assert harness_len(out) <= DEFAULT_BUDGET
    assert out.startswith(HEADER)
    # The first memories are whole, in rank order; the rest are one line each
    shown = [item for item in items if item["text"] in out]
    assert shown == items[: len(shown)]
    assert 2 <= len(shown) < 6
    for item in items[len(shown):]:
        assert item["summary"] in out
        assert "MEMORY-" + item["id"][:8].lstrip("0") not in out or item["summary"] in out
    assert f"{6 - len(shown)} more surfaced, listed by summary line" in out
    assert f"{len(shown)} of the 6 retrieved memories are shown above in full" in out
    # Nothing is cut mid-memory: every whole marker closes
    assert out.count("[/MEMORY]") == len(shown)
    # The full block is in the file the pointer names, and the pointer says Read
    files = spill_files(tmp_path)
    assert len(files) == 1 and files[0].startswith(f"{SESSION}-retrieval-")
    spilled = (tmp_path / "here-i-am-sessions" / files[0]).read_text(encoding="utf-8")
    assert spilled == retrieval_body(items)["context"]
    assert files[0] in out
    assert "Read tool" in out
    # The lines after the block survive
    assert "2 reflections saved in other sessions" in out
    assert "in-context verbatim held 1 slot" in out
    assert "[WAKEUP]" in out


def test_the_whole_stdout_is_measured_not_just_the_block(tmp_path):
    # A block that fits alone but not with the tail after it is fitted,
    # so the total stays under the line
    items = memory_items(4, 2300)  # ~9.3 KB block; the tail pushes it over
    out = run_prompt_hook(retrieval_body(items, new_sibling_reflections=3), tmp_path)
    assert harness_len(out) <= DEFAULT_BUDGET
    assert "listed by summary line" in out


def test_sweep_never_writes_past_the_budget(tmp_path):
    """The reviewer's sweep: six-memory blocks over a fine ladder of item
    sizes, the whole stdout measured as written. The fit's reserves are
    exact (the pointer is built before the fit), so no size lands over the
    budget — pinned where it bites, at every granularity, not at one."""
    script = tmp_path / "sweep.py"
    script.write_text(
        "import io, json, os, sys\n"
        f"sys.path.insert(0, {str(HOOKS_DIR)!r})\n"
        "import hook_util\n"
        "import user_prompt_submit\n"
        f"HEADER = {HEADER!r}\n"
        "def items(size):\n"
        "    out = []\n"
        "    for i in range(6):\n"
        "        body = f'MEMORY-{i}-' + 'm' * (size - 40)\n"
        "        out.append({'id': f'{i:08d}-aaaa', 'text': f'[MEMORY {i:08d} from 2026-09-01 - originally from you - via Claude Code]\\n{body}\\n[/MEMORY]', 'summary': f'- {i:08d} (2026-09-01 - originally from you - via Claude Code): MEMORY-{i} snippet'})\n"
        "    return out\n"
        "worst = (0, 0)\n"
        "for size in range(600, 3400, 37):\n"
        "    its = items(size)\n"
        "    body = {'context': HEADER + '\\n\\n' + '\\n\\n'.join(i['text'] for i in its), 'context_header': HEADER, 'context_items': its, 'retrieval_status': 'ran', 'new_sibling_reflections': 3, 'already_in_context': 2, 'in_context_reflections_skipped': 1}\n"
        "    hook_util.post_backend = lambda path, payload, timeout=30, body=body: body\n"
        "    sys.stdin = io.StringIO(json.dumps({'session_id': 'sweep', 'prompt': 'hello'}))\n"
        "    buf = io.StringIO()\n"
        "    real = sys.stdout\n"
        "    sys.stdout = buf\n"
        "    try:\n"
        "        user_prompt_submit.main()\n"
        "    finally:\n"
        "        sys.stdout = real\n"
        "    written = hook_util.output_chars(buf.getvalue())\n"
        "    if written > worst[0]:\n"
        "        worst = (written, size)\n"
        "print(json.dumps({'worst': worst[0], 'at': worst[1], 'budget': hook_util.inline_budget()}))\n",
        encoding="utf-8",
    )
    env = {**os.environ, **tmp_env(tmp_path)}
    env.pop("HIM_DISABLE", None)
    env.pop("HIM_INLINE_BUDGET", None)
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, cwd=HOOKS_DIR, env=env, timeout=120
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    report = json.loads(result.stdout.decode("utf-8").strip().splitlines()[-1])
    assert report["budget"] == DEFAULT_BUDGET
    assert report["worst"] <= DEFAULT_BUDGET, report
    # And the fit is using the channel, not hiding under it
    assert report["worst"] >= DEFAULT_BUDGET * 0.8, report


def test_nothing_fits_in_full_lists_everything_by_summary(tmp_path):
    items = memory_items(3, 5000)
    out = run_prompt_hook(retrieval_body(items), tmp_path, extra_env={"HIM_INLINE_BUDGET": "1200"})
    assert harness_len(out) <= 1200
    assert "are listed above by summary line" in out
    for item in items:
        assert item["summary"] in out
        assert item["text"] not in out


def test_budget_from_the_backend_then_env_override(tmp_path):
    items = memory_items(6, 2800)
    out = run_prompt_hook(retrieval_body(items, inline_budget=4000), tmp_path)
    assert harness_len(out) <= 4000
    assert "listed by summary line" in out.lower()
    out = run_prompt_hook(
        retrieval_body(items, inline_budget=4000), tmp_path, extra_env={"HIM_INLINE_BUDGET": "2500"}
    )
    assert harness_len(out) <= 2500


def test_backend_without_items_falls_back_to_the_summary_block(tmp_path):
    body = {
        "context": "[MEMORY] " + ("x" * 30000),
        "context_summary": "[HERE I AM MEMORY RETRIEVAL] 1 memory ...\n- abc12345: snippet",
        "retrieval_status": "ran",
    }
    out = run_prompt_hook(body, tmp_path)
    assert "too large to inject inline" in out
    assert "- abc12345: snippet" in out
    assert "xxxxx" not in out
    assert "Read tool" in out


# --- SessionStart: one file per bulk part


def session_body(context: str, parts: list, created=True) -> dict:
    return {
        "conversation_id": "conv-1",
        "entity_id": "test-entity",
        "entity_label": "Test Entity",
        "created": created,
        "context": context,
        "bulk_context": "\n\n".join(text for _, text in parts),
        "bulk_parts": [{"name": name, "text": text} for name, text in parts],
        "rooms_notice": "[ROOMS REGISTRY] this session is the porch",
    }


def test_small_session_start_prints_inline(tmp_path):
    out = run_session_start(
        session_body("[HERE I AM] You are Test Entity.", [("notes-index", "index"), ("reflections", "refl")]),
        tmp_path,
    )
    assert "[HERE I AM] You are Test Entity." in out
    assert "index" in out and "refl" in out
    assert "[ROOMS REGISTRY]" in out
    assert spill_files(tmp_path) == []


def test_oversized_bulk_goes_to_one_file_per_part_with_a_read_pointer(tmp_path):
    context = "[HERE I AM] You are Test Entity. " + ("c" * 3000)
    index = "[NOTES INDEX]\n" + ("i" * 40000)
    reflections = "[RECENT REFLECTIONS]\n" + ("r" * 30000)
    out = run_session_start(
        session_body(context, [("notes-index", index), ("reflections", reflections)]), tmp_path
    )
    assert harness_len(out) <= DEFAULT_BUDGET
    assert out.startswith(context)
    files = spill_files(tmp_path)
    assert files == [
        f"{SESSION}-session-start-notes-index.md",
        f"{SESSION}-session-start-reflections.md",
    ]
    directory = tmp_path / "here-i-am-sessions"
    assert (directory / files[0]).read_text(encoding="utf-8") == index
    assert (directory / files[1]).read_text(encoding="utf-8") == reflections
    for name in files:
        assert name in out
    assert "(39 KB)" in out and "(29 KB)" in out
    assert "Read each of those files now" in out
    assert "Read tool" in out
    assert "not a shell cat" in out
    # The rooms line comes after the pointer
    assert out.index("[ROOMS REGISTRY]") > out.index("Read each of those files")
    assert "iiiii" not in out and "rrrrr" not in out


def test_post_compaction_files_are_named_for_it(tmp_path):
    out = run_session_start(
        session_body("[HERE I AM] compacted", [("notes-index", "i" * 20000)], created=False),
        tmp_path,
        source="compact",
    )
    assert spill_files(tmp_path) == [f"{SESSION}-post-compact-notes-index.md"]
    assert "post-compact-notes-index.md" in out


def test_backend_without_parts_spills_the_joined_bulk_to_one_file(tmp_path):
    body = session_body("[HERE I AM] You are Test Entity.", [("notes-index", "i" * 20000)])
    del body["bulk_parts"]
    out = run_session_start(body, tmp_path)
    assert spill_files(tmp_path) == [f"{SESSION}-session-start-bulk.md"]
    assert "session-start-bulk.md" in out


def test_identity_block_over_the_line_is_pointed_at_first(tmp_path):
    # A long system prompt can put the inline block itself over the line;
    # the pointer then comes first so the harness's preview carries it,
    # and the block is filed as well
    context = "[HERE I AM] You are Test Entity. " + ("p" * 12000)
    out = run_session_start(session_body(context, [("reflections", "r" * 20000)]), tmp_path)
    assert out.startswith("[HERE I AM] Your identity block itself was too large")
    assert f"{SESSION}-session-start-identity.md" in spill_files(tmp_path)
    assert context in out  # still printed, after the pointer
