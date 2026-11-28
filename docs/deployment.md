# Deploying on a cloud machine

How to run the Here I Am server on a rented Linux machine, reachable only
from your own devices, with Claude Code sessions on more than one of them
(issue #396, phase 3; the notes share is issue #397).

**Read this first.** The application side of this guide is tested: the
authentication it relies on, the settings it names, and the checks at the
end are all exercised by the test suite and were run against a real
server. The machine side is written from the tools' documentation and has
**not yet been run on a real cloud machine**. Each step that still needs
its first real run is marked *(to check on the machine)*, with what to look
for. Update this file with what you find.

- [The shape](#the-shape)
- [1. The server's own user](#1-the-servers-own-user)
- [2. The server's settings](#2-the-servers-settings)
- [3. Running it](#3-running-it)
- [4. The private network and https](#4-the-private-network-and-https)
- [5. Credentials](#5-credentials)
- [6. Each machine that runs Claude Code](#6-each-machine-that-runs-claude-code)
- [7. Checking the lock](#7-checking-the-lock)
- [8. Backups](#8-backups)
- [When something is lost](#when-something-is-lost)
- [Still to decide](#still-to-decide)

## The shape

```
 your devices ── Tailscale (private, encrypted) ──▶ the cloud machine
   browser                                           tailscale serve  :443 https
   Claude Code hooks + memory tools                        │
                                                           ▼
                                                     Here I Am server  127.0.0.1:8000
                                                     (its own OS user; database, .env, notes)
```

- The machine has **no ports open to the internet**. Only devices signed in
  to your Tailscale network can reach it at all.
- The server listens on `127.0.0.1` only. `tailscale serve` is the one
  thing that speaks to the network, and it speaks https.
- Underneath that, the application's own authentication applies to every
  request: your password in the browser, a token per Claude Code machine.
  The private network is the outer door and this is the inner one.
- The server runs as **its own OS user**. Claude Code sessions on the same
  machine run as a different user and cannot read its files.

## 1. The server's own user

This is what keeps a session's shell out of the database. Authentication
checks requests; it does nothing about someone who can simply open the
file. On a machine where a Claude Code session and the server run as the
same user, the session can read the SQLite database and every API key in
`.env`, and can run `python -m app.auth` to mint itself any token. A
separate user closes that.

```bash
sudo useradd --system --create-home --home-dir /srv/here-i-am --shell /usr/sbin/nologin hereiam
```

```bash
sudo -u hereiam git clone https://github.com/Reidmcc/here-i-am.git /srv/here-i-am/app
```

Then, as that user, the usual install (`python -m venv venv`, `pip install
-r requirements.txt`) inside `/srv/here-i-am/app/backend`.

Make the directory unreadable to everyone else:

```bash
sudo chmod 700 /srv/here-i-am
```

Check it from the account your Claude Code sessions will use (here `kira`):

```bash
sudo -u kira cat /srv/here-i-am/app/backend/.env
```

The right answer is `Permission denied`. *(to check on the machine)*

The notes are the exception: sessions must read and write them. They live
outside that directory, owned by the server's user, and are shared as issue
#397 describes (`force user = hereiam` on the share, so a file a session
writes is owned by the account the server reads it as).

## 2. The server's settings

In `/srv/here-i-am/app/backend/.env`, beside the API keys and
`PINECONE_INDEXES`:

```bash
HIM_HOST=127.0.0.1
HIM_RELOAD=false
DEBUG=false
CLAUDE_CODE_MODE_ENABLED=true
AUTH_ALLOWED_ORIGINS=https://<machine>.<tailnet>.ts.net
LOCAL_TIMEZONE=America/New_York
NOTES_BASE_DIR=/srv/here-i-am-notes
HERE_I_AM_DATABASE_URL=sqlite+aiosqlite:////srv/here-i-am/data/here_i_am.db
```

- `HIM_RELOAD=false` matters more here than at home. With reload on, the
  server restarts whenever a file under it changes, so a `git pull` is a
  deploy. On the cloud machine, deploy on purpose: pull, then restart the
  service.
- `AUTH_ALLOWED_ORIGINS` is the address you will type in the browser,
  exactly, with no path. It is **required** here. The server accepts a
  browser login from its own page on the same machine without being told
  (`http://localhost`), and from nowhere else unless told: it does not
  take the request's word for what the server is called, because that is
  what a hostile page can fake. Without this line, logging in at the https
  address is refused, with a message naming the line to add. (Step 4 gives
  you the address.)
- `LOCAL_TIMEZONE` is your timezone, not the machine's (a cloud machine's
  clock is usually UTC). Each Claude Code machine sets `HIM_TIMEZONE` to
  match; the SessionStart hook says so loudly if the two disagree.
- The database path is absolute, outside the checkout, so a fresh clone
  never starts an empty one beside the code.

Create the data directories as the server's user, and close the file:

```bash
sudo -u hereiam mkdir -p /srv/here-i-am/data /srv/here-i-am/backups
```

```bash
sudo chmod 600 /srv/here-i-am/app/backend/.env
```

## 3. Running it

A systemd unit, `/etc/systemd/system/here-i-am.service`:

```ini
[Unit]
Description=Here I Am
After=network-online.target
Wants=network-online.target

[Service]
User=hereiam
WorkingDirectory=/srv/here-i-am/app/backend
ExecStart=/srv/here-i-am/app/backend/venv/bin/python run.py
Restart=on-failure
RestartSec=5
# The server's own files, and nothing else of the machine's
ProtectSystem=full
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now here-i-am
```

```bash
sudo journalctl -u here-i-am -f
```

At startup the server logs an `[AUTH]` warning for each thing that is
missing (no password; Claude Code mode on with no entity token). Those
warnings are the to-do list for step 5.

## 4. The private network and https

Install Tailscale on the cloud machine and on every device that will reach
it, signed in to the same account. Then, on the cloud machine:

```bash
sudo tailscale serve --bg 8000
```

That serves `http://127.0.0.1:8000` at `https://<machine>.<tailnet>.ts.net`
with a certificate Tailscale provides. The first run walks you through
turning on HTTPS certificates for the tailnet. `tailscale serve status`
shows what is being served; `tailscale serve reset` stops it.

In the cloud provider's firewall (the security group, on AWS): **no
inbound rules at all**. Tailscale connects outward.

Put that https address in `.env` as `AUTH_ALLOWED_ORIGINS` (step 2) and
restart the service.

*(to check on the machine)* Open the https address in a browser and log
in (after step 5).

- If logging in is refused with a message that the request "came from
  'https://…', which this server does not know as its own address", the
  address in `AUTH_ALLOWED_ORIGINS` does not match the one in the
  browser's address bar. The message ends with the exact line to use.
- After logging in, look at the cookie in the browser's developer tools:
  `him_session` should be marked `Secure` and `HttpOnly`. If `Secure` is
  missing, set `AUTH_COOKIE_SECURE=always`.

Record what you saw here, so the next machine doesn't have to find out.

## 5. Credentials

As the server's user, on the cloud machine:

```bash
sudo -u hereiam /srv/here-i-am/app/backend/auth.sh init --name cloud-vm --entity Kira
```

It asks for the password you will log in with, and prints one token: the
one for Claude Code sessions **on the cloud machine itself**. Then one more
for each other machine:

```bash
sudo -u hereiam /srv/here-i-am/app/backend/auth.sh token create --name desktop --entity Kira
```

One token per machine, named for the machine. Each is shown once.

Only the server's user can run this, which is the point: a session's own
account cannot mint itself a credential.

## 6. Each machine that runs Claude Code

On every machine, the cloud one included, in the account the sessions run
as (`~/.claude/settings.json`):

```json
"env": {
  "HIM_BACKEND_URL": "https://<machine>.<tailnet>.ts.net",
  "HIM_TOKEN": "him_… (this machine's own)",
  "HIM_ENTITY": "Kira",
  "HIM_TIMEZONE": "America/New_York",
  "HIM_NOTES_ROOT": "<where this machine mounts the notes>"
}
```

No trailing slash on `HIM_BACKEND_URL`. On the cloud machine itself it can
be `http://localhost:8000` instead, which skips the round trip through
Tailscale; the token is needed either way, because localhost is not
trusted.

The memory tools' server entry, once per machine:

```bash
claude mcp add --transport http -s user here-i-am '${HIM_BACKEND_URL:-http://localhost:8000}/mcp' --header 'Authorization: Bearer ${HIM_TOKEN}'
```

(`claude mcp remove -s user here-i-am` first if the machine already has an
entry. Then `claude mcp list` should show it connected.)

The rest of a machine's setup (the hooks, `HIM_GH_CONFIG_DIR`, the notes
mount) is in `docs/claude-code-mode.md`, "A new machine".

## 7. Checking the lock

From any device on the tailnet, with `B` the https address:

| Request | Expect |
| --- | --- |
| `curl -s -o /dev/null -w '%{http_code}' $B/api/health` | `200` |
| `curl -s -o /dev/null -w '%{http_code}' $B/api/conversations/` | `401` |
| the same with `-H "Authorization: Bearer <a machine's token>"` | `403` (an entity token does not reach the researcher's endpoints) |
| `curl -s $B/api/auth/session -H "Authorization: Bearer <that token>"` | the token's name and entity |

From a device that is **not** on the tailnet, the address should not
answer at all. *(to check on the machine)*

Then start a Claude Code session on each machine: the identity block
arrives, and `claude mcp list` shows `here-i-am` connected. If a hook is
refused it says so in one line naming what is wrong.

`auth.sh token list` on the server shows each token's last use,
which is a quick way to see which machines have actually connected.

## 8. Backups

What to keep: the database, `.env`, and the notes tree. Encrypted, and
somewhere other than this machine.

For SQLite, copy with the database's own backup command, never with `cp`
on a file the server has open:

```bash
sudo -u hereiam sqlite3 /srv/here-i-am/data/here_i_am.db ".backup '/srv/here-i-am/backups/here_i_am-$(date +%F).db'"
```

Two things to know about what a backup of the database carries:

- **It holds no usable credential.** The password is an scrypt hash;
  sessions and tokens are stored as digests. Someone with a copy cannot
  log in with it. (`.env` is another matter: it holds the provider API
  keys in the clear. Encrypt the backup.)
- **Restoring it restores who was allowed in at the time.** A token revoked
  after the backup was taken is active again after a restore. After any
  restore, run `auth.sh token list` and revoke again what should stay
  revoked.

The Pinecone indexes are rebuilt from the database if lost (Memory browser
→ Disaster Recovery), so the database is the thing that must not be lost.

## When something is lost

- **A machine is lost or retired.** `auth.sh token revoke <its name>`. The other machines are untouched. If you were logged in on
  its browser: `auth.sh session revoke-all`, or "Log out
  everywhere" in Settings from any browser.
- **A token may have leaked.** Revoke it and mint a new one for that
  machine. A token is only useful to someone already inside the tailnet.
- **The password is forgotten.** `auth.sh set-password` as the server's
  user. It needs no old password, because whoever can run it can
  already read the database. It signs every browser out.
- **The login says to wait.** Wrong passwords buy a wait of up to five
  minutes. It clears by itself, or at once with `sudo systemctl restart
  here-i-am`.

## Still to decide

From the issue, not settled by this guide:

- **SQLite or Postgres on the cloud machine.** SQLite is what is tested and
  what this guide assumes. The schema migrations in `database.py` are
  written against SQLite's catalog, so a move to Postgres needs that looked
  at first.
- **How often to back up, and where to.** At least daily for the database;
  the notes get the hourly snapshots issue #397 describes.
