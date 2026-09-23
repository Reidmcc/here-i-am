"""
The SessionStart hook's GitHub identity export (issue #362).

An entity with a GitHub identity configured contributes from its own
account in its own sessions: the hook writes `export` lines into the
per-session shell script Claude Code names in CLAUDE_ENV_FILE and runs
before every Bash command. The lines carry a path and two strings, never
a token; a missing CLAUDE_ENV_FILE is announced, never silent; an entity
with nothing configured leaves no trace.
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "claude-code-mode" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_util  # noqa: E402

FULL = {
    "author_name": "Kira",
    "author_email": "kira@example.com",
    "gh_config_dir": "E:\\priv\\gh-kira",
}


class TestExports:
    def test_full_identity_exports_author_gh_and_credential_routing(self):
        lines = hook_util.git_identity_exports(FULL)
        assert lines == [
            "export GIT_AUTHOR_NAME='Kira'",
            "export GIT_AUTHOR_EMAIL='kira@example.com'",
            "export GH_CONFIG_DIR='E:\\priv\\gh-kira'",
            "export GIT_CONFIG_COUNT=2",
            "export GIT_CONFIG_KEY_0='credential.https://github.com.helper' GIT_CONFIG_VALUE_0=''",
            "export GIT_CONFIG_KEY_1='credential.https://github.com.helper' "
            "GIT_CONFIG_VALUE_1='!gh auth git-credential'",
        ]

    def test_author_only_leaves_gh_and_credentials_alone(self):
        lines = hook_util.git_identity_exports(
            {"author_name": "Kira", "author_email": "kira@example.com"}
        )
        assert lines == [
            "export GIT_AUTHOR_NAME='Kira'",
            "export GIT_AUTHOR_EMAIL='kira@example.com'",
        ]

    def test_gh_only_leaves_author_alone(self):
        lines = hook_util.git_identity_exports({"gh_config_dir": "/home/x/gh-kira"})
        assert lines[0] == "export GH_CONFIG_DIR='/home/x/gh-kira'"
        assert not any("GIT_AUTHOR" in line for line in lines)
        assert "export GIT_CONFIG_COUNT=2" in lines

    def test_nothing_configured_exports_nothing(self):
        assert hook_util.git_identity_exports(None) == []
        assert hook_util.git_identity_exports({}) == []
        assert hook_util.git_identity_exports({"author_email": "  "}) == []

    def test_values_are_shell_quoted(self):
        lines = hook_util.git_identity_exports(
            {"author_name": "O'Kira $HOME", "author_email": "k@example.com"}
        )
        assert lines[0] == "export GIT_AUTHOR_NAME='O'\\''Kira $HOME'"


class TestSessionEnvFile:
    def test_appends_to_claude_env_file(self, tmp_path, monkeypatch):
        env_file = tmp_path / "sessionstart-hook-1.sh"
        env_file.write_bytes(b"export ALREADY=1\n")
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        path = hook_util.write_session_env(["export A='1'", "export B='2'"])
        assert path == str(env_file)
        assert env_file.read_bytes() == b"export ALREADY=1\nexport A='1'\nexport B='2'\n"

    def test_no_env_file_writes_nothing_and_says_so(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_ENV_FILE", raising=False)
        assert hook_util.write_session_env(["export A='1'"]) is None

    def test_empty_lines_do_not_touch_the_file(self, tmp_path, monkeypatch):
        env_file = tmp_path / "env.sh"
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        assert hook_util.write_session_env([]) is None
        assert not env_file.exists()


class TestIdentityLines:
    def test_announces_what_holds(self, tmp_path, monkeypatch):
        env_file = tmp_path / "env.sh"
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        gh_dir = tmp_path / "gh-kira"
        gh_dir.mkdir()
        (gh_dir / "hosts.yml").write_text(
            "github.com:\n    users:\n        Kira-here-i-am:\n"
            "            oauth_token: gho_secret\n    git_protocol: https\n"
            "    oauth_token: gho_secret\n    user: Kira-here-i-am\n",
            encoding="utf-8",
        )
        identity = dict(FULL, gh_config_dir=str(gh_dir))
        lines = hook_util.git_identity_lines({"git_identity": identity})
        assert len(lines) == 1
        line = lines[0]
        assert line.startswith("[GIT IDENTITY]")
        assert "authored as Kira <kira@example.com>" in line
        assert "(Kira-here-i-am)" in line
        assert "gho_secret" not in line
        assert "no Co-Authored-By trailer" in line
        assert "the human merges" in line
        written = env_file.read_text(encoding="utf-8")
        assert "export GIT_AUTHOR_EMAIL='kira@example.com'" in written
        assert f"export GH_CONFIG_DIR='{gh_dir}'" in written

    def test_unannounced_still_writes_the_environment(self, tmp_path, monkeypatch):
        env_file = tmp_path / "env.sh"
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        lines = hook_util.git_identity_lines({"git_identity": FULL}, announce=False)
        assert lines == []
        assert "GIT_AUTHOR_EMAIL" in env_file.read_text(encoding="utf-8")

    def test_missing_env_file_is_loud_even_unannounced(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_ENV_FILE", raising=False)
        for announce in (True, False):
            lines = hook_util.git_identity_lines(
                {"git_identity": FULL}, announce=announce
            )
            assert len(lines) == 1
            assert lines[0].startswith("[HERE I AM]")
            assert "CLAUDE_ENV_FILE" in lines[0]
            assert "human" in lines[0]

    def test_no_identity_is_silent(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_ENV_FILE", raising=False)
        assert hook_util.git_identity_lines({}) == []
        assert hook_util.git_identity_lines({"git_identity": None}) == []
        assert hook_util.git_identity_lines(None) == []

    def test_unreadable_gh_dir_still_announces(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(tmp_path / "env.sh"))
        identity = dict(FULL, gh_config_dir=str(tmp_path / "missing"))
        lines = hook_util.git_identity_lines({"git_identity": identity})
        assert len(lines) == 1
        assert "act as your own GitHub account." in lines[0]


class TestGhAccount:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("github.com:\n    user: Kira-here-i-am\n    git_protocol: https\n", "Kira-here-i-am"),
            ("github.com:\n    oauth_token: x\n    user: 'quoted'\n", "quoted"),
            ("other.example:\n    user: nope\n", None),
            ("", None),
        ],
    )
    def test_reads_login_from_hosts_yml(self, tmp_path, text, expected):
        (tmp_path / "hosts.yml").write_text(text, encoding="utf-8")
        assert hook_util.gh_account_in_config_dir(str(tmp_path)) == expected

    def test_missing_dir_or_file_is_none(self, tmp_path):
        assert hook_util.gh_account_in_config_dir(None) is None
        assert hook_util.gh_account_in_config_dir(str(tmp_path / "nope")) is None
