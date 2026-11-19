"""
The one place credentials are made (issue #396). Run it on the server's
own machine, from the backend directory, as the user the server runs as:

    python -m app.auth init                 first run: password + first token
    python -m app.auth set-password         set or change the password
    python -m app.auth token create --name laptop --entity Kira
    python -m app.auth token create --name backup-script --scope researcher
    python -m app.auth token list
    python -m app.auth token revoke laptop
    python -m app.auth session list
    python -m app.auth session revoke-all   sign every browser out
    python -m app.auth status

It writes straight to the server's database (HERE_I_AM_DATABASE_URL, the
same .env the server reads), which is the point: whoever can run it can
already read that database, so it grants nothing they didn't have. No HTTP
endpoint does any of this.

A token is printed once, at creation, and can't be shown again: only its
digest is kept. The password is asked for without echo; --password-stdin
reads it from standard input instead, for a script.
"""
import argparse
import asyncio
import getpass
import socket
import sys
from contextlib import asynccontextmanager
from datetime import timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from app.auth import store
from app.auth.passwords import MIN_PASSWORD_LENGTH, password_problem
from app.auth.policy import SCOPE_ENTITY, SCOPE_RESEARCHER, SCOPES
from app.config import EntityConfig, settings
from app.models.auth import ApiToken, AuthCredential, AuthSession


class CliError(Exception):
    """Something the person at the terminal got wrong; printed, exit 1."""


AUTH_TABLES = [AuthCredential.__table__, AuthSession.__table__, ApiToken.__table__]


@asynccontextmanager
async def open_session():
    """A database session on the server's database, with the auth tables
    in place: a fresh install runs this before the server has ever started.
    Only these three tables are created here; the rest, and the server's
    migrations, are the server's to do at its own startup."""
    from app.database import Base, async_session_maker, engine

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=AUTH_TABLES))
    async with async_session_maker() as db:
        yield db
    await engine.dispose()


def resolve_entity(identifier: Optional[str]) -> EntityConfig:
    """The configured entity an index name or label names (as the hooks'
    HIM_ENTITY is read). With one entity configured it needs no naming.
    With several, this does not guess: a token bound to the wrong entity
    is refused on every hook call of a machine whose HIM_ENTITY names
    another, and on a machine with no HIM_ENTITY it would quietly record
    that machine's sessions as the wrong entity."""
    entities = settings.get_entities()
    if not entities:
        raise CliError(
            "No entities are configured (PINECONE_INDEXES is empty), so there "
            "is no entity to bind a token to."
        )
    known = ", ".join(f"{e.label} ({e.index_name})" for e in entities)
    if not identifier or not identifier.strip():
        if len(entities) == 1:
            return entities[0]
        raise CliError(
            "More than one entity is configured, so say which one this token "
            f"is for with --entity. Configured: {known}."
        )
    wanted = identifier.strip().lower()
    for entity in entities:
        if entity.index_name.lower() == wanted or entity.label.lower() == wanted:
            return entity
    raise CliError(f"Unknown entity '{identifier}'. Configured: {known}.")


def _entity_label(entity_id: Optional[str]) -> str:
    if not entity_id:
        return "-"
    entity = settings.get_entity_by_index(entity_id)
    return entity.label if entity else f"{entity_id} (no longer configured)"


def is_interactive() -> bool:
    """Whether a password can be asked for without echo: standard input is
    a real console. It is not in Git Bash's own window on Windows (mintty),
    where getpass neither shows its prompt nor returns, nor in a pipe."""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


NOT_INTERACTIVE = (
    "This terminal can't be asked for a password without showing it (standard "
    "input is not a console; Git Bash's window on Windows is like that). Run "
    "this in PowerShell or Command Prompt, or put 'winpty' in front of the "
    "command in Git Bash, or pass --password-stdin and send the password on "
    "standard input. Nothing was changed."
)


def read_new_password(from_stdin: bool, stdin=None) -> str:
    if from_stdin:
        password = (stdin or sys.stdin).readline().rstrip("\r\n")
    else:
        if not is_interactive():
            raise CliError(NOT_INTERACTIVE)
        password = getpass.getpass(f"New password (at least {MIN_PASSWORD_LENGTH} characters): ")
        if getpass.getpass("Again: ") != password:
            raise CliError("The two entries differ. Nothing was changed.")
    problem = password_problem(password)
    if problem:
        raise CliError(f"{problem} Nothing was changed.")
    return password


def _when(moment) -> str:
    """A stored time (naive UTC) in the human's local time: LOCAL_TIMEZONE
    when it is set and loads, else this machine's own zone."""
    if not moment:
        return "never"
    zone = None
    if settings.local_timezone:
        try:
            zone = ZoneInfo(settings.local_timezone)
        except Exception:
            zone = None
    local = moment.replace(tzinfo=timezone.utc).astimezone(zone)
    return f"{local.strftime('%Y-%m-%d %H:%M')} {local.tzname() or ''}".strip()


def token_instructions(value: str, scope: str) -> List[str]:
    lines = [
        "",
        f"    {value}",
        "",
        "Shown once: only its digest is stored, so it can't be displayed again.",
    ]
    if scope == SCOPE_ENTITY:
        lines += [
            "On the machine it is for, give it to Claude Code as HIM_TOKEN, in the",
            '"env" block of ~/.claude/settings.json beside HIM_BACKEND_URL, and make',
            "its here-i-am MCP server send it (docs/authentication.md, \"Setting it",
            "up\", has both steps). Then restart that machine's Claude Code sessions.",
        ]
    else:
        lines += [
            "Send it as 'Authorization: Bearer <token>'. It can do everything the",
            "researcher can: keep it out of any place an entity session can read.",
        ]
    return lines


async def cmd_set_password(db, args, out, stdin=None) -> None:
    password = read_new_password(args.password_stdin, stdin)
    ended = await store.set_password(db, password)
    out("The password is set.")
    if ended:
        out(f"{ended} browser session(s) were signed out; log in again with the new password.")


async def cmd_token_create(db, args, out) -> None:
    scope = args.scope
    entity_id = None
    if scope == SCOPE_ENTITY:
        entity = resolve_entity(args.entity)
        entity_id = entity.index_name
    elif args.entity:
        raise CliError("--entity only applies to an entity token (drop it, or use --scope entity).")
    try:
        value, token = await store.create_token(db, args.name, scope, entity_id)
    except ValueError as e:
        raise CliError(str(e)) from e
    bound = f", bound to {_entity_label(entity_id)}" if entity_id else ""
    out(f"Token '{token.name}' created ({scope}{bound}):")
    for line in token_instructions(value, scope):
        out(line)


async def cmd_token_list(db, args, out) -> None:
    tokens = await store.list_tokens(db)
    if not tokens:
        out("No tokens.")
        return
    for token in tokens:
        state = f"REVOKED {_when(token.revoked_at)}" if token.revoked_at else "active"
        out(
            f"{token.id[:8]}  {token.name}  {token.scope}  entity: {_entity_label(token.entity_id)}  "
            f"created {_when(token.created_at)}  last used {_when(token.last_used_at)}  {state}"
        )


async def cmd_token_revoke(db, args, out) -> None:
    revoked = await store.revoke_tokens(db, args.token)
    if not revoked:
        raise CliError(f"No active token has the name or id '{args.token}'. Nothing was revoked.")
    for token in revoked:
        out(f"Revoked '{token.name}' ({token.id[:8]}). Requests carrying it are refused from now on.")


async def cmd_session_list(db, args, out) -> None:
    sessions = await store.list_sessions(db)
    if not sessions:
        out("No browser sessions.")
        return
    for session in sessions:
        out(
            f"opened {_when(session.created_at)}  last seen {_when(session.last_seen_at)}  "
            f"ends {_when(session.expires_at)}  {session.user_agent or 'unknown browser'}"
        )


async def cmd_session_revoke_all(db, args, out) -> None:
    ended = await store.delete_all_sessions(db)
    out(f"{ended} browser session(s) signed out.")


async def cmd_status(db, args, out) -> None:
    has_password = await store.get_password_hash(db) is not None
    tokens = [t for t in await store.list_tokens(db) if t.revoked_at is None]
    sessions = await store.list_sessions(db)
    out(f"Password: {'set' if has_password else 'NOT SET (nobody can log in; run: python -m app.auth init)'}")
    out(f"Browser sessions: {len(sessions)}")
    out(f"Active tokens: {len(tokens)}")
    for token in tokens:
        out(f"  {token.name}  {token.scope}  entity: {_entity_label(token.entity_id)}  last used {_when(token.last_used_at)}")


async def cmd_init(db, args, out, stdin=None) -> None:
    """First run: whatever is missing of a password and a first entity
    token. Safe to run again; it changes nothing that is already there."""
    # Which token, if any, before anything is asked or changed: a mistake
    # in --entity should not come after a password has been typed twice
    active = [t for t in await store.list_tokens(db) if t.revoked_at is None]
    wants_token = settings.claude_code_mode_enabled and not any(
        t.scope == SCOPE_ENTITY for t in active
    )
    entity = resolve_entity(args.entity) if wants_token else None

    if await store.get_password_hash(db) is None:
        out("Setting the researcher's password (what you type at the web page's login).")
        await store.set_password(db, read_new_password(args.password_stdin, stdin))
        out("The password is set.")
    else:
        out("A password is already set (change it with: python -m app.auth set-password).")

    if not settings.claude_code_mode_enabled:
        out("Claude Code mode is off (CLAUDE_CODE_MODE_ENABLED), so no machine token is needed.")
        return
    if not wants_token:
        out("An entity token already exists (list them with: python -m app.auth token list).")
        return
    name = args.name or socket.gethostname() or "this-machine"
    try:
        value, token = await store.create_token(db, name, SCOPE_ENTITY, entity.index_name)
    except ValueError as e:
        raise CliError(str(e)) from e
    out("")
    out(f"Token '{token.name}' created for this machine's Claude Code sessions (entity: {entity.label}):")
    for line in token_instructions(value, SCOPE_ENTITY):
        out(line)
    out("Every other machine gets its own: python -m app.auth token create --name <machine> --entity <entity>")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.auth",
        description="Set the researcher's password and manage machine tokens for the Here I Am server.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="first run: set the password and mint this machine's token")
    init.add_argument("--entity", help="entity for the first token (index name or label; default: the first configured)")
    init.add_argument("--name", help="name for the first token (default: this machine's hostname)")
    init.add_argument("--password-stdin", action="store_true", help="read the password from standard input")
    init.set_defaults(run=cmd_init, takes_stdin=True)

    set_password = commands.add_parser("set-password", help="set or change the password (signs every browser out)")
    set_password.add_argument("--password-stdin", action="store_true", help="read the password from standard input")
    set_password.set_defaults(run=cmd_set_password, takes_stdin=True)

    token = commands.add_parser("token", help="machine tokens").add_subparsers(dest="token_command", required=True)
    create = token.add_parser("create", help="mint a token (printed once)")
    create.add_argument("--name", required=True, help="the machine or script it is for")
    create.add_argument("--scope", choices=SCOPES, default=SCOPE_ENTITY,
                        help=f"{SCOPE_ENTITY} (default): the Claude Code endpoints, one entity; {SCOPE_RESEARCHER}: everything")
    create.add_argument("--entity", help="entity to bind an entity token to (index name or label; default: the first configured)")
    create.set_defaults(run=cmd_token_create)
    token.add_parser("list", help="every token, active and revoked").set_defaults(run=cmd_token_list)
    revoke = token.add_parser("revoke", help="revoke a token by name or id")
    revoke.add_argument("token", help="the token's name, its id, or the first 8+ characters of its id")
    revoke.set_defaults(run=cmd_token_revoke)

    session = commands.add_parser("session", help="browser sessions").add_subparsers(dest="session_command", required=True)
    session.add_parser("list", help="every live browser session").set_defaults(run=cmd_session_list)
    session.add_parser("revoke-all", help="sign every browser out").set_defaults(run=cmd_session_revoke_all)

    commands.add_parser("status", help="what is set up").set_defaults(run=cmd_status)
    return parser


async def run(argv: Optional[List[str]] = None, out=print, stdin=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        async with open_session() as db:
            if getattr(args, "takes_stdin", False):
                await args.run(db, args, out, stdin)
            else:
                await args.run(db, args, out)
    except CliError as e:
        out(f"Error: {e}")
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(run(argv))
