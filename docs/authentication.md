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
the `backend` directory. `auth.bat` (Windows) and `auth.sh` use the
server's own virtual environment, so nothing needs activating first:

```
auth.bat init --entity Kira
```

```bash
./auth.sh init --entity Kira
```

(`python -m app.auth init --entity Kira` is the same thing with the venv
already active.) Name the entity this machine's Claude Code sessions run
as. With only one entity configured, `--entity` can be left off.

It asks for a password twice (at least 12 characters) and, when Claude
Code mode is on, mints a token for this machine's Claude Code sessions and
prints it once.

> **Where to run it.** The password is typed without being shown, which
> needs a real console: PowerShell or Command Prompt on Windows, any
> terminal on Linux or macOS. In Git Bash's own window it says so and
> stops; put `winpty` in front of the command there, or use a different
> terminal.

Then:

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

   The hooks and the compaction mod read it from there. No slash on the
   end of `HIM_BACKEND_URL`.
3. **Give the memory tools the same token.** They reach the server over
   MCP, which has its own configuration. With the plugin installed this is
   already done: its `.mcp.json` sends `HIM_TOKEN`. With a hand-added
   server (`claude mcp list` shows `here-i-am`), replace the entry. Two
   commands, the second right after the first, because the first leaves
   the machine with no entry at all:

   ```bash
   claude mcp remove -s user here-i-am
   ```

   ```bash
   claude mcp add --transport http -s user here-i-am '${HIM_BACKEND_URL:-http://localhost:8000}/mcp' --header 'Authorization: Bearer ${HIM_TOKEN}'
   ```

   Type the `${…}` exactly as written, in single quotes. They are not
   filled in now: Claude Code fills them in from the same `env` block each
   time a session starts, so the token lives in one place. (This form works
   in PowerShell and in Git Bash. `claude mcp add-json` with the same
   content does not survive Windows PowerShell 5.1's quoting.)
4. **Check it.**

   ```bash
   claude mcp list
   ```

   should show `here-i-am: … ✔ Connected`. "Missing environment variables:
   HIM_TOKEN" means the token is not in the `env` block Claude Code reads.
5. **Restart the Claude Code sessions.** A session reads its environment
   and its MCP configuration when it starts.

Every other machine that runs the entity's sessions gets its own token:

```
auth.bat token create --name laptop --entity Kira
```

A token is shown once. Only its SHA-256 digest is stored, so it cannot be
displayed again; if it is lost, revoke it and mint another.

## Moving an existing install onto it

The server restarts itself when its code changes (`HIM_RELOAD`, on by
default), so **pulling this change is deploying it**: from that moment
every request needs a credential. Do it when no room is mid-turn.

1. Pull.
2. In `backend`: `auth.bat init --entity <the entity>`. Set the password;
   keep the token it prints.
3. Put the token in `~/.claude/settings.json` as `HIM_TOKEN` (step 2
   above).
4. Replace the memory tools' server entry (step 3 above: the two `claude
   mcp` commands).
5. Restart each Claude Code session, and reload any browser tab that was
   open on the app (a tab loaded before the pull is running the old page,
   which shows errors where the new one shows the login form).

**Then check each piece**, since a half-done step shows up only later, as
missing tools or missing records:

| Check | Good |
| --- | --- |
| `auth.bat status` | `Password: set`, and the token listed |
| `claude mcp list` | `here-i-am: … ✔ Connected` |
| The first prompt in a restarted room | No `[HERE I AM]` line about a token, and the memory tools are there |

Between the pull and a session's restart, that session's hooks are
refused, and they say so on every prompt: a `[HERE I AM]` line naming
`HIM_TOKEN` as the thing missing, and that the prompt was NOT recorded.
Nothing is recorded silently and nothing is lost silently; what is said in
that window is simply not in the archive. The session's memory tools are
refused too until it restarts.

The server also logs a warning at startup for each thing that is missing.

## The CLI

```
auth init [--entity E] [--name N]     first run: password, and this machine's token
auth set-password                     set or change the password (signs every browser out)
auth token create --name NAME [--entity E] [--scope entity|researcher]
auth token list                       every token, active and revoked, with last use
auth token revoke NAME                by name, id, or the first 8+ characters of the id
auth session list                     logged-in browsers
auth session revoke-all               sign every browser out
auth status
```

where `auth` is `auth.bat`, `./auth.sh`, or `python -m app.auth` with the
venv active.

- With more than one entity configured, a token's entity has to be named.
  It does not guess: a token bound to the wrong entity would be refused on
  every hook call, or, on a machine that names no entity itself, would
  quietly record that machine's sessions as the wrong one.
- `--password-stdin` on `init` and `set-password` reads the password from
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
is logged out. Logging in again ends the session that browser had.
Changing the password ends all of them.

A browser sends a cookie without being asked, so two more things are
checked on every request that arrives with one:

- **It must not be marked by the browser as another origin's.** Browsers
  label each request with where it came from (`Sec-Fetch-Site`), and a
  page cannot change the label. Anything but this server's own page (or an
  address typed by hand) is refused, reads included. That covers what
  `SameSite` lets through: a page on another port of the same machine.
- **A request that changes something must come from an address this
  server knows as its own** (`Origin`): its page on this machine
  (`http://localhost`, `127.0.0.1`), or an address listed in
  `AUTH_ALLOWED_ORIGINS`. Nothing the request says about its own host is
  believed. A page served from a name that its author points at this
  machine (DNS rebinding) sends that name as both its origin and its host,
  so the two agree with each other; what it cannot be is a name the server
  was told. Logging in is held to the same rule, so such a page cannot
  guess passwords through your browser either.

Wrong passwords buy a growing wait: five are free, then each further one
doubles a delay (2 s, 4 s, … up to 5 minutes) during which no password is
looked at. No response may be shown inside another page's frame.

**Machines**: the Claude Code hooks, the MCP client, the compaction mod,
scripts. Each sends `Authorization: Bearer him_…`. A token has a scope:

| Scope | Reaches | Bound to |
| --- | --- | --- |
| `entity` | the Claude Code hook endpoints and `/mcp`, nothing else | one entity |
| `researcher` | everything | nobody |

An entity token is what a Claude Code machine holds. It covers exactly
what the hooks and the memory tools call. It cannot archive a
conversation, override a memory's status, delete anything, rebuild the
vector store, or send a chat message that spends provider credit: those
are the researcher's, and an entity token is refused them with a 403.
That is what makes "every archive change is the researcher's" a fact
about the code. Before tokens it held by convention.

Its reach is a list of routes (`policy.ENTITY_ROUTES`), not everything
under `/api/claude-code/`. A route added there later is closed to entity
tokens until someone lists it, having checked that it holds its caller to
the token's entity.

A researcher token works anywhere, including in a Claude Code machine's
`HIM_TOKEN`, where it would put the researcher's whole reach in front of
the entity's own shell. The server notices that at session start and the
hook says so, loudly, every time.

### Bound to one entity

An entity token acts as its own entity and no other:

- On the hook endpoints, the token's entity is used when the request
  names none, so `HIM_ENTITY` is optional on a machine with a token. A
  request that names a different entity is refused, with a message that
  says whose token it is and the two ways to fix it.
- A Claude Code session whose conversation belongs to another entity is
  refused on every hook endpoint.
- Through the memory tools, `conversation_id` must name one of that
  entity's conversations. Before tokens, the conversation id was the only
  thing that said whose memory a call could touch, and it was whatever the
  caller sent.
- A memory named by its id (`memory_neighbors`, `memory_mark`,
  `memory_release`, and the `revises` / `cites` of `memory_save`) is
  looked up inside the entity's experience: its own conversations, the
  shared rooms it takes part in, and for the default entity the old rows
  that name no entity. Anything else is not found, by full id or by
  prefix, and a prefix is matched literally (`%` and `_` are not
  wildcards).
- In a room several entities share, each may pin or release the human's
  words and its own, not another entity's. A memory's status is one value
  on the row, so releasing what someone else said would take it from
  their recall too.

Each of these is checked before anything is read or written, so a refusal
leaves no trace in the archive.

### Closed by default

The check is one dependency on the whole application
(`app/auth/dependency.py`), so it runs for every route, including one
added next month by someone who has never read this page. What answers
without a credential is two routes:

- `GET /api/health`, which says only that the server is up.
- `POST /api/auth/login`.

and the web page's own files: `index.html`, `css/` and `js/`. The rest of
that directory (`node_modules`, the test files, the package and lint
configuration, anything dropped there later) is not served.

`tests/test_auth_route_walk.py` reads every route off the running app and
fails on any that answers a stranger, or that answers an entity token
outside its list. If it fails on a new route, the route is open: fix the
route, do not add it to a list.

FastAPI's docs pages (`API_DOCS_ENABLED`) need a researcher login like
everything else.

### What a refusal looks like from a Claude Code session

The hooks tell a refusal apart from an unreachable server, in one line:

- **No token on this machine**: `HIM_TOKEN` is not set in the session's
  environment.
- **A token the server does not accept**: unknown, or revoked.
- **A token not allowed this** (403), with the server's own sentence: for
  instance, `HIM_ENTITY` naming a different entity than the token's.

The backend checks a credential before it writes anything, so the hooks
say NOT recorded outright. The first two name the command that mints a
token, and say that the memory tools use the same token and are refused
with it. The Stop hook stops the turn with the line (exit 2), as it does
for any recording failure. With no token at all, the compaction mod does
not ask the entity for a reflection it could not save.

The server logs each refusal with the address it came from and, for a
revoked token, the token's name. It never logs a token, a cookie or a
password (a test holds it to that). The address is what uvicorn reports,
which for a request through a local proxy is whatever the proxy says.

## Behind a TLS terminator

On a cloud machine the server still listens on `127.0.0.1`, with
something in front that speaks https to the browser (`tailscale serve`,
Caddy). One setting is then **required**, because the browser no longer
reaches the server at `localhost`:

```
AUTH_ALLOWED_ORIGINS=https://your.server.name
```

the address you type in the browser, exactly, with no path. Without it
logging in is refused, and the message says this line to add. The same
applies to reaching a home install by a LAN address.

The `Secure` flag on the cookie follows the scheme of the `Origin` the
browser sent, which a terminator passes through untouched.
`AUTH_COOKIE_SECURE` (`auto`, `always`, `never`) overrides it.

A Claude Code machine reaching the server across the network sets
`HIM_BACKEND_URL` to the https address. The token is sent only in a
header, never in a URL, and the hooks do not follow redirects, so it is
not passed on to another host. Tokens need no allowed origin: nothing
attaches one to a request unasked.

## What it does not do

Stated plainly, so none of it is assumed:

- **It does not protect the database from someone who can read the
  server's disk.** A session running on the server's own machine as the
  same user can open the SQLite file, or run the CLI and mint itself a
  researcher token. On a single desktop that is the case today, and the
  standing agreement not to touch the live directory is the fence. On a
  cloud machine the fix is the server running as its own OS user (the
  deployment guide).
- **It does not stop a session that drives the researcher's own browser.**
  A session using Claude in Chrome acts inside a browser that is logged
  in, with the researcher's session. That stays an agreement.
- **The entity's shell can read its own token.** Accepted: the token
  grants what the hooks already do on the entity's behalf.
- **A request's body is read before its credential is checked.** That is
  the order the web framework works in. A stranger who can reach the port
  gets no data that way, but can make the server read and parse a large
  body, and is told when a body is malformed. On a private network the
  strangers are your own devices. Closing it properly means checking the
  credential in a layer below the framework; it is filed as a follow-up.
- **One entity can take a session id before another uses it.** A Claude
  Code session id belongs to whichever entity's token records it first.
  It takes knowing another session's id before its first prompt, which in
  practice means two entities on one machine under one OS user.
- **It is not TLS.** The server speaks plain http to whatever is in front
  of it.
- **One account, no second factor**, while the server is reachable only
  from the researcher's own devices.
- **The login wait is shared, and renews.** Each wrong password past the
  fifth starts a wait of up to five minutes, and the count clears only on
  a right password or a server restart. Someone who can load the server's
  own page and type wrong passwords can keep the researcher waiting.
  Another website cannot: it is refused before any password is looked at.

## What was measured

On Claude Code 2.1.286, 2026-11-18, against a stub MCP server that logs
the headers it receives, and then against this server:

- `${VAR}` and `${VAR:-default}` are filled in, in both `url` and
  `headers`, for a plugin's `.mcp.json`, a file given to `--mcp-config`,
  and a user-scope entry.
- A value in the `env` block of `settings.json` feeds that filling-in, at
  session start and for `claude mcp list`.
- A variable that is not set goes out as the literal text
  (`Bearer ${HIM_TOKEN}`), which the server refuses as an unknown token.
  `claude mcp list` warns "Missing environment variables".
- A server that answers 401 shows as `failed` in the session's MCP
  status; the session itself starts normally, without those tools.
- The `claude mcp add --transport http … --header …` command above stores
  `{"type":"http","url":"${HIM_BACKEND_URL:-http://localhost:8000}/mcp","headers":{"Authorization":"Bearer ${HIM_TOKEN}"}}`
  and connects to this server with an entity token (`✔ Connected`). Typed
  in Git Bash. For Windows PowerShell 5.1 it was checked by handing
  `claude.exe` the command line PowerShell builds, not by typing it in a
  PowerShell window; the `add-json` form failed that same check
  ("Invalid configuration"), after the `remove` before it had succeeded.
- A URL built from a `HIM_BACKEND_URL` that ends in a slash points at
  `//mcp`, which the server does not serve. The hooks strip the slash and
  keep working, so the SessionStart hook says so when it sees one.

In Chromium, against a local server at `http://localhost`: the login form,
a wrong password, a right one, logging out, and a session ended from the
CLI while the page was open. The cookie was kept (it carries no `Secure`
flag over plain http) and was not readable from the page's scripts.

Three second readers attacked a running copy of the server before this
was merged (PR #411). What they got through, and its fix, is in that
thread: a memory named by id from outside its entity's experience, the
login reachable from a rebound name, and the page's directory served
whole.

Not measured, because it needs the cloud machine: the login through
`tailscale serve` with `AUTH_ALLOWED_ORIGINS` set, and the `Secure` cookie
over its https.
