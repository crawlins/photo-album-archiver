"""Per-device bearer tokens.

A token is 32 random bytes, shown once when it is created; only its SHA-256
is stored. Being random rather than chosen, it needs no slow hash: there is
nothing to guess. Lookups compare against every stored hash with
``hmac.compare_digest`` so their time does not depend on how much of a hash
matched.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from .store import Store

TOKEN_BYTES = 32
TOUCH_EVERY_S = 60  # how often a token's "last used" is written, at most


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(store: Store, name: str) -> str:
    """Store a new token's hash under ``name`` and return the token, which is not kept."""
    if not name or not name.strip():
        raise ValueError("a token needs a name")
    token = new_token()
    store.add_token(name.strip(), hash_token(token))
    return token


class Authenticator:
    def __init__(self, store: Store):
        self.store = store
        self._touched: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, header: str | None) -> str | None:
        """The token's name for an ``Authorization`` header holding a valid token, else None."""
        if not header:
            return None
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        presented = hash_token(token.strip())
        found = None
        for name, stored in self.store.token_hashes():
            if hmac.compare_digest(presented, stored):
                found = name
        if found is not None:
            now = time.monotonic()
            with self._lock:
                due = now - self._touched.get(found, -TOUCH_EVERY_S) >= TOUCH_EVERY_S
                if due:
                    self._touched[found] = now
            if due:
                self.store.touch_token(found)
        return found
