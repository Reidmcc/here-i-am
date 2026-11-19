"""
Who may reach what (issue #396). The whole access policy is this file:
two short tables and one function, written as data so it can be read in
one sitting and walked by a test.

Routes are named by their path *template* (`/api/messages/{message_id}`),
as the router matched it, never by the raw request path: a template can't
be reached around with `..` segments or an encoded slash.
"""
from dataclasses import dataclass
from typing import Optional

SCOPE_RESEARCHER = "researcher"
SCOPE_ENTITY = "entity"
SCOPES = (SCOPE_ENTITY, SCOPE_RESEARCHER)

VIA_SESSION = "session"
VIA_TOKEN = "token"

# Answer without any credential. Liveness says nothing about the
# configuration (see main.health_check), and logging in is how a credential
# is obtained. Nothing else belongs here.
PUBLIC_ROUTES = frozenset({
    ("GET", "/api/health"),
    ("POST", "/api/auth/login"),
})

# What an entity token reaches: the endpoints the Claude Code hooks and the
# compaction mod call, and the MCP transport the memory tools ride on.
ENTITY_PATH_PREFIX = "/api/claude-code/"
ENTITY_PATHS = frozenset({"/mcp"})

# Open to any authenticated principal, whatever its scope: asking who you
# are, which is how a machine checks its own token.
ANY_PRINCIPAL_ROUTES = frozenset({
    ("GET", "/api/auth/session"),
})


@dataclass(frozen=True)
class Principal:
    """
    Who a request is from, once its credential has been checked.

    `scope` is what it may reach; `entity_id` (an index name) is set only
    for an entity token and says whose memory it may touch. `name` is the
    token's name ("laptop") or "researcher" for a browser session, and is
    what the server logs. `credential_id` is the token row's id or the
    session row's id (a digest, not the cookie).
    """
    scope: str
    via: str
    name: str
    entity_id: Optional[str] = None
    credential_id: Optional[str] = None

    @property
    def is_researcher(self) -> bool:
        return self.scope == SCOPE_RESEARCHER


def is_public(method: str, path_template: Optional[str]) -> bool:
    return (method.upper(), path_template) in PUBLIC_ROUTES


def in_entity_scope(path_template: Optional[str]) -> bool:
    if not path_template:
        return False
    return path_template.startswith(ENTITY_PATH_PREFIX) or path_template in ENTITY_PATHS


def allowed(principal: Principal, method: str, path_template: Optional[str]) -> bool:
    """Whether this principal's scope reaches this route. A scope this file
    doesn't know reaches nothing."""
    if principal.scope == SCOPE_RESEARCHER:
        return True
    if principal.scope != SCOPE_ENTITY:
        return False
    if (method.upper(), path_template) in ANY_PRINCIPAL_ROUTES:
        return True
    return in_entity_scope(path_template)
