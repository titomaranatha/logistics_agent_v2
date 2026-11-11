"""JWT auth, role-based permissions, Redis rate limiting."""
import time
from dataclasses import dataclass

import jwt

READ = {"inventory:read", "tasks:read", "docks:read", "shipments:read", "kb:read"}
ROLE_PERMS = {
    "viewer": READ,
    "operator": READ | {"tasks:write", "docks:write"},
    "supervisor": READ | {"tasks:write", "docks:write", "actions:approve"},
    "admin": READ | {"tasks:write", "docks:write", "actions:approve", "audit:read"},
}


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str

    def can(self, perm: str) -> bool:
        return perm in ROLE_PERMS.get(self.role, set())


def issue_token(secret, sub, role, ttl=3600, issuer="logistics-agent"):
    now = int(time.time())
    return jwt.encode({"sub": sub, "role": role, "iss": issuer, "iat": now, "exp": now + ttl}, secret, algorithm="HS256")


def decode_token(secret, token, issuer="logistics-agent") -> Principal:
    try:
        c = jwt.decode(token, secret, algorithms=["HS256"], issuer=issuer,
                       options={"require": ["exp", "sub", "role", "iss"]})
    except jwt.ExpiredSignatureError:
        raise AuthError("expired") from None
    except jwt.PyJWTError:
        raise AuthError("invalid") from None
    if c["role"] not in ROLE_PERMS:
        raise AuthError("unknown_role")
    return Principal(c["sub"], c["role"])


class RateLimiter:
    """Fixed-window counter in Redis; shared across API replicas."""
    def __init__(self, redis):
        self.redis = redis

    def check(self, scope, subject, limit):
        window = int(time.time() // 60)
        key = f"rl:{scope}:{subject}:{window}"
        n = self.redis.incr(key)
        if n == 1:
            self.redis.expire(key, 70)
        return n <= limit, max(1, 60 - int(time.time() % 60))
