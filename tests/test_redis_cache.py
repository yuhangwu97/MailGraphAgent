from types import SimpleNamespace

from src.backend.storage import redis_cache as rc


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    def hset(self, *args, **kwargs):
        self.ops.append(("hset", args, kwargs))
        return self

    def setex(self, *args, **kwargs):
        self.ops.append(("setex", args, kwargs))
        return self

    def set(self, *args, **kwargs):
        self.ops.append(("set", args, kwargs))
        return self

    def sadd(self, *args, **kwargs):
        self.ops.append(("sadd", args, kwargs))
        return self

    def srem(self, *args, **kwargs):
        self.ops.append(("srem", args, kwargs))
        return self

    def zadd(self, *args, **kwargs):
        self.ops.append(("zadd", args, kwargs))
        return self

    def zrem(self, *args, **kwargs):
        self.ops.append(("zrem", args, kwargs))
        return self

    def delete(self, *args, **kwargs):
        self.ops.append(("delete", args, kwargs))
        return self

    def execute(self):
        for name, args, kwargs in self.ops:
            getattr(self.redis, name)(*args, **kwargs)
        return []


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.strings = {}
        self.sets = {}
        self.zsets = {}

    def pipeline(self):
        return FakePipeline(self)

    def hset(self, key, field=None, value=None, mapping=None):
        self.hashes.setdefault(key, {})
        if mapping is not None:
            self.hashes[key].update(mapping)
        else:
            self.hashes[key][field] = value

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def setex(self, key, ttl, value):
        self.strings[key] = value

    def set(self, key, value):
        self.strings[key] = value

    def get(self, key):
        return self.strings.get(key)

    def delete(self, key):
        self.hashes.pop(key, None)
        self.strings.pop(key, None)
        self.sets.pop(key, None)
        self.zsets.pop(key, None)

    def sadd(self, key, *values):
        self.sets.setdefault(key, set()).update(values)

    def srem(self, key, *values):
        for value in values:
            self.sets.get(key, set()).discard(value)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def spop(self, key):
        s = self.sets.get(key)
        if not s:
            return None
        return s.pop()

    def scard(self, key):
        return len(self.sets.get(key, set()))

    def exists(self, key):
        return int(key in self.hashes or key in self.strings or key in self.sets or key in self.zsets)

    def type(self, key):
        if key in self.hashes:
            return "hash"
        if key in self.sets:
            return "set"
        if key in self.zsets:
            return "zset"
        if key in self.strings:
            return "string"
        return "none"

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zrem(self, key, *members):
        for member in members:
            self.zsets.get(key, {}).pop(member, None)

    def zrangebyscore(self, key, min_score, max_score):
        def bound(value, fallback):
            if value == "-inf":
                return float("-inf")
            if value == "+inf":
                return float("inf")
            return float(value) if value is not None else fallback

        lo = bound(min_score, float("-inf"))
        hi = bound(max_score, float("inf"))
        return [
            member for member, score in self.zsets.get(key, {}).items()
            if lo <= float(score) <= hi
        ]

    def close(self):
        pass


def make_cache(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rc.redis, "Redis", lambda **kwargs: fake)
    monkeypatch.setattr(
        rc,
        "get_settings",
        lambda: SimpleNamespace(
            redis_host="localhost",
            redis_port=6379,
            redis_db=0,
            redis_password="",
            fetched_body_ttl_days=7,
        ),
    )
    return rc.MailCache("acct"), fake


def _make_cache():
    cache = rc.MailCache.__new__(rc.MailCache)
    cache._r = FakeRedis()
    cache._prefix = "mailgraph:"
    cache._mbx_prefix = "mailgraph:mbx:_:"
    cache._body_ttl = 3600
    cache._account_id = None
    return cache


def test_store_mail_queues_body_and_mark_ingested_clears_it(monkeypatch):
    cache, fake = make_cache(monkeypatch)
    mail = {"message_id": "mid-1", "subject": "合同", "attachments": []}

    cache.mark_processing("mid-1", "101", "INBOX", "合同", "a@example.com", "2026-07-09")
    cache.store_mail(mail)
    cache.mark_ingested("mid-1", "doc-1", att_doc_ids=["att-1"])

    assert fake.smembers(cache._k("ingest_queue")) == set()
    assert cache.get_mail("mid-1") is None
    state = cache.get_mail_state("mid-1")
    assert state["status"] == "done"
    assert state["knowledge_doc_id"] == "doc-1"
    assert state["knowledge_att_doc_ids"] == "att-1"
    assert fake.exists(cache._k("mail", "mid-1", "done"))
    assert fake.smembers(cache._k("done_uids", "INBOX")) == {"101"}
    assert fake.get(cache._k("idx", "doc", "doc-1")) == "mid-1"


def test_mark_ingest_failed_removes_queue_but_keeps_body_for_debug(monkeypatch):
    cache, fake = make_cache(monkeypatch)
    mail = {"message_id": "mid-2", "subject": "预算", "attachments": []}

    cache.mark_processing("mid-2", "102", "INBOX", "预算", "b@example.com", "2026-07-09")
    cache.store_mail(mail)
    cache.mark_ingest_failed("mid-2", "RAGFlow 邮件正文上传失败")

    assert fake.smembers(cache._k("ingest_queue")) == set()
    assert cache.get_mail("mid-2") == mail
    state = cache.get_mail_state("mid-2")
    assert state["status"] == "failed"
    assert "RAGFlow" in state["error_msg"]


def test_query_stats_uses_indexes_and_message_id_intersection(monkeypatch):
    cache, fake = make_cache(monkeypatch)
    cache.mark_processing("m1", "101", "INBOX", "合同预算", "alice@example.com", "2026-07-08T10:00:00", from_name="张三", attachment_count=1)
    cache.mark_ingested("m1", "doc-1", att_doc_ids=[])
    cache.mark_processing("m2", "102", "INBOX", "项目进展", "bob@example.com", "2026-07-08T11:00:00", from_name="李四", attachment_count=0)
    cache.mark_ingest_failed("m2", "upload failed")

    result = cache.query_stats(
        statuses=["done", "failed"],
        has_attachment=True,
        sender="张三",
        message_ids={"m1", "m2"},
        limit=10,
    )

    assert result["matched_ids"] == ["m1"]
    assert result["total"] == 1
    assert result["by_status"]["done"] == 1


def test_claim_tracked_adds_inflight_and_release_removes():
    cache = _make_cache()
    # seed one mail body + queue entry
    cache.store_mail({"message_id": "m1", "cleaned_body": "hi", "attachments": []})
    claimed = cache.claim_pending_mail_tracked()
    assert claimed["message_id"] == "m1"
    inflight = cache._r.zrangebyscore(cache._k("inflight"), "-inf", "+inf")
    assert "m1" in inflight
    cache.release_inflight("m1")
    inflight = cache._r.zrangebyscore(cache._k("inflight"), "-inf", "+inf")
    assert "m1" not in inflight


def test_reap_inflight_requeues_stale_and_resets_status():
    import time as _t
    cache = _make_cache()
    cache.store_mail({"message_id": "m1", "cleaned_body": "hi", "attachments": []})
    cache.claim_pending_mail_tracked()          # m1 now in-flight, out of queue
    # mark it processing (as the worker would)
    cache._r.hset(cache._k("mail", "m1"), mapping={"status": "processing"})
    # force stale claim time
    cache._r.zadd(cache._k("inflight"), {"m1": _t.time() - 999})

    requeued = cache.reap_inflight(stale_seconds=60)
    assert requeued == ["m1"]
    assert "m1" in cache._r.smembers(cache._k("ingest_queue"))
    assert "m1" not in cache._r.zrangebyscore(cache._k("inflight"), "-inf", "+inf")
    assert cache._r.hgetall(cache._k("mail", "m1")).get("status") == "pending"
