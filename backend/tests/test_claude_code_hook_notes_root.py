"""
The SessionStart hook's half of one notes tree (issue #397).

The notes live beside the server; a session on another machine reaches
the same tree over a mount. The hook tells the backend where this machine
reaches it (HIM_NOTES_ROOT, with os.name), and checks what the block then
names: index.md there must be the server's, byte for byte. A directory
that isn't the server's copy — a mount fallen through to an old folder, a
stale copy, a wrong or missing root — is said aloud, never edited in
silence. A pass says nothing.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402

INDEX = "# Index\n\nThe one copy.\n".encode("utf-8")


def make_notes(tmp_path, content: bytes = INDEX) -> Path:
    entity_dir = tmp_path / "notes" / "Kira"
    entity_dir.mkdir(parents=True)
    (entity_dir / "index.md").write_bytes(content)
    return entity_dir


def check_body(entity_dir, content: bytes = INDEX) -> dict:
    return {
        "notes_check": {
            "entity_dir": str(entity_dir),
            "index_sha256": hashlib.sha256(content).hexdigest(),
        }
    }


class TestPayload:
    def test_unset_root_sends_nothing(self, monkeypatch):
        monkeypatch.delenv("HIM_NOTES_ROOT", raising=False)
        assert hook_util.notes_root_payload() == {}

    def test_blank_root_sends_nothing(self, monkeypatch):
        monkeypatch.setenv("HIM_NOTES_ROOT", "   ")
        assert hook_util.notes_root_payload() == {}

    def test_root_is_made_absolute_with_this_machines_flavor(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HIM_NOTES_ROOT", str(tmp_path / "mnt" / ".." / "notes"))
        assert hook_util.notes_root_payload() == {
            "notes_root": os.path.abspath(str(tmp_path / "notes")),
            "path_flavor": os.name,
        }


class TestCheck:
    def test_the_servers_copy_passes_silently(self, tmp_path):
        entity_dir = make_notes(tmp_path)
        assert hook_util.notes_check_lines(check_body(entity_dir)) == []

    def test_a_different_copy_is_said_aloud(self, tmp_path):
        entity_dir = make_notes(tmp_path, b"# Index\n\nA stale copy.\n")
        lines = hook_util.notes_check_lines(check_body(entity_dir))
        assert len(lines) == 1
        assert lines[0].startswith("[HERE I AM] THESE ARE NOT YOUR NOTES:")
        assert str(entity_dir) in lines[0]
        assert "HIM_NOTES_ROOT" in lines[0]
        assert "tell the user" in lines[0]

    def test_line_endings_count(self, tmp_path):
        """Bytes, not text: a copy that differs only in line endings is a
        different copy (a sync tool rewrote it), and says so."""
        entity_dir = make_notes(tmp_path, INDEX.replace(b"\n", b"\r\n"))
        lines = hook_util.notes_check_lines(check_body(entity_dir))
        assert lines and "THESE ARE NOT YOUR NOTES" in lines[0]

    def test_unreachable_index_is_said_aloud(self, tmp_path):
        missing = tmp_path / "mnt" / "Kira"
        lines = hook_util.notes_check_lines(check_body(missing))
        assert len(lines) == 1
        assert lines[0].startswith("[HERE I AM] YOUR NOTES ARE NOT REACHABLE")
        assert str(missing / "index.md") in lines[0]

    def test_no_server_index_checks_only_the_directory(self, tmp_path):
        entity_dir = make_notes(tmp_path)
        body = {"notes_check": {"entity_dir": str(entity_dir), "index_sha256": None}}
        assert hook_util.notes_check_lines(body) == []
        missing = {"notes_check": {"entity_dir": str(tmp_path / "gone"), "index_sha256": None}}
        lines = hook_util.notes_check_lines(missing)
        assert lines and "NOT REACHABLE" in lines[0]

    def test_no_check_in_the_response_prints_nothing(self):
        assert hook_util.notes_check_lines({}) == []
        assert hook_util.notes_check_lines({"notes_check": None}) == []
        assert hook_util.notes_check_lines(None) == []


SESSION = "notes-root-test"
CONTEXT = "[HERE I AM] You are Kira, a Here I Am entity, operating in Claude Code mode."


def run_session_start(body: dict, tmp_path, notes_root=None):
    """Run session_start.main() as a subprocess with the backend stubbed,
    capturing the payload it sent — the wire from HIM_NOTES_ROOT to the
    request and from the response to stdout."""
    env = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    for name in ("HIM_DISABLE", "HIM_INLINE_BUDGET", "CLAUDE_ENV_FILE", "HIM_NOTES_ROOT"):
        env.pop(name, None)
    if notes_root is not None:
        env["HIM_NOTES_ROOT"] = notes_root
    body_path = tmp_path / "stub-body.json"
    body_path.write_text(json.dumps(body), encoding="utf-8")
    sent_path = tmp_path / "sent-payload.json"
    stdin_payload = {"session_id": SESSION, "source": "startup", "cwd": str(tmp_path)}
    code = (
        "import io, json, sys\n"
        "import hook_util\n"
        f"body = json.load(open({str(body_path)!r}, encoding='utf-8'))\n"
        "def stub(path, payload, timeout=30):\n"
        f"    json.dump(payload, open({str(sent_path)!r}, 'w', encoding='utf-8'))\n"
        "    return body\n"
        "hook_util.post_backend = stub\n"
        f"sys.stdin = io.StringIO({json.dumps(json.dumps(stdin_payload))})\n"
        "import session_start\n"
        "session_start.main()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        cwd=HOOKS_DIR,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    sent = json.loads(sent_path.read_text(encoding="utf-8"))
    return result.stdout.decode("utf-8").replace("\r\n", "\n"), sent


class TestThroughMain:
    def test_root_reaches_the_request(self, tmp_path):
        root = str(tmp_path / "him-notes")
        _, sent = run_session_start({"context": CONTEXT}, tmp_path, notes_root=root)
        assert sent["notes_root"] == os.path.abspath(root)
        assert sent["path_flavor"] == os.name

    def test_no_root_leaves_the_request_as_before(self, tmp_path):
        _, sent = run_session_start({"context": CONTEXT}, tmp_path)
        assert "notes_root" not in sent
        assert "path_flavor" not in sent

    def test_a_pass_adds_nothing_to_the_output(self, tmp_path):
        entity_dir = make_notes(tmp_path)
        out, _ = run_session_start({"context": CONTEXT, **check_body(entity_dir)}, tmp_path)
        assert out.strip() == CONTEXT

    def test_a_mismatch_prints_after_the_context_before_the_other_trailers(self, tmp_path):
        entity_dir = make_notes(tmp_path, b"stale")
        out, _ = run_session_start(
            {
                "context": CONTEXT,
                **check_body(entity_dir),
                "rooms_notice": "[ROOMS REGISTRY] noted",
            },
            tmp_path,
        )
        assert out.index(CONTEXT) < out.index("THESE ARE NOT YOUR NOTES") < out.index(
            "[ROOMS REGISTRY]"
        )

    def test_a_mismatch_survives_a_spilled_bulk(self, tmp_path):
        entity_dir = make_notes(tmp_path, b"stale")
        out, _ = run_session_start(
            {
                "context": CONTEXT,
                "bulk_context": "x" * 20000,
                "bulk_parts": [{"name": "notes-index", "text": "x" * 20000}],
                "inline_budget": 9600,
                **check_body(entity_dir),
            },
            tmp_path,
        )
        assert "too large to inject inline" in out
        assert "THESE ARE NOT YOUR NOTES" in out
