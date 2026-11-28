"""
Backoff on wrong passwords (issue #396).

One counter for the whole server, not one per address: there is one
account, and behind a TLS terminator every caller arrives from the same
loopback address, so a per-address count would either count everyone
together anyway or be reset by changing a header. After a few free
mistakes each further one doubles a wait during which no password is
checked at all, capped so the researcher is never locked out for long.

The cost of that choice: someone who can reach the login endpoint can keep
the researcher waiting. The server is meant to be reachable only from the
researcher's own devices, and a wait is a better failure than a guessable
password. The state is in this process: restarting the server clears it,
which only someone on the server's machine can do.
"""
import math
import time
from typing import Callable

FREE_FAILURES = 5
BASE_WAIT_SECONDS = 2.0
MAX_WAIT_SECONDS = 300.0


class LoginThrottle:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures = 0
        self._locked_until = 0.0

    def retry_after(self) -> int:
        """Whole seconds until a password will be looked at again; 0 when
        one will be looked at now."""
        remaining = self._locked_until - self._clock()
        return max(0, math.ceil(remaining))

    def failure(self) -> None:
        self._failures += 1
        over = self._failures - FREE_FAILURES
        if over > 0:
            wait = min(BASE_WAIT_SECONDS * (2 ** (over - 1)), MAX_WAIT_SECONDS)
            self._locked_until = self._clock() + wait

    def success(self) -> None:
        self._failures = 0
        self._locked_until = 0.0


login_throttle = LoginThrottle()
