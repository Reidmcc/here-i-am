"""
The SessionStart hook's GitHub identity export (issue #362).

An entity with a GitHub identity configured contributes from its own
account in its own sessions: the hook writes `export` lines into the
per-session shell script Claude Code names in CLAUDE_ENV_FILE and runs
before every Bash command. The lines carry a path and two strings, never
a token; a missing CLAUDE_ENV_FILE is announced, never silent; an entity
with nothing configured leaves no trace.
"""
import json
import os
import subprocess
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

# gh's hosts.yml as gh 2.40+ writes it (multi-account: a users map, and the
# active login repeated as user), with a fake token the output must never
# carry
HOSTS_YML = (
    "github.com:\n    users:\n        Kira-here-i-am:\n"
    "            oauth_token: gho_secret\n    git_protocol: https\n"
    "    oauth_token: gho_secret\n    user: Kira-here-i-am\n"
)


def gh_login(gh_dir: Path, text: str = HOSTS_YML) -> Path:
    gh_dir.mkdir(parents=True, exist_ok=True)
    (gh_dir / "hosts.yml").write_text(text, encoding="utf-8")
    return gh_dir


@pytest.fixture(autouse=True)
def _no_machine_gh_config_dir(monkeypatch):
    """The developer's own HIM_GH_CONFIG_DIR must not reach the tests."""
    monkeypatch.delenv("HIM_GH_CONFIG_DIR", raising=False)


@pytest.fixture
def full(tmp_path):
    """FULL with its gh config directory a real, logged-in one — a path
    that doesn't exist is the missing-login notice (issue #398)."""
    return dict(FULL, gh_config_dir=str(gh_login(tmp_path / "gh-kira")))


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

    def test_email_without_name_exports_no_author_pair(self):
        """GIT_AUTHOR_NAME='' makes git refuse every commit ("empty ident
        name ... not allowed"); an unset pair falls back to the machine's
        identity. The backend defaults the name, but the hook re-validates
        what it receives because the two deploy separately."""
        lines = hook_util.git_identity_exports(
            {"author_email": "kira@example.com", "gh_config_dir": "/gh"}
        )
        assert not any("GIT_AUTHOR" in line for line in lines)
        assert lines[0] == "export GH_CONFIG_DIR='/gh'"
        assert hook_util.git_identity_exports({"author_email": "kira@example.com"}) == []

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
    def test_replaces_the_identity_an_earlier_firing_wrote(self, tmp_path, monkeypatch):
        """The file survives every compaction and resume, so a firing
        replaces what the last one wrote — appending grew it a block per
        firing until the Bash preamble broke (issue #381)."""
        env_file = tmp_path / "sessionstart-hook-0.sh"
        old = hook_util.git_identity_exports(FULL)
        env_file.write_text("\n".join(old) + "\n", encoding="utf-8", newline="\n")
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        new = hook_util.git_identity_exports(
            {"author_name": "Kira", "author_email": "kira@new.example"}
        )
        path = hook_util.write_session_env(new)
        assert path == str(env_file)
        assert env_file.read_text(encoding="utf-8") == "\n".join(new) + "\n"

    def test_keeps_lines_the_hook_did_not_write(self, tmp_path, monkeypatch):
        """N is the hook's position in the list run for that firing; whether
        a matcher filters that list wasn't measured (PR #382's review), so
        another SessionStart hook may share the file on some firings. Its
        lines survive both a replacement and a clear."""
        env_file = tmp_path / "sessionstart-hook-0.sh"
        old = hook_util.git_identity_exports(FULL)
        env_file.write_text(
            "export OTHER_HOOK='x'\n" + "\n".join(old) + "\n", encoding="utf-8", newline="\n"
        )
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        hook_util.write_session_env(old)
        assert env_file.read_text(encoding="utf-8") == (
            "export OTHER_HOOK='x'\n" + "\n".join(old) + "\n"
        )
        assert hook_util.clear_session_env_identity() is True
        assert env_file.read_text(encoding="utf-8") == "export OTHER_HOOK='x'\n"

    def test_every_exported_line_is_one_a_firing_replaces(self):
        """A line git_identity_exports writes that the replacement doesn't
        recognize would pile up a copy per firing again (issue #381)."""
        for identity in (FULL, {"gh_config_dir": "/gh"}, {"author_name": "K", "author_email": "k@e"}):
            for line in hook_util.git_identity_exports(identity):
                assert hook_util._IDENTITY_EXPORT.match(line), line

    def test_clear_removes_a_file_left_empty(self, tmp_path, monkeypatch):
        env_file = tmp_path / "sessionstart-hook-0.sh"
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        assert hook_util.clear_session_env_identity() is False
        assert not env_file.exists()
        hook_util.write_session_env(hook_util.git_identity_exports(FULL))
        assert hook_util.clear_session_env_identity() is True
        assert not env_file.exists()

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
        gh_dir = gh_login(tmp_path / "gh-kira")
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

    def test_unannounced_still_writes_the_environment(self, tmp_path, monkeypatch, full):
        env_file = tmp_path / "env.sh"
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        lines = hook_util.git_identity_lines({"git_identity": full}, announce=False)
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


class TestMachineGhConfigDir:
    """Issue #398: where the entity's gh login lives belongs to the machine
    a session runs on, not to the server, once rooms run on several."""

    def test_machine_dir_wins_over_the_servers(self, tmp_path, monkeypatch):
        env_file = tmp_path / "env.sh"
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        here = gh_login(tmp_path / "gh-here")
        monkeypatch.setenv("HIM_GH_CONFIG_DIR", str(here))
        lines = hook_util.git_identity_lines({"git_identity": FULL})
        written = env_file.read_text(encoding="utf-8")
        assert f"export GH_CONFIG_DIR='{here}'" in written
        assert "gh-kira" not in written
        assert len(lines) == 1 and "(Kira-here-i-am)" in lines[0]

    def test_machine_dir_supplies_the_login_the_server_leaves_out(self, tmp_path, monkeypatch):
        """The cloud shape: the server holds who the entity is (author name
        and email), each machine holds where its login lives."""
        env_file = tmp_path / "env.sh"
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        here = gh_login(tmp_path / "gh-here")
        monkeypatch.setenv("HIM_GH_CONFIG_DIR", str(here))
        identity = {"author_name": "Kira", "author_email": "kira@example.com", "gh_config_dir": None}
        hook_util.git_identity_lines({"git_identity": identity})
        written = env_file.read_text(encoding="utf-8")
        assert "export GIT_AUTHOR_EMAIL='kira@example.com'" in written
        assert f"export GH_CONFIG_DIR='{here}'" in written
        assert "export GIT_CONFIG_COUNT=2" in written

    def test_machine_dir_does_nothing_without_an_identity(self, tmp_path, monkeypatch):
        """It relocates a login the server says the entity has; it never
        creates one, so a variable set for one entity can't put another
        entity's sessions on that account."""
        env_file = tmp_path / "env.sh"
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        monkeypatch.setenv("HIM_GH_CONFIG_DIR", str(gh_login(tmp_path / "gh-here")))
        for body in ({}, {"git_identity": None}, {"git_identity": {"author_email": " "}}):
            assert hook_util.git_identity_lines(body) == []
        assert not env_file.exists()

    def test_blank_machine_dir_is_unset(self, monkeypatch):
        monkeypatch.setenv("HIM_GH_CONFIG_DIR", "  ")
        assert hook_util.apply_machine_gh_config_dir(FULL) == FULL

    def test_home_is_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HIM_GH_CONFIG_DIR", "~/gh-kira")
        identity = hook_util.apply_machine_gh_config_dir(FULL)
        assert os.path.normpath(identity["gh_config_dir"]) == str(tmp_path / "gh-kira")


class TestMissingLogin:
    """A gh config directory with no github.com login keeps the exports
    (a failing gh beats one acting as the human) and says so on every
    firing, instead of telling the session it acts as its own account."""

    @pytest.mark.parametrize(
        "setup, detail",
        [
            (lambda d: None, "does not exist"),
            (lambda d: d.mkdir(), "has no hosts.yml"),
            (lambda d: gh_login(d, ""), "has no github.com login"),
            (lambda d: gh_login(d, "{}\n"), "has no github.com login"),
            (lambda d: gh_login(d, "gitlab.com:\n    user: x\n"), "has no github.com login"),
        ],
    )
    def test_missing_login_is_loud_and_exports_stand(self, tmp_path, monkeypatch, setup, detail):
        env_file = tmp_path / "env.sh"
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        gh_dir = tmp_path / "gh-kira"
        setup(gh_dir)
        identity = dict(FULL, gh_config_dir=str(gh_dir))
        lines = hook_util.git_identity_lines({"git_identity": identity})
        assert len(lines) == 2
        statement, notice = lines
        # What still holds is still said: the author, and the attribution rule
        assert statement.startswith("[GIT IDENTITY]")
        assert "authored as Kira <kira@example.com>" in statement
        assert "act as your own GitHub account" not in statement
        assert notice.startswith("[HERE I AM] Your own GitHub login is not set up")
        assert detail in notice
        assert str(gh_dir) in notice
        assert "gh auth login" in notice
        assert "Tell the user" in notice
        assert "gh_config_dir on the server" in notice
        written = env_file.read_text(encoding="utf-8")
        assert f"export GH_CONFIG_DIR='{gh_dir}'" in written
        assert "GIT_CONFIG_VALUE_1='!gh auth git-credential'" in written

    def test_missing_login_is_loud_on_resume_too(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(tmp_path / "env.sh"))
        identity = dict(FULL, gh_config_dir=str(tmp_path / "missing"))
        lines = hook_util.git_identity_lines({"git_identity": identity}, announce=False)
        assert len(lines) == 1
        assert lines[0].startswith("[HERE I AM] Your own GitHub login is not set up")

    def test_notice_names_the_machine_variable_as_the_source(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(tmp_path / "env.sh"))
        monkeypatch.setenv("HIM_GH_CONFIG_DIR", str(tmp_path / "missing"))
        lines = hook_util.git_identity_lines({"git_identity": FULL})
        assert "comes from HIM_GH_CONFIG_DIR on this machine" in lines[-1]

    def test_account_only_identity_prints_just_the_notice(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(tmp_path / "env.sh"))
        identity = {"gh_config_dir": str(tmp_path / "missing")}
        lines = hook_util.git_identity_lines({"git_identity": identity})
        assert len(lines) == 1
        assert lines[0].startswith("[HERE I AM] Your own GitHub login is not set up")

    def test_unreadable_login_is_unconfirmed_not_missing(self, tmp_path, monkeypatch):
        """A github.com entry in a layout this reader doesn't know may still
        be a login gh can use: said quietly in the statement, no alarm."""
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(tmp_path / "env.sh"))
        gh_dir = gh_login(tmp_path / "gh-kira", "github.com:\n    oauth_token: gho_secret\n")
        identity = dict(FULL, gh_config_dir=str(gh_dir))
        lines = hook_util.git_identity_lines({"git_identity": identity})
        assert len(lines) == 1
        assert lines[0].startswith("[GIT IDENTITY]")
        assert "your own GitHub account (unconfirmed:" in lines[0]
        assert "gho_secret" not in lines[0]
        assert hook_util.git_identity_lines({"git_identity": identity}, announce=False) == []


SESSION = "git-identity-test"


def run_session_start(body: dict, tmp_path, source: str, env_file, extra_env=None) -> str:
    """Run session_start.main() as a subprocess with the backend stubbed —
    the wire from the response to stdout and to CLAUDE_ENV_FILE, which the
    unit tests above can't see (the #358 rule: test the path, not the
    parts)."""
    env = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    env.pop("HIM_DISABLE", None)
    env.pop("HIM_INLINE_BUDGET", None)
    env.pop("CLAUDE_ENV_FILE", None)
    if env_file is not None:
        env["CLAUDE_ENV_FILE"] = str(env_file)
    env.update(extra_env or {})
    body_path = tmp_path / "stub-body.json"
    body_path.write_text(json.dumps(body), encoding="utf-8")
    stdin_payload = {"session_id": SESSION, "source": source, "cwd": str(tmp_path)}
    code = (
        "import io, json, sys\n"
        "import hook_util\n"
        f"body = json.load(open({str(body_path)!r}, encoding='utf-8'))\n"
        "hook_util.post_backend = lambda path, payload, timeout=30: body\n"
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
    return result.stdout.decode("utf-8").replace("\r\n", "\n")


CONTEXT = "[HERE I AM] You are Kira, a Here I Am entity, operating in Claude Code mode."


class TestThroughMain:
    def test_startup_prints_statement_after_context_and_writes_env(self, tmp_path, full):
        env_file = tmp_path / "sessionstart-hook-1.sh"
        out = run_session_start(
            {
                "context": CONTEXT,
                "bulk_context": "",
                "git_identity": full,
                "rooms_notice": "[ROOMS REGISTRY] noted",
            },
            tmp_path,
            "startup",
            env_file,
        )
        assert out.index(CONTEXT) < out.index("[GIT IDENTITY]") < out.index("[ROOMS REGISTRY]")
        assert "authored as Kira <kira@example.com>" in out
        assert "GIT_AUTHOR_EMAIL='kira@example.com'" in env_file.read_text(encoding="utf-8")

    def test_startup_with_spilled_bulk_keeps_statement_inline(self, tmp_path, full):
        env_file = tmp_path / "env.sh"
        out = run_session_start(
            {
                "context": CONTEXT,
                "bulk_context": "x" * 20000,
                "bulk_parts": [{"name": "notes-index", "text": "x" * 20000}],
                "inline_budget": 9600,
                "git_identity": full,
            },
            tmp_path,
            "startup",
            env_file,
        )
        assert "too large to inject inline" in out
        assert out.index(CONTEXT) < out.index("[GIT IDENTITY]")
        assert "x" * 1000 not in out
        assert env_file.exists()

    def test_resume_writes_env_and_prints_nothing(self, tmp_path, full):
        env_file = tmp_path / "env.sh"
        out = run_session_start(
            {"context": "", "bulk_context": "", "git_identity": full},
            tmp_path,
            "resume",
            env_file,
        )
        assert out.strip() == ""
        assert "export GH_CONFIG_DIR=" in env_file.read_text(encoding="utf-8")

    def test_repeated_firings_leave_one_block(self, tmp_path, full):
        """Issue #381: the env file is keyed on the session id, so every
        startup, compact, and resume of a long-lived session hands the hook
        the same file. Ten firings must leave one identity block, not ten
        — the preamble is prepended to every Bash command."""
        env_file = tmp_path / "sessionstart-hook-0.sh"
        body = {"context": CONTEXT, "bulk_context": "", "git_identity": full}
        run_session_start(body, tmp_path, "startup", env_file)
        once = env_file.read_bytes()
        for source in ("compact", "resume") * 4 + ("compact",):
            context = CONTEXT if source == "compact" else ""
            run_session_start(dict(body, context=context), tmp_path, source, env_file)
        assert env_file.read_bytes() == once
        assert once.decode("utf-8").count("export GIT_AUTHOR_EMAIL=") == 1

    def test_resume_replaces_a_changed_identity(self, tmp_path, full):
        env_file = tmp_path / "sessionstart-hook-0.sh"
        run_session_start(
            {"context": CONTEXT, "bulk_context": "", "git_identity": full},
            tmp_path,
            "startup",
            env_file,
        )
        changed = dict(full, author_email="kira@new.example", gh_config_dir=None)
        run_session_start(
            {"context": "", "bulk_context": "", "git_identity": changed},
            tmp_path,
            "resume",
            env_file,
        )
        assert env_file.read_text(encoding="utf-8").splitlines() == [
            "export GIT_AUTHOR_NAME='Kira'",
            "export GIT_AUTHOR_EMAIL='kira@new.example'",
        ]

    def test_a_file_bloated_by_the_old_append_shrinks_back(self, tmp_path, full):
        """A session started under the appending hook carries one block per
        past firing; its next firing leaves one."""
        env_file = tmp_path / "sessionstart-hook-0.sh"
        block = "\n".join(hook_util.git_identity_exports(full)) + "\n"
        env_file.write_text(block * 24, encoding="utf-8", newline="\n")
        run_session_start(
            {"context": "", "bulk_context": "", "git_identity": full},
            tmp_path,
            "resume",
            env_file,
        )
        assert env_file.read_text(encoding="utf-8") == block

    def test_resume_with_identity_unconfigured_removes_it_aloud(self, tmp_path, full):
        """No identity in a response means the entity has none configured
        (an unreachable backend returns before the export). The file follows
        the response, so a session started with an identity loses it at its
        next firing — and says so, since commits now carry the human's."""
        env_file = tmp_path / "sessionstart-hook-0.sh"
        run_session_start(
            {"context": CONTEXT, "bulk_context": "", "git_identity": full},
            tmp_path,
            "startup",
            env_file,
        )
        out = run_session_start(
            {"context": "", "bulk_context": "", "git_identity": None},
            tmp_path,
            "resume",
            env_file,
        )
        assert not env_file.exists()
        assert out.startswith("[HERE I AM] Your own GitHub identity is no longer configured")
        assert "human" in out
        # Said once: the next firing has nothing to remove and is silent
        again = run_session_start(
            {"context": "", "bulk_context": "", "git_identity": None},
            tmp_path,
            "resume",
            env_file,
        )
        assert again.strip() == ""

    def test_resume_without_env_file_is_loud(self, tmp_path, full):
        out = run_session_start(
            {"context": "", "bulk_context": "", "git_identity": full},
            tmp_path,
            "resume",
            None,
        )
        assert out.startswith("[HERE I AM] Your own GitHub identity could not be exported")
        assert "human" in out

    def test_machine_dir_reaches_the_env_file(self, tmp_path, full):
        """Issue #398, through the hook's entry point: the machine's
        HIM_GH_CONFIG_DIR is the directory the session's gh reads."""
        env_file = tmp_path / "env.sh"
        here = gh_login(tmp_path / "gh-here")
        out = run_session_start(
            {"context": CONTEXT, "bulk_context": "", "git_identity": full},
            tmp_path,
            "startup",
            env_file,
            {"HIM_GH_CONFIG_DIR": str(here)},
        )
        assert f"export GH_CONFIG_DIR='{here}'" in env_file.read_text(encoding="utf-8")
        assert "(Kira-here-i-am)" in out
        assert "not set up" not in out

    def test_missing_login_is_said_on_resume(self, tmp_path, full):
        env_file = tmp_path / "env.sh"
        out = run_session_start(
            {"context": "", "bulk_context": "", "git_identity": full},
            tmp_path,
            "resume",
            env_file,
            {"HIM_GH_CONFIG_DIR": str(tmp_path / "missing")},
        )
        assert out.startswith("[HERE I AM] Your own GitHub login is not set up on this machine")
        assert "comes from HIM_GH_CONFIG_DIR on this machine" in out
        assert "export GH_CONFIG_DIR=" in env_file.read_text(encoding="utf-8")

    def test_no_identity_leaves_no_trace(self, tmp_path, full):
        env_file = tmp_path / "env.sh"
        out = run_session_start(
            {"context": CONTEXT, "bulk_context": "", "git_identity": None},
            tmp_path,
            "startup",
            env_file,
        )
        assert out.strip() == CONTEXT
        assert not env_file.exists()


class TestGhAccount:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("github.com:\n    user: Kira-here-i-am\n    git_protocol: https\n", "Kira-here-i-am"),
            ("github.com:\n    oauth_token: x\n    user: 'quoted'\n", "quoted"),
            ("other.example:\n    user: nope\n", None),
            ('"github.com":\n    user: Kira-here-i-am\n', "Kira-here-i-am"),
            ("github.com:\n    user: Kira-here-i-am", "Kira-here-i-am"),
            (HOSTS_YML, "Kira-here-i-am"),
            ("", None),
        ],
    )
    def test_reads_login_from_hosts_yml(self, tmp_path, text, expected):
        (tmp_path / "hosts.yml").write_text(text, encoding="utf-8")
        assert hook_util.gh_account_in_config_dir(str(tmp_path)) == expected

    def test_missing_dir_or_file_is_none(self, tmp_path):
        assert hook_util.gh_account_in_config_dir(None) is None
        assert hook_util.gh_account_in_config_dir(str(tmp_path / "nope")) is None

    @pytest.mark.parametrize(
        "text, state",
        [
            (HOSTS_YML, "ok"),
            ("github.com:\r\n    user: Kira-here-i-am\r\n", "ok"),
            ("github.com:\n    oauth_token: x\n", "unconfirmed"),
            ("", "missing"),
            ("{}\n", "missing"),
            ("ghe.example:\n    user: x\n", "missing"),
        ],
    )
    def test_login_state(self, tmp_path, text, state):
        # Written as bytes so a CRLF file stays CRLF on every platform
        (tmp_path / "hosts.yml").write_bytes(text.encode("utf-8"))
        assert hook_util.gh_login_state(str(tmp_path))[0] == state
