import time

from src.backend import jobstore
from tests.test_jobstore import FakeRedis


def test_heartbeat_callback_touches_job(monkeypatch):
    r = FakeRedis()
    job_id = jobstore.create_job("ingest", client=r)
    jobstore.set_stage(job_id, "build-graph", client=r)
    r.hset(jobstore._job_key(job_id), mapping={"heartbeat_at": time.time() - 100})

    from src.backend import worker
    # build the on_log the worker uses, wired to our fake client
    cb = worker._heartbeat_logger(job_id, client=r, publish=lambda *a, **k: None)
    cb("processing mail 3")
    hb = float(jobstore.get_job(job_id, client=r)["heartbeat_at"])
    assert hb > time.time() - 5


def test_reaper_marks_interrupted_and_requeues():
    import time
    from src.backend import jobstore, worker
    from tests.test_jobstore import FakeRedis

    r = FakeRedis()
    job_id = jobstore.create_job("parse", client=r)
    jobstore.set_stage(job_id, "build-graph", client=r)
    r.hset(jobstore._job_key(job_id), mapping={"heartbeat_at": time.time() - 999})

    calls = {"reaped": []}

    class FakeCache:
        def reap_inflight(self, stale_seconds=60):
            calls["reaped"].append(stale_seconds)
            return ["m1", "m2"]
        def close(self):
            pass

    n = worker._reap(client=r, cache_factory=lambda: FakeCache(), stale_seconds=60)
    assert jobstore.get_job(job_id, client=r)["status"] == "interrupted"
    assert calls["reaped"] == [60]
    assert n == 1  # one job interrupted


def test_crash_then_recover_full_loop():
    import time
    from src.backend import jobstore, worker
    from tests.test_jobstore import FakeRedis
    from tests.test_redis_cache import _make_cache

    jclient = FakeRedis()
    cache = _make_cache()

    # a job is running, building graph
    job_id = jobstore.create_job("parse", client=jclient)
    jobstore.set_stage(job_id, "build-graph", client=jclient)

    # a mail is claimed but the worker "crashes" before release
    cache.store_mail({"message_id": "m1", "cleaned_body": "x", "attachments": []})
    cache.claim_pending_mail_tracked()
    cache._r.hset(cache._k("mail", "m1"), mapping={"status": "processing"})

    # simulate stale timestamps (crash)
    cache._r.zadd(cache._k("inflight"), {"m1": time.time() - 999})
    jclient.hset(jobstore._job_key(job_id), mapping={"heartbeat_at": time.time() - 999})

    # reaper runs
    worker._reap(client=jclient, cache_factory=lambda: cache, stale_seconds=60)

    # mail is claimable again and job is interrupted
    assert "m1" in cache._r.smembers(cache._k("ingest_queue"))
    assert cache.claim_pending_mail_tracked()["message_id"] == "m1"
    assert jobstore.get_job(job_id, client=jclient)["status"] == "interrupted"
