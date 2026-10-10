"""
The SessionStart hook says whether the compaction mod is loaded (issue
#394 review), so the identity block can describe compaction's moment
plainly: the turn the mod gives where it is loaded, and only "needs no
watching for" where the hook can't see it. A mod loads from a folder named
in CLAUDE_CODE_PLUGIN_DIRS, and the folder's manifest names the plugin.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
MODS_DIR = HOOKS_DIR.parent / "mods"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402


def _plugin(directory: Path, name: str) -> str:
    (directory / ".claude-plugin").mkdir(parents=True)
    (directory / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0"}), encoding="utf-8"
    )
    return str(directory)


def test_the_repos_compaction_mod_has_the_name_the_hook_looks_for():
    # The real manifest, so a rename of the mod can't silently turn the
    # plain sentence off
    assert hook_util.plugin_names_in_dirs(str(MODS_DIR / "compact-talk")) == {
        hook_util.COMPACT_TALK_PLUGIN_NAME
    }


def test_names_come_from_every_folder_in_the_list(tmp_path):
    value = os.pathsep.join(
        [_plugin(tmp_path / "a", "memory-pane"), _plugin(tmp_path / "b", "here-i-am-compact-talk")]
    )
    assert hook_util.plugin_names_in_dirs(value) == {"memory-pane", "here-i-am-compact-talk"}


def test_missing_and_unreadable_folders_count_for_nothing(tmp_path):
    broken = tmp_path / "broken" / ".claude-plugin"
    broken.mkdir(parents=True)
    (broken / "plugin.json").write_text("{not json", encoding="utf-8")
    value = os.pathsep.join([str(tmp_path / "absent"), str(tmp_path / "broken"), "", "  "])
    assert hook_util.plugin_names_in_dirs(value) == set()
    assert hook_util.plugin_names_in_dirs(None) == set()


def test_loaded_only_when_the_list_names_the_mod(tmp_path, monkeypatch):
    other = _plugin(tmp_path / "pane", "memory-pane")
    monkeypatch.delenv(hook_util.PLUGIN_DIRS_ENV, raising=False)
    assert not hook_util.compact_talk_mod_loaded()
    monkeypatch.setenv(hook_util.PLUGIN_DIRS_ENV, other)
    assert not hook_util.compact_talk_mod_loaded()
    # A folder merely named like the mod is not the mod
    lookalike = tmp_path / "compact-talk"
    lookalike.mkdir()
    monkeypatch.setenv(hook_util.PLUGIN_DIRS_ENV, os.pathsep.join([other, str(lookalike)]))
    assert not hook_util.compact_talk_mod_loaded()
    monkeypatch.setenv(
        hook_util.PLUGIN_DIRS_ENV, os.pathsep.join([other, str(MODS_DIR / "compact-talk")])
    )
    assert hook_util.compact_talk_mod_loaded()


def _session_start_payload(tmp_path, plugin_dirs=None) -> dict:
    """Run session_start.main() with the backend stubbed and return the
    payload it posted (the wire, not just the helper)."""
    out = tmp_path / "payload.json"
    stdin_payload = {"session_id": "mod-session", "source": "startup", "cwd": str(tmp_path)}
    code = (
        "import io, json, sys\n"
        "import hook_util\n"
        "def post_backend(path, payload, timeout=30):\n"
        f"    open({str(out)!r}, 'w', encoding='utf-8').write(json.dumps(payload))\n"
        "    return {'context': ''}\n"
        "hook_util.post_backend = post_backend\n"
        "hook_util.desktop_sessions_index = lambda *a, **k: {}\n"
        "hook_util.live_sessions_snapshot = lambda *a, **k: []\n"
        "hook_util.lineage_hints = lambda *a, **k: {}\n"
        f"sys.stdin = io.StringIO({json.dumps(json.dumps(stdin_payload))})\n"
        "import session_start\n"
        "session_start.main()\n"
    )
    env = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    for name in ("HIM_DISABLE", "CLAUDE_ENV_FILE", hook_util.PLUGIN_DIRS_ENV):
        env.pop(name, None)
    if plugin_dirs is not None:
        env[hook_util.PLUGIN_DIRS_ENV] = plugin_dirs
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, cwd=HOOKS_DIR, env=env, timeout=30
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(out.read_text(encoding="utf-8"))


def test_session_start_sends_the_flag(tmp_path):
    assert _session_start_payload(tmp_path)["compact_talk_mod"] is False
    loaded = _session_start_payload(tmp_path, str(MODS_DIR / "compact-talk"))
    assert loaded["compact_talk_mod"] is True
