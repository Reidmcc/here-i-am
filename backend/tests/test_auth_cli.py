"""
`python -m app.auth` (issue #396): the one place a password is set and a
token is minted.
"""
import io
import re
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import cli, passwords, store
from app.config import settings
from app.models.auth import ApiToken, AuthSession

PASSWORD = "correct horse battery staple"
TWO_ENTITIES = (
    '[{"index_name": "ada-index", "label": "Ada", "llm_provider": "anthropic"},'
    ' {"index_name": "bea-index", "label": "Bea", "llm_provider": "anthropic"}]'
)
TOKEN = re.compile(r"^\s+(him_[A-Za-z0-9_-]{40,})$", re.M)


@pytest.fixture(autouse=True)
def setup(monkeypatch, test_engine):
    monkeypatch.setattr(passwords, "SCRYPT_N", 2 ** 10)
    monkeypatch.setattr(settings, "pinecone_indexes", TWO_ENTITIES)
    monkeypatch.setattr(settings, "claude_code_mode_enabled", True)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def open_session():
        async with maker() as db:
            yield db

    monkeypatch.setattr(cli, "open_session", open_session)
    return maker


@pytest.fixture
def maker(setup):
    return setup


async def run(*argv, stdin=""):
    lines = []
    code = await cli.run(list(argv), out=lines.append, stdin=io.StringIO(stdin))
    return code, "\n".join(str(line) for line in lines)


class TestSetPassword:
    async def test_sets_a_password_that_then_checks(self, maker):
        code, output = await run("set-password", "--password-stdin", stdin=PASSWORD + "\n")
        assert code == 0 and "The password is set." in output
        assert PASSWORD not in output
        async with maker() as db:
            assert await store.check_password(db, PASSWORD) is True
            assert await store.check_password(db, PASSWORD + "x") is False
            assert PASSWORD not in await store.get_password_hash(db)

    async def test_a_short_password_changes_nothing(self, maker):
        code, output = await run("set-password", "--password-stdin", stdin="short\n")
        assert code == 1
        assert "at least 12 characters" in output and "Nothing was changed" in output
        async with maker() as db:
            assert await store.get_password_hash(db) is None

    async def test_changing_it_signs_browsers_out(self, maker):
        await run("set-password", "--password-stdin", stdin=PASSWORD + "\n")
        async with maker() as db:
            await store.create_session(db, "a browser")
        code, output = await run("set-password", "--password-stdin", stdin="another long password\n")
        assert code == 0 and "1 browser session(s) were signed out" in output
        async with maker() as db:
            assert (await db.execute(select(AuthSession))).scalars().all() == []

    async def test_typed_twice_must_match(self, maker, monkeypatch):
        monkeypatch.setattr(cli, "is_interactive", lambda: True)
        answers = iter([PASSWORD, PASSWORD + " typo"])
        monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
        code, output = await run("set-password")
        assert code == 1 and "differ" in output
        async with maker() as db:
            assert await store.get_password_hash(db) is None


class TestTokens:
    async def test_create_prints_the_token_once_and_stores_its_digest(self, maker):
        code, output = await run("token", "create", "--name", "laptop", "--entity", "bea")
        assert code == 0
        assert "Token 'laptop' created (entity, bound to Bea)" in output
        value = TOKEN.search(output).group(1)
        assert "HIM_TOKEN" in output and "Shown once" in output
        async with maker() as db:
            row = (await db.execute(select(ApiToken))).scalar_one()
        assert (row.name, row.scope, row.entity_id) == ("laptop", "entity", "bea-index")
        assert row.token_hash == store.digest(value)
        # Nothing later shows it again
        _code, listing = await run("token", "list")
        assert value not in listing and "laptop" in listing and "Bea" in listing

    async def test_with_one_entity_configured_it_needs_no_naming(self, maker, monkeypatch):
        monkeypatch.setattr(
            settings, "pinecone_indexes",
            '[{"index_name": "ada-index", "label": "Ada", "llm_provider": "anthropic"}]',
        )
        code, _output = await run("token", "create", "--name", "desktop")
        assert code == 0
        async with maker() as db:
            assert (await db.execute(select(ApiToken))).scalar_one().entity_id == "ada-index"

    async def test_with_several_it_does_not_guess(self, maker):
        # A token bound to the wrong entity is refused on every hook call of
        # a machine whose HIM_ENTITY names another, and with no HIM_ENTITY
        # it would record that machine's sessions as the wrong entity
        code, output = await run("token", "create", "--name", "desktop")
        assert code == 1
        assert "--entity" in output and "Ada (ada-index)" in output and "Bea (bea-index)" in output
        assert TOKEN.search(output) is None
        async with maker() as db:
            assert (await db.execute(select(ApiToken))).scalars().all() == []

    async def test_a_researcher_token_is_bound_to_nobody(self, maker):
        code, output = await run("token", "create", "--name", "script", "--scope", "researcher")
        assert code == 0 and "everything the" in output
        async with maker() as db:
            row = (await db.execute(select(ApiToken))).scalar_one()
        assert (row.scope, row.entity_id) == ("researcher", None)

    @pytest.mark.parametrize("argv,message", [
        (("token", "create", "--name", "x", "--entity", "nobody"), "Unknown entity 'nobody'"),
        (("token", "create", "--name", "x", "--scope", "researcher", "--entity", "ada"),
         "only applies to an entity token"),
    ])
    async def test_mistakes_mint_nothing(self, maker, argv, message):
        code, output = await run(*argv)
        assert code == 1 and message in output
        assert TOKEN.search(output) is None
        async with maker() as db:
            assert (await db.execute(select(ApiToken))).scalars().all() == []

    async def test_a_name_in_use_is_refused(self, maker):
        await run("token", "create", "--name", "laptop", "--entity", "ada")
        code, output = await run("token", "create", "--name", "laptop", "--entity", "ada")
        assert code == 1 and "already exists" in output

    async def test_with_no_entities_there_is_nothing_to_bind_to(self, maker, monkeypatch):
        monkeypatch.setattr(settings, "pinecone_indexes", "")
        code, output = await run("token", "create", "--name", "laptop")
        assert code == 1 and "No entities are configured" in output

    async def test_revoke(self, maker):
        await run("token", "create", "--name", "laptop", "--entity", "ada")
        code, output = await run("token", "revoke", "laptop")
        assert code == 0 and "Revoked 'laptop'" in output
        _code, listing = await run("token", "list")
        assert "REVOKED" in listing
        code, output = await run("token", "revoke", "laptop")
        assert code == 1 and "Nothing was revoked" in output


class TestATerminalThatCannotHideAPassword:
    """Git Bash's own window on Windows is not a console: getpass shows no
    prompt there and never returns. The command says so instead of hanging
    (PR #411 review)."""

    @pytest.mark.parametrize("argv", [("set-password",), ("init", "--entity", "ada")])
    async def test_it_says_what_to_do_and_changes_nothing(self, maker, monkeypatch, argv):
        monkeypatch.setattr(cli, "is_interactive", lambda: False)
        asked = []
        monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": asked.append(prompt) or "x" * 20)
        code, output = await run(*argv)
        assert code == 1 and asked == []
        assert "PowerShell" in output and "winpty" in output and "--password-stdin" in output
        async with maker() as db:
            assert await store.get_password_hash(db) is None
            assert await store.list_tokens(db) == []

    async def test_password_stdin_needs_no_console(self, maker, monkeypatch):
        monkeypatch.setattr(cli, "is_interactive", lambda: False)
        code, _output = await run("set-password", "--password-stdin", stdin=PASSWORD + "\n")
        assert code == 0


class TestInit:
    async def test_a_missing_entity_is_said_before_any_password_is_asked(self, maker, monkeypatch):
        monkeypatch.setattr(cli, "is_interactive", lambda: True)
        asked = []
        monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": asked.append(prompt) or PASSWORD)
        code, output = await run("init")
        assert code == 1 and asked == []
        assert "--entity" in output and "Ada (ada-index)" in output
        async with maker() as db:
            assert await store.get_password_hash(db) is None
            assert await store.list_tokens(db) == []

    async def test_first_run_sets_the_password_and_mints_this_machines_token(self, maker):
        code, output = await run("init", "--password-stdin", "--name", "desk", "--entity", "ada", stdin=PASSWORD + "\n")
        assert code == 0
        assert "The password is set." in output
        assert "Token 'desk' created" in output and "entity: Ada" in output
        assert TOKEN.search(output)
        async with maker() as db:
            assert await store.check_password(db, PASSWORD) is True
            assert len(await store.list_tokens(db)) == 1

    async def test_running_it_again_changes_nothing(self, maker):
        await run("init", "--password-stdin", "--name", "desk", "--entity", "ada", stdin=PASSWORD + "\n")
        async with maker() as db:
            before = await store.get_password_hash(db)
        code, output = await run("init", "--password-stdin", stdin="some other password\n")
        assert code == 0
        assert "already set" in output and "already exists" in output
        assert TOKEN.search(output) is None
        async with maker() as db:
            assert await store.get_password_hash(db) == before
            assert len(await store.list_tokens(db)) == 1

    async def test_the_token_is_named_for_the_machine_by_default(self, maker, monkeypatch):
        monkeypatch.setattr(cli.socket, "gethostname", lambda: "workshop-pc")
        _code, output = await run("init", "--password-stdin", "--entity", "ada", stdin=PASSWORD + "\n")
        assert "Token 'workshop-pc' created" in output

    async def test_with_claude_code_mode_off_no_token_is_minted(self, maker, monkeypatch):
        monkeypatch.setattr(settings, "claude_code_mode_enabled", False)
        code, output = await run("init", "--password-stdin", stdin=PASSWORD + "\n")
        assert code == 0 and "no machine token is needed" in output
        async with maker() as db:
            assert await store.list_tokens(db) == []


class TestStatusAndSessions:
    async def test_status_says_what_is_missing(self, maker):
        _code, output = await run("status")
        assert "Password: NOT SET" in output and "Active tokens: 0" in output
        await run("init", "--password-stdin", "--name", "desk", "--entity", "ada", stdin=PASSWORD + "\n")
        _code, output = await run("status")
        assert "Password: set" in output and "Active tokens: 1" in output and "desk" in output

    async def test_sessions_can_be_listed_and_ended(self, maker):
        async with maker() as db:
            await store.create_session(db, "Firefox on the laptop")
        _code, output = await run("session", "list")
        assert "Firefox on the laptop" in output
        code, output = await run("session", "revoke-all")
        assert code == 0 and "1 browser session(s) signed out" in output
        _code, output = await run("session", "list")
        assert output == "No browser sessions."


class TestTheRealCommand:
    def test_python_m_app_auth_on_a_fresh_database(self, tmp_path):
        """Everything above replaces the function that opens the database.
        This runs the command as a person does, against a database file
        that doesn't exist yet: the tables are made, the password set, the
        token printed once, and the second command sees them."""
        import os
        import subprocess
        import sys
        from pathlib import Path

        backend = Path(__file__).resolve().parents[1]
        database = (tmp_path / "fresh.db").as_posix()
        env = {
            **os.environ,
            "HERE_I_AM_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
            "PINECONE_INDEXES": TWO_ENTITIES,
            "CLAUDE_CODE_MODE_ENABLED": "true",
            "LOCAL_TIMEZONE": "UTC",
        }

        def auth(*argv, stdin=""):
            result = subprocess.run(
                [sys.executable, "-m", "app.auth", *argv], input=stdin.encode("utf-8"),
                capture_output=True, cwd=backend, env=env, timeout=120,
            )
            return result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")

        code, out, err = auth("status")
        assert code == 0, err
        assert "Password: NOT SET" in out

        code, out, err = auth("init", "--password-stdin", "--name", "desk", "--entity", "Bea",
                              stdin=PASSWORD + "\n")
        assert code == 0, err
        token = TOKEN.search(out.replace("\r\n", "\n")).group(1)
        assert "entity: Bea" in out and PASSWORD not in out + err

        code, out, err = auth("token", "list")
        assert code == 0 and "desk" in out and "Bea" in out and "active" in out
        assert token not in out + err

        code, out, _err = auth("token", "create", "--name", "second")
        assert code == 1 and "--entity" in out

        # A terminal with no console attached (this subprocess has a pipe)
        code, out, _err = auth("set-password")
        assert code == 1 and "--password-stdin" in out


class TestStartupWarnings:
    async def test_the_server_says_what_is_missing_at_startup(self, maker, monkeypatch):
        import app.main as main

        monkeypatch.setattr(main, "async_session_maker", maker)
        lines = await main.auth_setup_warnings()
        assert len(lines) == 2
        assert "python -m app.auth init" in lines[0]
        assert "token create" in lines[1]
        await run("init", "--password-stdin", "--name", "desk", "--entity", "ada", stdin=PASSWORD + "\n")
        assert await main.auth_setup_warnings() == []
