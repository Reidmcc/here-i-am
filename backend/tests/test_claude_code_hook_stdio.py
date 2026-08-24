"""
The Claude Code hooks must speak UTF-8 on every stdio stream regardless of
the platform's default encoding. Python on Windows defaults piped streams
to the ANSI codepage (cp1252): before hook_util's import-time reconfigure,
SessionStart crashed mid-print on a non-breaking hyphen in the identity
block — the spill file was written but the inline context and its
read-this-file pointer never reached the entity — and hook output that did
survive arrived with every em-dash as U+FFFD.

These tests force the failure mode portably via PYTHONIOENCODING, so the
Windows behavior is exercised on any CI platform.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"

# The exact character that crashed SessionStart on Windows (U+2011,
# non-breaking hyphen — unrepresentable in cp1252), plus an em-dash and an
# emoji covering the mojibake case.
SAMPLE = "id‑block — café \U0001f9e0"
SAMPLE_LITERAL = "'id\\u2011block \\u2014 caf\\u00e9 \\U0001f9e0'"


def run_snippet(code: str, stdin: bytes = b"") -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    return subprocess.run(
        [sys.executable, "-c", code],
        input=stdin,
        capture_output=True,
        cwd=HOOKS_DIR,
        env=env,
        timeout=30,
    )


def test_stdout_is_utf8_after_hook_util_import():
    result = run_snippet(f"import hook_util\nprint({SAMPLE_LITERAL})")
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.decode("utf-8").strip() == SAMPLE


def test_stderr_is_utf8_after_hook_util_import():
    # stop.py's fail-loud escalation writes its notice to stderr
    result = run_snippet(
        f"import sys\nimport hook_util\nprint({SAMPLE_LITERAL}, file=sys.stderr)"
    )
    assert result.returncode == 0
    assert result.stderr.decode("utf-8").strip() == SAMPLE


def test_stdin_json_decodes_as_utf8_after_hook_util_import():
    # user_prompt_submit json.loads the prompt from stdin; under cp1252 an
    # em-dash in the prompt would arrive as mojibake, and several UTF-8
    # continuation bytes are undefined in cp1252 entirely
    payload = json.dumps({"prompt": SAMPLE}, ensure_ascii=False).encode("utf-8")
    result = run_snippet(
        "import json, sys\nimport hook_util\nprint(json.load(sys.stdin)['prompt'])",
        stdin=payload,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.decode("utf-8").strip() == SAMPLE


def test_hooks_without_own_prints_still_import_hook_util():
    # stop.py and session_end.py rely on hook_util's import side effect for
    # their stdio encoding — a refactor dropping the import silently
    # reintroduces the Windows failure
    for script in ("stop.py", "session_end.py"):
        source = (HOOKS_DIR / script).read_text(encoding="utf-8")
        assert "import hook_util" in source, script
