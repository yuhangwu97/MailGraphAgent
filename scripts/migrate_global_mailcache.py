"""一次性迁移：把按账号隔离的 Redis 数据合并进全局命名空间。

背景
----
系统改为「统一数据池」：邮件 / 会话数据不再按账号隔离，与本就全局的知识图谱一致。
账号仅作为 IMAP 登录凭据保留。本脚本把历史上按账号存的键搬进全局键。

键的去向（源前缀 mailgraph:{account_id}:）
- conv:*                        → mailgraph:conv:*            （会话/记忆，全局）
- done_uids* / progress*        → mailgraph:mbx:{account_id}:* （IMAP 抓取簿记，仍按账号，
                                   因 UID 只在单个邮箱内唯一，全局合并会误去重漏抓）
- 其余（mail/body/ingest_queue/idx/usage/...）→ mailgraph:*   （全局）

合并策略（目标键已存在时）
- set  → SADD 求并集            zset → ZADD 求并集           list → 目标空才 RPUSH（避免重复）
- hash → HSETNX 只补缺字段（保留目标已有值）
- string(memory) → 按 updated_at 保留较新的一份
- string(其它，如 body/done 标记/idx:doc) → 保留目标已有值
目标键不存在时按类型整体拷贝，string 额外沿用源 TTL。

用法
----
  .venv/bin/python scripts/migrate_global_mailcache.py            # dry-run，只打印计划
  .venv/bin/python scripts/migrate_global_mailcache.py --apply    # 真正合并并删除旧键

建议 --apply 前先停掉 API / worker，避免迁移过程中有新键写入旧命名空间。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

# 让脚本能 import 项目模块（从任意 cwd 运行）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import redis  # noqa: E402
from config.settings import get_settings  # noqa: E402
from src.backend.storage.account_store import AccountStore  # noqa: E402

# 只迁移这些已知语义的全局数据键（按 mailgraph:{aid}: 之后的第一段判定）。
# conv → 全局会话；done_uids/progress → 按账号 mbx；其余未知（如已弃用的 ragflow）跳过不动。
GLOBAL_DATA_HEADS = {"mail", "body", "ingest_queue", "idx", "usage"}


def classify(account_id: str, key: str) -> tuple[str | None, str]:
    """返回 (目标键, 分类)。目标键为 None 表示跳过（未知语义的键，保持原样不迁移不删）。"""
    old_prefix = f"mailgraph:{account_id}:"
    suffix = key[len(old_prefix):]
    head = suffix.split(":", 1)[0]
    if head == "conv":
        return "mailgraph:conv:" + suffix[len("conv:"):], "conv(全局)"
    if head in ("done_uids", "progress"):
        return f"mailgraph:mbx:{account_id}:" + suffix, "mbx(按账号)"
    if head in GLOBAL_DATA_HEADS:
        return "mailgraph:" + suffix, "global(全局)"
    return None, f"skip(未知:{head})"


def merge_key(r: redis.Redis, src: str, dest: str, is_memory: bool) -> str:
    """把 src 合并进 dest，返回执行动作描述。幂等：重复跑不会重复累积。"""
    t = r.type(src)
    dest_exists = r.exists(dest)

    if t == "set":
        members = r.smembers(src)
        if members:
            r.sadd(dest, *members)
        return "union-set" if dest_exists else "copy-set"

    if t == "zset":
        items = r.zrange(src, 0, -1, withscores=True)
        if items:
            r.zadd(dest, {m: s for m, s in items})
        return "union-zset" if dest_exists else "copy-zset"

    if t == "list":
        if dest_exists:
            return "skip-list(dest-exists)"  # 消息列表按 uuid 键，正常不会撞
        vals = r.lrange(src, 0, -1)
        if vals:
            r.rpush(dest, *vals)
        return "copy-list"

    if t == "hash":
        fields = r.hgetall(src)
        if not dest_exists:
            if fields:
                r.hset(dest, mapping=fields)
            return "copy-hash"
        for f, v in fields.items():
            r.hsetnx(dest, f, v)  # 只补缺，保留目标已有
        return "merge-hash(hsetnx)"

    if t == "string":
        val = r.get(src)
        if not dest_exists:
            r.set(dest, val)
            pttl = r.pttl(src)
            if pttl and pttl > 0:
                r.pexpire(dest, pttl)
            return "copy-string"
        if is_memory:
            try:
                cur = json.loads(r.get(dest) or "{}")
                new = json.loads(val or "{}")
                if float(new.get("updated_at", 0)) > float(cur.get("updated_at", 0)):
                    r.set(dest, val)
                    return "memory-replace(newer)"
            except Exception:
                pass
            return "memory-keep(existing)"
        return "string-keep(existing)"

    return f"skip-type({t})"


def main() -> None:
    apply = "--apply" in sys.argv
    cfg = get_settings()
    r = redis.Redis(
        host=cfg.redis_host, port=cfg.redis_port, db=cfg.redis_db,
        password=cfg.redis_password or None, decode_responses=True,
        socket_connect_timeout=5,
    )
    try:
        r.ping()
    except Exception as e:
        print(f"✗ 无法连接 Redis: {e}")
        sys.exit(1)

    accounts = AccountStore().list()
    aids = [a["id"] for a in accounts]
    print(f"模式: {'APPLY（会写入并删除旧键）' if apply else 'DRY-RUN（只打印，不改动）'}")
    print(f"账号数: {len(aids)} → {aids}\n")

    if not aids:
        print("没有账号，无需迁移。")
        return

    total = Counter()
    collisions = 0
    actions = Counter()

    for aid in aids:
        keys = list(r.scan_iter(match=f"mailgraph:{aid}:*", count=500))
        if not keys:
            print(f"[{aid}] 无历史键，跳过")
            continue
        cat_count: Counter = Counter()
        for src in keys:
            dest, cat = classify(aid, src)
            cat_count[cat] += 1
            total[cat] += 1
            if dest is None:
                # 未知语义的键（如已弃用的 ragflow）：保持原样，不迁移不删
                if cat_count[cat] <= 3 and not apply:
                    print(f"[{aid}] {src}\n        → 跳过（{cat}），保持原样")
                continue
            dest_exists = bool(r.exists(dest))
            if dest_exists:
                collisions += 1
            if apply:
                is_memory = dest == "mailgraph:conv:memory"
                act = merge_key(r, src, dest, is_memory)
                actions[act] += 1
                r.delete(src)
            else:
                # dry-run 采样打印前几条，避免刷屏
                if cat_count[cat] <= 3:
                    flag = "  ⚠dest已存在(将合并)" if dest_exists else ""
                    print(f"[{aid}] {src}\n        → {dest}{flag}")
        print(f"[{aid}] 键数 {len(keys)}: " + ", ".join(f"{k}={v}" for k, v in cat_count.items()))
        print()

    print("── 汇总 ──")
    for cat, n in total.items():
        print(f"  {cat}: {n}")
    print(f"  目标键已存在(需合并)的键: {collisions}")
    if apply:
        print("\n── 执行动作 ──")
        for act, n in actions.items():
            print(f"  {act}: {n}")
        print("\n✓ 迁移完成，旧的 mailgraph:{account_id}:* 键已删除。")
        print("  验证: GET /api/mails/stats（不带头）应返回合并后的全局总数。")
    else:
        print("\n这是 dry-run。确认无误后加 --apply 正式执行：")
        print("  .venv/bin/python scripts/migrate_global_mailcache.py --apply")


if __name__ == "__main__":
    main()
