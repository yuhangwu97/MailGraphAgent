from types import SimpleNamespace

from src.storage import redis_cache as rc


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

    def sadd(self, *args, **kwargs):
        self.ops.append(("sadd", args, kwargs))
        return self

    def srem(self, *args, **kwargs):
        self.ops.append(("srem", args, kwargs))
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

    def get(self, key):
        return self.strings.get(key)

    def delete(self, key):
        self.hashes.pop(key, None)
        self.strings.pop(key, None)
        self.sets.pop(key, None)

    def sadd(self, key, *values):
        self.sets.setdefault(key, set()).update(values)

    def srem(self, key, *values):
        for value in values:
            self.sets.get(key, set()).discard(value)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def exists(self, key):
        return int(key in self.hashes or key in self.strings or key in self.sets)

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
    assert state["ragflow_doc_id"] == "doc-1"
    assert state["ragflow_att_doc_ids"] == "att-1"
    assert fake.exists(cache._k("mail", "mid-1", "done"))
    assert fake.smembers(cache._k("done_uids", "INBOX")) == {"101"}


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
