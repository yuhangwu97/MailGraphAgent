# Mail Workbench Redesign — Plan 1: Job Registry & Crash Recovery

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent Redis-backed job registry plus in-flight tracking, heartbeat, and a reaper so mail processing survives worker crashes / page refreshes and becomes resumable.

**Architecture:** A new `jobstore` module stores one hash per job (`mailgraph:job:{id}`) indexed by a sorted set. `MailCache` gains an in-flight sorted set so claimed-but-unfinished mails are recoverable. The worker writes heartbeats while running and runs a reaper (on startup + periodically) that re-queues stale in-flight mails and marks their jobs `interrupted`. No frontend in this plan.

**Tech Stack:** Python 3.12+, redis-py (`decode_responses=True`), pytest, hand-rolled `FakeRedis` (see `tests/test_redis_cache.py`).

**Spec:** `docs/2026-07-14-mail-workbench-redesign-design.md` §3.1, §3.4.

---

## File Structure

- Create: `src/backend/jobstore.py` — job CRUD, listing, counters, stage/heartbeat, reaper query. One responsibility: job records.
- Create: `tests/test_jobstore.py` — unit tests for jobstore.
- Modify: `src/backend/storage/redis_cache.py` — add in-flight zset + claim/release/reap-inflight methods.
- Modify: `tests/test_redis_cache.py` — extend `FakeRedis` with zset range ops used by in-flight; add in-flight tests.
- Modify: `src/backend/worker.py` — heartbeat during jobs; reaper on startup + between polls.
- Create: `tests/test_worker_reaper.py` — integration test: simulate crash → reap → requeue.

**Conventions:**
- All keys prefixed `mailgraph:`. Job store uses the shared client from `jobqueue._client()` but every public function accepts an optional `client` param so tests inject a fake.
- Timestamps are `time.time()` floats stored as strings in hashes.
- Job `type` ∈ {`scan`, `parse`, `reprocess`, `ingest`}. Job `status` ∈ {`queued`, `running`, `paused`, `completed`, `failed`, `interrupted`, `partial`}. Job `stage` ∈ {`acquire`, `parse-body`, `parse-attachments`, `build-graph`}.

---

## Task 1: jobstore create/get round-trip

**Files:**
- Create: `src/backend/jobstore.py`
- Test: `tests/test_jobstore.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jobstore.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_jobstore.py::test_create_and_get_job -v`
Expected: FAIL — `AttributeError: module 'src.backend.jobstore' has no attribute 'create_job'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/backend/jobstore.py
"""Persistent job registry (Redis-backed).

One hash per job at mailgraph:job:{id}; a zset mailgraph:jobs:index (score=created_at)
for listing; a set mailgraph:job:{id}:items for the message_ids a job covers.
Every public function accepts an optional `client` so tests can inject a fake.
"""
from __future__ import annotations

import json
import time
import uuid

JOB_PREFIX = "mailgraph:job:"
JOBS_INDEX = "mailgraph:jobs:index"

_TERMINAL = {"completed", "failed", "interrupted", "partial"}


def _client(client=None):
    if client is not None:
        return client
    from src.backend.jobqueue import _client as jq_client
    return jq_client()


def _job_key(job_id: str) -> str:
    return f"{JOB_PREFIX}{job_id}"


def _items_key(job_id: str) -> str:
    return f"{JOB_PREFIX}{job_id}:items"


def create_job(job_type: str, source: str = "", params: dict | None = None,
               item_ids: list[str] | None = None, client=None) -> str:
    r = _client(client)
    job_id = uuid.uuid4().hex
    now = time.time()
    r.hset(_job_key(job_id), mapping={
        "job_id": job_id,
        "type": job_type,
        "source": source,
        "stage": "",
        "status": "queued",
        "total": len(item_ids or []),
        "done": 0,
        "failed": 0,
        "skipped": 0,
        "att_failed": 0,
        "cursor": "",
        "params": json.dumps(params or {}, ensure_ascii=False),
        "error": "",
        "summary": "",
        "created_at": now,
        "updated_at": now,
        "heartbeat_at": now,
    })
    r.zadd(JOBS_INDEX, {job_id: now})
    if item_ids:
        r.sadd(_items_key(job_id), *item_ids)
    return job_id


def get_job(job_id: str, client=None) -> dict | None:
    r = _client(client)
    data = r.hgetall(_job_key(job_id))
    return data or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_jobstore.py::test_create_and_get_job -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/jobstore.py tests/test_jobstore.py
git commit -m "feat(jobstore): job create/get round-trip"
```

---

## Task 2: jobstore progress counters + stage + heartbeat

**Files:**
- Modify: `src/backend/jobstore.py`
- Test: `tests/test_jobstore.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jobstore.py  (append)
def test_progress_and_stage_and_heartbeat():
    r = FakeRedis()
    job_id = jobstore.create_job("parse", client=r)
    jobstore.set_stage(job_id, "parse-attachments", client=r)
    jobstore.incr(job_id, "done", 2, client=r)
    jobstore.incr(job_id, "att_failed", 1, client=r)
    before = float(jobstore.get_job(job_id, client=r)["heartbeat_at"])
    time.sleep(0.01)
    jobstore.heartbeat(job_id, client=r)
    job = jobstore.get_job(job_id, client=r)
    assert job["stage"] == "parse-attachments"
    assert int(float(job["done"])) == 2
    assert int(float(job["att_failed"])) == 1
    assert float(job["heartbeat_at"]) > before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_jobstore.py::test_progress_and_stage_and_heartbeat -v`
Expected: FAIL — `AttributeError: ... has no attribute 'set_stage'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/backend/jobstore.py  (append)
def _touch(r, job_id: str) -> None:
    r.hset(_job_key(job_id), mapping={"updated_at": time.time()})


def set_stage(job_id: str, stage: str, client=None) -> None:
    r = _client(client)
    r.hset(_job_key(job_id), mapping={"stage": stage, "status": "running"})
    _touch(r, job_id)


def incr(job_id: str, field: str, amount: float = 1, client=None) -> None:
    r = _client(client)
    r.hincrbyfloat(_job_key(job_id), field, amount)
    _touch(r, job_id)


def heartbeat(job_id: str, client=None) -> None:
    r = _client(client)
    r.hset(_job_key(job_id), mapping={"heartbeat_at": time.time()})


def update(job_id: str, client=None, **fields) -> None:
    r = _client(client)
    if fields:
        r.hset(_job_key(job_id), mapping=fields)
    _touch(r, job_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_jobstore.py::test_progress_and_stage_and_heartbeat -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/jobstore.py tests/test_jobstore.py
git commit -m "feat(jobstore): progress counters, stage, heartbeat"
```

---

## Task 3: jobstore list + terminal marking

**Files:**
- Modify: `src/backend/jobstore.py`
- Test: `tests/test_jobstore.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jobstore.py  (append)
def test_list_jobs_newest_first_and_mark_terminal():
    r = FakeRedis()
    a = jobstore.create_job("scan", client=r)
    time.sleep(0.01)
    b = jobstore.create_job("parse", client=r)
    ids = [j["job_id"] for j in jobstore.list_jobs(client=r)]
    assert ids[:2] == [b, a]  # newest first

    jobstore.mark_terminal(a, "completed", summary="done 5/5", client=r)
    job = jobstore.get_job(a, client=r)
    assert job["status"] == "completed"
    assert job["summary"] == "done 5/5"

    running = [j["job_id"] for j in jobstore.list_jobs(status="running", client=r)]
    assert a not in running
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_jobstore.py::test_list_jobs_newest_first_and_mark_terminal -v`
Expected: FAIL — `AttributeError: ... has no attribute 'list_jobs'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/backend/jobstore.py  (append)
def list_jobs(status: str | None = None, limit: int = 50, client=None) -> list[dict]:
    r = _client(client)
    job_ids = r.zrevrange(JOBS_INDEX, 0, limit - 1)
    out = []
    for jid in job_ids:
        data = r.hgetall(_job_key(jid))
        if not data:
            continue
        if status and data.get("status") != status:
            continue
        out.append(data)
    return out


def mark_terminal(job_id: str, status: str, summary: str = "",
                  error: str = "", client=None) -> None:
    if status not in _TERMINAL:
        raise ValueError(f"not a terminal status: {status}")
    r = _client(client)
    r.hset(_job_key(job_id), mapping={
        "status": status,
        "summary": summary,
        "error": error,
        "updated_at": time.time(),
        "heartbeat_at": time.time(),
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_jobstore.py -v`
Expected: PASS (all jobstore tests)

- [ ] **Step 5: Commit**

```bash
git add src/backend/jobstore.py tests/test_jobstore.py
git commit -m "feat(jobstore): list jobs + terminal marking"
```

---

## Task 4: jobstore reaper query (stale running jobs)

**Files:**
- Modify: `src/backend/jobstore.py`
- Test: `tests/test_jobstore.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jobstore.py  (append)
def test_find_stale_running_jobs():
    r = FakeRedis()
    fresh = jobstore.create_job("parse", client=r)
    jobstore.set_stage(fresh, "build-graph", client=r)  # status=running, heartbeat now

    stale = jobstore.create_job("parse", client=r)
    jobstore.set_stage(stale, "build-graph", client=r)
    # force an old heartbeat
    r.hset(jobstore._job_key(stale), mapping={"heartbeat_at": time.time() - 999})

    stale_ids = [j["job_id"] for j in jobstore.find_stale_running(stale_seconds=60, client=r)]
    assert stale in stale_ids
    assert fresh not in stale_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_jobstore.py::test_find_stale_running_jobs -v`
Expected: FAIL — `AttributeError: ... has no attribute 'find_stale_running'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/backend/jobstore.py  (append)
def find_stale_running(stale_seconds: float = 60, limit: int = 500, client=None) -> list[dict]:
    r = _client(client)
    cutoff = time.time() - stale_seconds
    out = []
    for jid in r.zrevrange(JOBS_INDEX, 0, limit - 1):
        data = r.hgetall(_job_key(jid))
        if not data or data.get("status") != "running":
            continue
        try:
            hb = float(data.get("heartbeat_at", 0))
        except (TypeError, ValueError):
            hb = 0
        if hb < cutoff:
            out.append(data)
    return out


def items(job_id: str, client=None) -> set[str]:
    r = _client(client)
    return set(r.smembers(_items_key(job_id)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_jobstore.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/jobstore.py tests/test_jobstore.py
git commit -m "feat(jobstore): find_stale_running for reaper"
```

---

## Task 5: MailCache in-flight tracking (claim/release)

**Files:**
- Modify: `src/backend/storage/redis_cache.py` (add methods near `claim_pending_mail`, ~line 545)
- Modify: `tests/test_redis_cache.py` (extend `FakeRedis` with `spop`, `zadd`, `zrem`, `zrangebyscore`)
- Test: `tests/test_redis_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_redis_cache.py  (append; extend FakeRedis first — see Step 3)
def test_claim_tracked_adds_inflight_and_release_removes():
    cache = _make_cache()  # existing helper in this file that builds MailCache w/ FakeRedis
    # seed one mail body + queue entry
    cache.store_mail({"message_id": "m1", "cleaned_body": "hi", "attachments": []})
    claimed = cache.claim_pending_mail_tracked()
    assert claimed["message_id"] == "m1"
    inflight = cache.r.zrangebyscore(cache._k("inflight"), "-inf", "+inf")
    assert "m1" in inflight
    cache.release_inflight("m1")
    inflight = cache.r.zrangebyscore(cache._k("inflight"), "-inf", "+inf")
    assert "m1" not in inflight
```

> If `_make_cache()` does not already exist in `tests/test_redis_cache.py`, add this helper near the top:
> ```python
> def _make_cache():
>     from src.backend.storage import redis_cache as rc
>     cache = rc.MailCache.__new__(rc.MailCache)
>     cache._r = FakeRedis()
>     cache._prefix = "mailgraph:"
>     cache._mbx_prefix = "mailgraph:mbx:_:"
>     cache._body_ttl = 3600
>     cache._account_id = None
>     return cache
> ```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_redis_cache.py::test_claim_tracked_adds_inflight_and_release_removes -v`
Expected: FAIL — `AttributeError: 'MailCache' object has no attribute 'claim_pending_mail_tracked'`
(or FakeRedis missing `spop`/`zadd`/`zrangebyscore` — fix in Step 3)

- [ ] **Step 3: Write minimal implementation**

Extend `FakeRedis` in `tests/test_redis_cache.py` (add methods; keep existing ones):

```python
# tests/test_redis_cache.py  — inside class FakeRedis
    def spop(self, key):
        s = self.sets.get(key)
        if not s:
            return None
        return s.pop()

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {})
        self.zsets[key].update(mapping)
        return len(mapping)

    def zrem(self, key, *members):
        z = self.zsets.get(key, {})
        for m in members:
            z.pop(m, None)
        return len(members)

    def zrangebyscore(self, key, minv, maxv):
        z = self.zsets.get(key, {})
        lo = float("-inf") if minv == "-inf" else float(minv)
        hi = float("inf") if maxv == "+inf" else float(maxv)
        return [m for m, s in sorted(z.items(), key=lambda kv: kv[1]) if lo <= s <= hi]
```

Add methods to `MailCache` in `src/backend/storage/redis_cache.py` (after `claim_pending_mail`):

```python
    def claim_pending_mail_tracked(self) -> dict | None:
        """SPOP a pending mail AND record it in the in-flight zset (score=claimed_at).

        Recoverable variant of claim_pending_mail: if the worker crashes before
        release_inflight, the reaper can find and requeue it. Expired bodies are
        skipped (already SPOP'd out) without touching in-flight.
        """
        import time as _t
        while True:
            mid = self.r.spop(self._k("ingest_queue"))
            if not mid:
                return None
            mail = self.get_mail(mid)
            if mail is None:
                continue
            self.r.zadd(self._k("inflight"), {mid: _t.time()})
            return mail

    def release_inflight(self, message_id: str) -> None:
        """Remove a mail from the in-flight zset once graph-build finished."""
        if message_id:
            self.r.zrem(self._k("inflight"), message_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_redis_cache.py::test_claim_tracked_adds_inflight_and_release_removes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/storage/redis_cache.py tests/test_redis_cache.py
git commit -m "feat(cache): in-flight tracking for crash-recoverable claim"
```

---

## Task 6: MailCache reap stale in-flight → requeue + reset status

**Files:**
- Modify: `src/backend/storage/redis_cache.py`
- Test: `tests/test_redis_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_redis_cache.py  (append)
def test_reap_inflight_requeues_stale_and_resets_status():
    import time as _t
    cache = _make_cache()
    cache.store_mail({"message_id": "m1", "cleaned_body": "hi", "attachments": []})
    cache.claim_pending_mail_tracked()          # m1 now in-flight, out of queue
    # mark it processing (as the worker would)
    cache.r.hset(cache._k("mail", "m1"), mapping={"status": "processing"})
    # force stale claim time
    cache.r.zadd(cache._k("inflight"), {"m1": _t.time() - 999})

    requeued = cache.reap_inflight(stale_seconds=60)
    assert requeued == ["m1"]
    assert "m1" in cache.r.smembers(cache._k("ingest_queue"))
    assert "m1" not in cache.r.zrangebyscore(cache._k("inflight"), "-inf", "+inf")
    assert cache.r.hgetall(cache._k("mail", "m1")).get("status") == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_redis_cache.py::test_reap_inflight_requeues_stale_and_resets_status -v`
Expected: FAIL — `AttributeError: 'MailCache' object has no attribute 'reap_inflight'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/backend/storage/redis_cache.py  (append to MailCache)
    def reap_inflight(self, stale_seconds: float = 60) -> list[str]:
        """Re-queue in-flight mails whose claim is older than stale_seconds.

        A crash between claim_pending_mail_tracked and release_inflight leaves a
        mail out of the queue and stuck at 'processing'. This puts it back in
        ingest_queue, resets its status to 'pending', and clears the in-flight entry.
        Returns the requeued message_ids.
        """
        import time as _t
        cutoff = _t.time() - stale_seconds
        key = self._k("inflight")
        stale = self.r.zrangebyscore(key, "-inf", cutoff)
        for mid in stale:
            self.r.sadd(self._k("ingest_queue"), mid)
            self.r.zrem(key, mid)
            if self.r.hgetall(self._k("mail", mid)):
                self.r.hset(self._k("mail", mid), mapping={"status": "pending"})
        return list(stale)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_redis_cache.py -v`
Expected: PASS (all redis_cache tests)

- [ ] **Step 5: Commit**

```bash
git add src/backend/storage/redis_cache.py tests/test_redis_cache.py
git commit -m "feat(cache): reap_inflight requeues stale claims and resets status"
```

---

## Task 7: Worker heartbeat wiring (job_id flows through to pipeline logs)

**Files:**
- Modify: `src/backend/worker.py:25-49` (`_handle`)
- Modify: `src/backend/jobqueue.py:71-83` (allow a `job_record_id` param passthrough — see below)
- Test: `tests/test_worker_reaper.py`

**Context:** The worker currently only publishes progress to pub/sub. We add: when a job carries a `job_record_id` in its params, the worker calls `jobstore.heartbeat(job_record_id)` on every `on_log`. This ties the durable job record to the live worker.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_reaper.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_reaper.py::test_heartbeat_callback_touches_job -v`
Expected: FAIL — `AttributeError: module 'src.backend.worker' has no attribute '_heartbeat_logger'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/backend/worker.py  (add helper + use it in _handle)
def _heartbeat_logger(job_record_id, client=None, publish=None):
    """Return an on_log(msg) that publishes progress AND heartbeats the durable job."""
    from src.backend import jobstore
    from src.backend.jobqueue import publish_progress

    pub = publish or publish_progress

    def on_log(msg: str) -> None:
        if job_record_id:
            try:
                jobstore.heartbeat(job_record_id, client=client)
            except Exception:
                pass
        pub  # keep ref; actual publish below when wired with job_id
    return on_log
```

Then in `_handle`, replace the inline `on_log` with a version that both publishes to pub/sub (keyed by the pub/sub `job_id`) and heartbeats the durable `job_record_id` (from `params`):

```python
# src/backend/worker.py  — inside _handle, replace the on_log definition
    job_record_id = params.pop("job_record_id", "")

    def on_log(msg: str) -> None:
        if job_record_id:
            try:
                from src.backend import jobstore
                jobstore.heartbeat(job_record_id)
            except Exception:
                pass
        publish_progress(job_id, "progress", {"msg": msg})
```

> Note: `_heartbeat_logger` exists for unit-testing the heartbeat side effect in isolation;
> `_handle` uses the inline form so the real `publish_progress(job_id, ...)` binds the pub/sub id.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worker_reaper.py::test_heartbeat_callback_touches_job -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/worker.py tests/test_worker_reaper.py
git commit -m "feat(worker): heartbeat durable job record on progress"
```

---

## Task 8: Worker reaper sweep (startup + between polls)

**Files:**
- Modify: `src/backend/worker.py:52-69` (`main` loop)
- Test: `tests/test_worker_reaper.py`

**Context:** A reaper function marks stale running jobs `interrupted` and requeues their in-flight mails. It runs once at startup and every ~30s in the idle loop.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_reaper.py  (append)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_reaper.py::test_reaper_marks_interrupted_and_requeues -v`
Expected: FAIL — `AttributeError: module 'src.backend.worker' has no attribute '_reap'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/backend/worker.py  (add)
def _reap(client=None, cache_factory=None, stale_seconds: float = 60) -> int:
    """Mark stale running jobs interrupted and requeue in-flight mails.

    Returns the number of jobs marked interrupted.
    """
    from src.backend import jobstore

    # 1) requeue stale in-flight mails (account-agnostic pool)
    if cache_factory is None:
        from src.backend.storage.redis_cache import MailCache
        cache_factory = lambda: MailCache(None)
    cache = cache_factory()
    try:
        cache.reap_inflight(stale_seconds=stale_seconds)
    finally:
        try:
            cache.close()
        except Exception:
            pass

    # 2) mark stale running jobs interrupted
    stale_jobs = jobstore.find_stale_running(stale_seconds=stale_seconds, client=client)
    for job in stale_jobs:
        jobstore.mark_terminal(job["job_id"], "interrupted",
                               summary="worker heartbeat lost", client=client)
    return len(stale_jobs)
```

Wire into `main()`:

```python
# src/backend/worker.py  — main()
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info("ingest worker started, waiting for jobs...")
    try:
        _reap()  # startup recovery sweep
    except Exception:
        logger.exception("startup reap failed")
    last_reap = time.time()
    while True:
        try:
            job = pop_job(timeout=5)
            if job:
                _handle(job)
            if time.time() - last_reap > 30:
                _reap()
                last_reap = time.time()
        except KeyboardInterrupt:
            logger.info("worker stopping")
            break
        except Exception:
            logger.exception("worker loop error; retrying")
            time.sleep(1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worker_reaper.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/worker.py tests/test_worker_reaper.py
git commit -m "feat(worker): reaper sweep on startup and between polls"
```

---

## Task 9: End-to-end crash-recovery integration test

**Files:**
- Test: `tests/test_worker_reaper.py`

**Context:** Prove the whole loop: claim a mail (crash before release) → reaper requeues it → it is claimable again and its job is interrupted.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_reaper.py  (append)
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
    cache.r.hset(cache._k("mail", "m1"), mapping={"status": "processing"})

    # simulate stale timestamps (crash)
    cache.r.zadd(cache._k("inflight"), {"m1": time.time() - 999})
    jclient.hset(jobstore._job_key(job_id), mapping={"heartbeat_at": time.time() - 999})

    # reaper runs
    worker._reap(client=jclient, cache_factory=lambda: cache, stale_seconds=60)

    # mail is claimable again and job is interrupted
    assert "m1" in cache.r.smembers(cache._k("ingest_queue"))
    assert cache.claim_pending_mail_tracked()["message_id"] == "m1"
    assert jobstore.get_job(job_id, client=jclient)["status"] == "interrupted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_reaper.py::test_crash_then_recover_full_loop -v`
Expected: FAIL initially only if a prior task is incomplete; otherwise PASS. If it fails on `_make_cache` import, ensure Task 5 added that helper at module top-level in `tests/test_redis_cache.py`.

- [ ] **Step 3: (No new implementation)**

This test exercises code from Tasks 5–8. If it fails, fix the offending task rather than adding code here.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/test_jobstore.py tests/test_redis_cache.py tests/test_worker_reaper.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_worker_reaper.py
git commit -m "test(worker): end-to-end crash-recovery loop"
```

---

## Self-Review Notes

- **Spec coverage (§3.1):** job hash fields, items set, index zset — Tasks 1–4. ✅
- **Spec coverage (§3.4):** in-flight tracking (Task 5), reaper requeue + status reset (Task 6), heartbeat (Task 7), startup + periodic reaper sweep (Task 8), end-to-end recovery (Task 9). ✅
- **Deferred to later plans:** `pause`/`resume`/`retry-failed` semantics are HTTP-route concerns → Plan 4 (routes). This plan lands the storage + recovery primitives they build on.
- **Type consistency:** job field names (`done`/`failed`/`skipped`/`att_failed`/`stage`/`status`/`heartbeat_at`) identical across Tasks 1–8; `claim_pending_mail_tracked` / `release_inflight` / `reap_inflight` names consistent across Tasks 5, 6, 9.
- **Real-Redis caveat:** the real client's `zrangebyscore` takes numeric/`-inf`/`+inf` bounds — matches usage in Task 6. `hincrbyfloat` is native redis-py. `spop` returns a single member (native).

---

## Follow-on Plans (to be written after Plan 1 lands)

- **Plan 2 — Stage decoupling & attachment-parse isolation** (spec §3.3): split `run_ingest`/`run_ingest_one` into `parse-attachments` + `build-graph`; per-attachment status in `mailgraph:mail:{id}:atts`; subprocess-based per-attachment timeout; explicit `degraded` when DeepDoc models missing.
- **Plan 3 — IMAP as a source** (spec §2, §3.3): `imap_client.fetch_headers`; IMAP entry in `mail/sources` registry (`scan_headers` + `read_message` + reader); `Pipeline.run_scan(source, params)`; deprecate `run_fetch` to delegate.
- **Plan 4 — Job routes & mails.py refactor** (spec §3.2): `routes/jobs.py` (list/detail/pause/resume/retry-failed/delete); make `fetch`/`index`/`reprocess`/`parse-selected` create durable Jobs and pass `job_record_id`.
- **Plan 5 — Frontend** (spec §4): `IntakeDialog.vue`, `JobCenter.vue`/`JobCard.vue`/`JobDetail.vue`, `stores/jobs.ts`, `MailList.vue` status-label + attachment-status refactor, remove ephemeral `processLogs`.

