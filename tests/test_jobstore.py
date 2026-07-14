import json
import time

from src.backend import jobstore


class FakeRedis:
    """Minimal Redis double for jobstore (hash + zset ops)."""

    def __init__(self):
        self.hashes = {}
        self.zsets = {}
        self.sets = {}

    # hash
    def hset(self, key, mapping=None, **kw):
        self.hashes.setdefault(key, {})
        if mapping:
            self.hashes[key].update({k: str(v) for k, v in mapping.items()})
        return 1

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hincrbyfloat(self, key, field, amount):
        self.hashes.setdefault(key, {})
        cur = float(self.hashes[key].get(field, 0))
        cur += amount
        self.hashes[key][field] = str(cur)
        return cur

    def exists(self, key):
        return 1 if (key in self.hashes or key in self.zsets or key in self.sets) else 0

    def delete(self, *keys):
        for k in keys:
            self.hashes.pop(k, None)
            self.zsets.pop(k, None)
            self.sets.pop(k, None)
        return len(keys)

    # zset
    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {})
        self.zsets[key].update(mapping)
        return len(mapping)

    def zrem(self, key, *members):
        z = self.zsets.get(key, {})
        for m in members:
            z.pop(m, None)
        return len(members)

    def zrevrange(self, key, start, end):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1], reverse=True)
        members = [m for m, _ in items]
        if end == -1:
            return members[start:]
        return members[start:end + 1]

    # set
    def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    def smembers(self, key):
        return set(self.sets.get(key, set()))


def test_create_and_get_job():
    r = FakeRedis()
    job_id = jobstore.create_job("scan", source="imap",
                                 params={"folder": "INBOX", "limit": 50}, client=r)
    assert job_id
    job = jobstore.get_job(job_id, client=r)
    assert job["type"] == "scan"
    assert job["source"] == "imap"
    assert job["status"] == "queued"
    assert json.loads(job["params"])["folder"] == "INBOX"
    assert float(job["created_at"]) > 0
