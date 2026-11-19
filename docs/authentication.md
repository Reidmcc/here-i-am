# Authentication

The server answers only to a credential (issue #396). There is no switch
that turns this off and no address that is trusted without one, localhost
included.

- [Setting it up](#setting-it-up)
- [Moving an existing install onto it](#moving-an-existing-install-onto-it)
- [The CLI](#the-cli)
- [How it works](#how-it-works)
- [Behind a TLS terminator](#behind-a-tls-terminator)
- [What it does not do](#what-it-does-not-do)
- [What was measured](#what-was-measured)

## Setting it up

Everything is done with one command, run on the server's own machine from
the `backend` directory, as the user the server runs as:

```bash
python -m app.auth init
```

It asks for a password twice (at least 12 characters) and, when Claude
Code mode is on, mints a token for this machine's Claude Code sessions and
prints it once. Then:

1. **Log in.** Open the web page and type the password.
2. **Give Claude Code the token.** In `~/.claude/settings.json`, add it to
   the `env` block beside `HIM_BACKEND_URL`:

   ```json
   "env": {
     "HIM_BACKEND_URL": "http://localhost:8000",
     "HIM_ENTITY": "Kira",
     "HIM_TOKEN": "him_…"
   }
   ```

   The hooks and the compaction mod read it from there.
3. **Give the memory tools the same token.** They reach the server over
   MCP, which has its own configuration. With the plugin installed this is
   already done: its `.mcp.json` sends `HIM_TOKEN`. With a hand-added
   server, replace the entry so it does the same:

   ```bash
   claude mcp remove -s user here-i-am
   ```

   ```bash
   claude mcp add-json -s user here-i-am '{"type":"http","url":"${HIM_BACKEND_URL:-http://localhost:8000}/mcp","headers":{"Authorization":"Bearer ${HIM_TOKEN}"}}'
   ```

   The `${…}` stay as written (the single quotes keep the shell from
   filling them in). Claude Code fills them in from the same `env` block
   when a session starts, so the token lives in one place.
4. **Restart the Claude Code sessions.** A session reads its environment
   and its MCP configuration when it starts.

Every other machine that runs the entity's sessions gets its own token:

```bash
python -m app.auth token create --name laptop --entity Kira
```

A token is shown once. Only its SHA-256 digest is stored, so it cannot be
displayed again; if it is lost, revoke it and mint another.

## Moving an existing install onto it

The server restarts itself when its code changes (`HIM_RELOAD`, on by
default), so **pulling this change is deploying it**: from that moment
every request needs a credential. Do it when no room is mid-turn.

1. Pull.
2. `python -m app.auth init`, in `backend`. Set the password; keep the
   token it prints.
3. Put the token in `~/.claude/settings.json` as `HIM_TOKEN` (step 2
   above) and replace the MCP entry (step 3 above).
4. Restart each Claude Code session.

Between the pull and a session's restart, that session's hooks are
refused, and they say so on every prompt: a `[HERE I AM]` line naming
`HIM_TOKEN` as the thing missing, and that the prompt was NOT recorded.
Nothing is recorded silently and nothing is lost silently; what is said in
that window is simply not in the archive. The session's memory tools are
refused too until it restarts.

`python -m app.auth status` says what is set up. The server also logs a
warning at startup for each thing that is missing.

## The CLI

```
python -m app.auth init                  first run: password, and this machine's token
python -m app.auth set-password          set or change the password (signs every browser out)
python -m app.auth token create --name NAME [--entity ENTITY] [--scope entity|researcher]
python -m app.auth token list            every token, active and revoked, with last use
python -m app.auth token revoke NAME     by name, id, or the first 8+ characters of the id
python -m app.auth session list          logged-in browsers
python -m app.auth session revoke-all    sign every browser out
python -m app.auth status
```

`--password-stdin` on `init` and `set-password` reads the password from
standard input, for a script.

The CLI writes straight to the server's database (the one
`HERE_I_AM_DATABASE_URL` names, read from the same `.env`). That is the
whole of its authority: whoever can run it can already read that database.
**No HTTP endpoint sets a password or mints a token.**

## How it works

### Two kinds of caller

**The researcher**, a person in a browser. The password is checked against
an scrypt hash and buys a session: a random value the browser keeps in a
cookie and the server keeps only the digest of. The cookie is `HttpOnly`
(the page's scripts cannot read it), `SameSite=Strict` (the browser does
not send it from another site), and `Secure` whenever the login arrived
over https. A session ends after 30 days without a request, or at 90 days
regardless (`AUTH_SESSION_IDLE_DAYS`, `AUTH_SESSION_MAX_DAYS`), or when it
is logged out. Changing the password ends all of them.

A browser sends a cookie without being asked, so a request that changes
something with it must also come from the server's own page: its `Origin`
header must name this server. Wrong passwords buy a growing wait: five are
free, then each further one doubles a delay (2 s, 4 s, … up to 5 minutes)
during which no password is looked at.

**Machines**: the Claude Code hooks, the MCP client, the compaction mod,
scripts. Each sends `Authorization: Bearer him_…`. A token has a scope:

| Scope | Reaches | Bound to |
| --- | --- | --- |
| `entity` | `/api/claude-code/*` and `/mcp`, nothing else | one entity |
| `researcher` | everything | nobody |

An entity token is what a Claude Code machine holds. It covers exactly
what the hooks and the memory tools call. It cannot archive a
conversation, override a memory's status, delete anything, rebuild the
vector store, or send a chat message that spends provider credit: those
are the researcher's, and an entity token is refused them with a 403.
That is what makes "every archive change is the researcher's" a fact
about the code. Before tokens it held by convention.

### Bound to one entity

An entity token acts as its own entity and no other:

- On the hook endpoints, the token's entity is used when the request
  names none, so `HIM_ENTITY` is optional on a machine with a token. A
  request that names a different entity is refused.
- A Claude Code session whose conversation belongs to another entity is
  refused on every hook endpoint.
- Through the memory tools, `conversation_id` must name one of that
  entity's conversations. Before tokens, the conversation id was the only
  thing that said whose memory a call could touch, and it was whatever the
  caller sent.

Each of these is checked before anything is read or written, so a refusal
leaves no trace in the archive.

### Closed by default

The check is one dependency on the whole application
(`app/auth/dependency.py`), so it runs for every route, including one
added next month by someone who has never read this page. What answers
without a credential is two routes:

- `GET /api/health`, which says only that the server is up.
- `POST /api/auth/login`.

and the static files of the web page, which hold no data and are the
public repository's. `tests/test_auth_route_walk.py` reads every route off
the running app and fails on any that answers a stranger, or that answers
an entity token outside its scope. If it fails on a new route, the route
is open: fix the route, do not add it to a list.

FastAPI's docs pages (`API_DOCS_ENABLED`) need a researcher login like
everything else.

### What a refusal looks like from a Claude Code session

The hooks tell a refusal apart from an unreachable server, in one line:

- **No token on this machine**: `HIM_TOKEN` is not set in the session's
  environment.
- **A token the server does not accept**: unknown, or revoked.
- **A token not allowed this**: for instance, another entity's session.

The backend checks a credential before it writes anything, so the hooks
say NOT recorded outright. Each line names the command that mints a
token. The Stop hook stops the turn with it (exit 2), as it does for any
recording failure.

The server logs each refusal with the address it came from and, for a
revoked token, the token's name. It never logs a token or a cookie.

## Behind a TLS terminator

On a cloud machine the server still listens on `127.0.0.1`, with
something in front that speaks https to the browser (`tailscale serve`,
Caddy). Two things then depend on headers the terminator passes along:

- **The same-origin rule** compares the browser's `Origin` with the
  `Host` the request was sent to, or with `X-Forwarded-Host` when the
  terminator rewrites `Host`. If it does neither, logging in fails with a
  message naming the fix: `AUTH_ALLOWED_ORIGINS=https://your.server.name`.
- **The `Secure` flag** follows the scheme of the `Origin` the browser
  sent, which a terminator passes through untouched. `AUTH_COOKIE_SECURE`
  (`auto`, `always`, `never`) overrides it.

A Claude Code machine reaching the server across the network sets
`HIM_BACKEND_URL` to the https address. The token is sent only in a
header, never in a URL, and the hooks do not follow redirects, so it is
not passed on to another host.

## What it does not do

Stated plainly, so none of it is assumed:

- **It does not protect the database from someone who can read the
  server's disk.** A session running on the server's own machine as the
  same user can open the SQLite file, or run `python -m app.auth` and mint
  itself a researcher token. On a single desktop that is the case today,
  and the standing agreement not to touch the live directory is the
  fence. On a cloud machine the fix is the server running as its own OS
  user (the deployment guide).
- **It does not stop a session that drives the researcher's own browser.**
  A session using Claude in Chrome acts inside a browser that is logged
  in, with the researcher's session. That stays an agreement.
- **The entity's shell can read its own token.** Accepted: the token
  grants what the hooks already do on the entity's behalf.
- **It is not TLS.** The server speaks plain http to whatever is in front
  of it.
- **One account, no second factor**, while the server is reachable only
  from the researcher's own devices.
- **The login wait is shared.** Anyone who can reach the login endpoint
  can keep the researcher waiting up to five minutes at a time. Restarting
  the server clears it.

## What was measured

On Claude Code 2.1.286, 2026-11-18, against a stub MCP server that logs
the headers it receives:

- `${VAR}` and `${VAR:-default}` are filled in, in both `url` and
  `headers`, for a plugin's `.mcp.json`, a file given to `--mcp-config`,
  and a user-scope entry (`claude mcp add-json -s user`).
- A value in the `env` block of `settings.json` feeds that filling-in, at
  session start and for `claude mcp list`.
- A variable that is not set goes out as the literal text
  (`Bearer ${HIM_TOKEN}`), which the server refuses as an unknown token.
  `claude mcp list` warns "Missing environment variables".
- A server that answers 401 shows as `failed` in the session's MCP
  status; the session itself starts normally, without those tools.

In Chromium, against a local server at `http://localhost`: the login form,
a wrong password, a right one, logging out, and a session ended from the
CLI while the page was open. The cookie was kept (it carries no `Secure`
flag over plain http) and was not readable from the page's scripts.

Not measured, because it needs the cloud machine: which of `Host` and
`X-Forwarded-Host` `tailscale serve` passes, and the `Secure` cookie over
its https. Both have a setting to fall back on, named above.
