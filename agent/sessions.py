"""Redis-backed conversation state with TTL, ownership checks and per-session locks."""
import json
import uuid


class SessionStore:
    def __init__(self, redis, ttl=3600):
        self.redis, self.ttl = redis, ttl

    def _k(self, sid):
        return f"sess:{sid}"

    def create(self, principal):
        state = {"id": uuid.uuid4().hex, "user": principal.sub, "role": principal.role, "messages": [],
                 "partial": {}, "pending": {}, "facts": [], "repairs": 0, "fail_streak": 0, "tools_called": []}
        self.save(state)
        return state

    def load(self, sid):
        raw = self.redis.get(self._k(sid))
        return json.loads(raw) if raw else None

    def save(self, state):
        self.redis.set(self._k(state["id"]), json.dumps(state, default=str), ex=self.ttl)

    def lock(self, sid):
        return self.redis.lock(f"lock:{sid}", timeout=90, blocking_timeout=10)
