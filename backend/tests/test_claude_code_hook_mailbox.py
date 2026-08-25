"""
The UserPromptSubmit hook's sibling-reflections mailbox flag.

A long-running Claude Code session cannot see reflections that concurrent
sessions save, and unretrieved history and genuine novelty feel identical
from inside — so when /retrieve reports new sibling reflections, the hook
must print a one-line notice (never the content: pulling it is the
entity's call, via memory_query mode "recent"). The notice must appear in
every output branch: alongside inline context, after a spill pointer, and
alone when retrieval returned nothing.

The hook runs as a subprocess with hook_util.post_backend stubbed, so the
real print/branch structure is exercised without a backend.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"

STDIN_PAYLOAD = json.dumps({"session_id": "hook-test-session", "prompt": "hello"})


def run_hook_with_response(body: dict, extra_env: dict = None) -> str:
    """Run user_prompt_submit.main() with post_backend stubbed to return body."""
    code = (
        "import io, json, sys\n"
        "import hook_util\n"
        f"body = json.loads({json.dumps(json.dumps(body))})\n"
        "hook_util.post_backend = lambda path, payload, timeout=30: body\n"
        f"sys.stdin = io.StringIO({json.dumps(STDIN_PAYLOAD)})\n"
        "import user_prompt_submit\n"
        "user_prompt_submit.main()\n"
    )
    env = {**os.environ, **(extra_env or {})}
    env.pop("HIM_DISABLE", None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        cwd=HOOKS_DIR,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("utf-8")


def test_notice_alone_when_no_context():
    out = run_hook_with_response({"context": "", "new_sibling_reflections": 2})
    assert "2 reflections saved in other sessions" in out
    assert 'mode="recent"' in out


def test_notice_appended_to_inline_context():
    out = run_hook_with_response(
        {"context": "[MEMORY] something", "new_sibling_reflections": 1}
    )
    assert "[MEMORY] something" in out
    assert "1 reflection saved in other sessions" in out
    # Singular form for a single reflection
    assert "1 reflections" not in out


def test_no_notice_when_count_zero():
    out = run_hook_with_response(
        {"context": "[MEMORY] something", "new_sibling_reflections": 0}
    )
    assert "[MEMORY] something" in out
    assert "saved in other sessions" not in out


def test_no_notice_when_field_absent():
    # An older backend without the field must not break the hook
    out = run_hook_with_response({"context": "[MEMORY] something"})
    assert "[MEMORY] something" in out
    assert "saved in other sessions" not in out


def test_notice_survives_spill_branch(tmp_path):
    big_context = "[MEMORY] " + ("x" * 30000)
    out = run_hook_with_response(
        {
            "context": big_context,
            "context_summary": "- abc12345 (2026-08-24 - You said - via Here I Am): snippet",
            "new_sibling_reflections": 3,
        },
        extra_env={"HIM_INLINE_BUDGET": "1000", "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    assert "too large to inject inline" in out
    assert "3 reflections saved in other sessions" in out
    # The spilled verbatim text is not inlined
    assert "xxxxx" not in out
