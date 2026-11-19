"""
Authentication for the Here I Am server (issue #396).

What sits behind this server is an entity's whole memory, writable, with
other people's words in it. So every request carries a credential, and
nothing is trusted for where it comes from, localhost included: once a
Claude Code session runs on the same machine as the server, its shell is
localhost too.

Two kinds of principal:

- **The researcher**, a person in a browser. A password (scrypt hash in
  the database, set only by the CLI on the server) buys a server-side
  session, carried in an `HttpOnly; SameSite=Strict` cookie. A cookie is
  sent by the browser unasked, so requests that change something must also
  come from the server's own origin (`origin.py`).

- **Machines**: the Claude Code hooks, the MCP client, the compaction mod,
  scripts. Each presents `Authorization: Bearer him_…`, a token minted and
  revoked by the CLI, stored as a SHA-256 digest. A token's scope is
  `researcher` (everything) or `entity`: bound to one entity, and good only
  for `/api/claude-code/*` and `/mcp`, which is exactly what the hooks and
  the memory tools call. The researcher's acts (archiving, status
  overrides, deletion, rebuilds, the chat routes that spend credit) are
  out of an entity token's reach, which is what makes "every archive
  change is the researcher's" a fact about the code and not a convention.

Closed by default: `authenticate` is an app-level dependency, so it runs
for every route FastAPI serves, and what is open without a credential is
the short list in `policy.PUBLIC_ROUTES`. A test walks the app's routes and
fails on any that answers without one (tests/test_auth_route_walk.py).

No HTTP endpoint creates a credential. Passwords and tokens are made by
`python -m app.auth` on the server's own machine, by someone who can
already read its database.

Layout: `policy` (who may reach what), `passwords`, `store` (the tables),
`throttle` (login backoff), `origin`, `dependency` (the check itself),
`cli`. The HTTP side of logging in and out is `app/routes/auth.py`.
"""
from app.auth.dependency import authenticate
from app.auth.policy import SCOPE_ENTITY, SCOPE_RESEARCHER, Principal

__all__ = ["authenticate", "Principal", "SCOPE_ENTITY", "SCOPE_RESEARCHER"]
