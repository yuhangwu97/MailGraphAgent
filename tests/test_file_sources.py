"""
文件邮件源 + indexed 状态 的测试
================================
覆盖：eml scan/read 往返、build_email_message 附件、expand_paths、
MailCache.store_indexed/list_indexed/count_indexed 去重与索引、
Pipeline.run_index_files 端到端（需本地 Redis）。
"""
import json
from pathlib import Path

import pytest

from src.backend.mail.sources import (
    EXTENSIONS,
    expand_paths,
    read_message,
    scan_file,
)
from src.backend.mail.sources.base import HeaderRecord, build_email_message
from src.backend.mail.parser import parse_email


SAMPLE_EML = """Message-ID: <roundtrip-1@example.com>
Subject: 项目预算讨论 Q3
From: Alice Wang <alice@corp.com>
To: bob@corp.com, carol@corp.com
Date: Mon, 06 Jul 2026 10:00:00 +0800
Content-Type: text/plain; charset=utf-8

请查收 Q3 预算草案，周五前反馈。
"""

SAMPLE_EML_NO_ID = """Subject: 无 Message-ID 的邮件
From: dave@corp.com
Date: Tue, 07 Jul 2026 09:00:00 +0800
Content-Type: text/plain; charset=utf-8

这封没有 Message-ID 头。
"""


def _write(dirpath: Path, name: str, content: str) -> Path:
    p = dirpath / name
    p.write_text(content, encoding="utf-8")
    return p


# ── 文件源 ──

def test_extensions_cover_targets():
    assert {".eml", ".msg", ".pst", ".ost"} <= EXTENSIONS


def test_expand_paths_recurses_and_filters(tmp_path):
    _write(tmp_path, "a.eml", SAMPLE_EML)
    sub = tmp_path / "sub"
    sub.mkdir()
    _write(sub, "b.eml", SAMPLE_EML_NO_ID)
    _write(tmp_path, "note.txt", "ignore me")
    found = {p.name for p in expand_paths([str(tmp_path)])}
    assert found == {"a.eml", "b.eml"}


def test_eml_scan_read_roundtrip(tmp_path):
    p = _write(tmp_path, "mail.eml", SAMPLE_EML)
    recs = list(scan_file(str(p)))
    assert len(recs) == 1
    rec = recs[0]
    assert rec.message_id == "<roundtrip-1@example.com>"
    assert rec.subject == "项目预算讨论 Q3"
    assert rec.from_addr == "alice@corp.com"
    assert rec.from_name == "Alice Wang"
    assert rec.locator["source_type"] == "eml"

    msg = read_message(rec.locator)
    parsed = parse_email(msg)
    assert parsed.message_id == "<roundtrip-1@example.com>"
    assert parsed.subject == "项目预算讨论 Q3"
    assert "Q3 预算草案" in parsed.body


def test_eml_missing_message_id_is_synthesized(tmp_path):
    p = _write(tmp_path, "noid.eml", SAMPLE_EML_NO_ID)
    rec = next(iter(scan_file(str(p))))
    assert rec.message_id.startswith("<file-") and rec.message_id.endswith("@mailgraph.local>")


def test_build_email_message_with_attachment_is_parseable():
    msg = build_email_message(
        message_id="<att-1@example.com>",
        subject="带附件",
        from_header="Eve <eve@corp.com>",
        date_header="Mon, 06 Jul 2026 10:00:00 +0800",
        plain_body="正文见附件",
        attachments=[{"filename": "report.pdf", "data": b"%PDF-1.4 fake",
                      "mime_type": "application/pdf"}],
    )
    parsed = parse_email(msg, download_dir=None)
    assert parsed.subject == "带附件"
    assert "正文见附件" in parsed.body


def test_read_message_unknown_source_type_raises():
    with pytest.raises(ValueError):
        read_message({"source_type": "zzz", "path": "/nope"})


# ── MailCache indexed（需本地 Redis）──

TEST_ACCOUNT = "pytest_fileimport"


def _redis_available() -> bool:
    try:
        from src.backend.storage.redis_cache import MailCache
        c = MailCache(TEST_ACCOUNT)
        c.r.ping()
        return True
    except Exception:
        return False


requires_redis = pytest.mark.skipif(not _redis_available(), reason="Redis 未运行")


@pytest.fixture
def cache():
    from src.backend.storage.redis_cache import MailCache
    c = MailCache(TEST_ACCOUNT)
    # 清空该测试命名空间
    for key in c.r.scan_iter(match=c._prefix + "*"):
        c.r.delete(key)
    yield c
    for key in c.r.scan_iter(match=c._prefix + "*"):
        c.r.delete(key)
    c.close()


def _rec(mid, subject="s", date="2026-07-06T10:00:00+08:00", folder="收件箱"):
    return HeaderRecord(
        message_id=mid, subject=subject, from_addr="a@corp.com", from_name="A",
        date=date, folder=folder, has_attachment=False,
        locator={"source_type": "pst", "path": "/x/archive.pst",
                 "folder_path": [0], "index": 1},
    )


@requires_redis
def test_store_indexed_roundtrip_and_dedup(cache):
    assert cache.store_indexed(_rec("<m1@corp.com>")) is True
    # 重复存入应去重
    assert cache.store_indexed(_rec("<m1@corp.com>")) is False
    assert cache.store_indexed(_rec("<m2@corp.com>", date="2026-07-05T09:00:00+08:00")) is True

    assert cache.count_indexed() == 2
    items = cache.list_indexed(limit=10)
    assert [i["message_id"] for i in items] == ["<m1@corp.com>", "<m2@corp.com>"]  # date desc
    # locator 已存
    st = cache.get_mail_state("<m1@corp.com>")
    assert st["status"] == "indexed"
    assert json.loads(st["source_locator"])["path"] == "/x/archive.pst"
    # get_stats 有 indexed 桶
    assert cache.get_stats().get("indexed") == 2


@requires_redis
def test_run_index_files_end_to_end(tmp_path, cache):
    from src.backend.storage.account_store import AccountStore
    from src.backend.pipeline import Pipeline

    _write(tmp_path, "a.eml", SAMPLE_EML)
    _write(tmp_path, "b.eml", SAMPLE_EML_NO_ID)

    store = AccountStore()
    # 直接塞一条测试账号（键=TEST_ACCOUNT），便于 Pipeline 按 id 解析
    store._r.hset(
        "mailgraph:accounts", TEST_ACCOUNT,
        json.dumps({"id": TEST_ACCOUNT, "label": "pytest", "imap_server": "",
                    "imap_port": 993, "email_user": "pytest@local", "email_pass": "",
                    "provider": ""}, ensure_ascii=False),
    )
    try:
        stats = Pipeline(TEST_ACCOUNT).run_index_files([str(tmp_path)])
        assert stats["files"] == 2
        assert stats["scanned"] == 2
        assert stats["indexed"] == 2
        assert cache.count_indexed() == 2
    finally:
        store._r.hdel("mailgraph:accounts", TEST_ACCOUNT)
        store.close()
