"""
Pipeline 编排器
串联邮件抓取 → 清洗 → AI 提取 → RAGFlow 知识库导入全流程
"""
import json
import logging
from pathlib import Path

from config.settings import get_settings

logger = logging.getLogger("pipeline")


class Pipeline:
    """邮件处理流水线 — RAGFlow 知识库为核心"""

    def __init__(self):
        self.cfg = get_settings()

    def run_fetch(self, folder: str = "INBOX", limit: int = 20, since: str | None = None):
        """拉取邮件，保存进度到 Redis"""
        from src.mail.imap_client import IMAPClient
        from src.mail.parser import parse_email
        from src.mail.cleaner import MailCleaner
        from src.storage.redis_cache import MailCache
        from datetime import datetime

        cache = MailCache()
        cleaner = MailCleaner()
        since_dt = datetime.fromisoformat(since) if since else None

        results = []
        with IMAPClient() as client:
            uids = client.search_uids(folder=folder, since=since_dt)
            if not uids:
                logger.info("未找到邮件")
                return results

            uids = uids[-limit:]
            logger.info(f"处理 {len(uids)} 封邮件...")

            count = 0
            for uid, msg in client.fetch_batch(uids, folder=folder):
                try:
                    parsed = parse_email(msg)
                    cleaned_body = cleaner.clean(parsed.body_text, parsed.body_html)
                    is_noise = cleaner.is_noise_email(parsed.subject, parsed.from_addr, cleaned_body)

                    cache.mark_processing(parsed.message_id, uid, folder,
                                          parsed.subject, parsed.from_addr, parsed.date)

                    if is_noise:
                        cache.mark_skipped(parsed.message_id, "噪音邮件")
                        continue

                    result = {
                        "message_id": parsed.message_id,
                        "uid": uid,
                        "folder": folder,
                        "subject": parsed.subject,
                        "from_addr": parsed.from_addr,
                        "from_name": parsed.from_name,
                        "to_addrs": parsed.to_addrs,
                        "cc_addrs": parsed.cc_addrs,
                        "date": parsed.date,
                        "timestamp": parsed.timestamp,
                        "cleaned_body": cleaned_body,
                        "attachments": [{"filename": a["filename"], "path": a["path"]}
                                        for a in parsed.attachments],
                    }
                    results.append(result)
                    cache.mark_done(parsed.message_id)
                    count += 1
                    logger.info(f"  [{count}] {parsed.subject[:50]}")

                except Exception as e:
                    logger.error(f"  [{count+1}] UID {uid}: {e}")

        logger.info(f"已拉取 {len(results)} 封邮件，进度已保存到 Redis")
        return results

    def run_process(self, limit: int = 10):
        """AI 提取 + RAGFlow 知识库导入"""
        from src.attachment.ragflow_client import get_ragflow_client

        rf = get_ragflow_client()
        rf.get_or_create_dataset("MailGraph")
        rf.enable_graphrag()

        # 从已拉取结果中处理（通过 Pipeline 实例传入的 results）
        # 此处从 fetched_mails.json 读取（兼容旧流程）
        fetched_file = self.cfg.resolve_data_path("fetched_mails.json")

        if not fetched_file.exists():
            logger.warning("未找到已拉取的邮件，请先运行 fetch")
            return

        with open(fetched_file, "r", encoding="utf-8") as f:
            mails = json.load(f)

        mails = mails[:limit]
        logger.info(f"处理 {len(mails)} 封邮件...")

        # 尝试 AI 提取
        try:
            from src.ai.extractor import Extractor
            extractor = Extractor()
        except Exception as e:
            logger.warning(f"AI 提取器初始化失败: {e}，跳过 AI 提取")
            extractor = None

        results = []
        doc_ids = []

        for i, mail in enumerate(mails):
            try:
                if extractor:
                    extraction = extractor.extract_from_email(
                        subject=mail.get("subject", ""),
                        body=mail.get("cleaned_body", ""),
                        from_addr=mail.get("from_addr", ""),
                        to_addrs=mail.get("to_addrs", []),
                        date=mail.get("date", ""),
                    )
                else:
                    extraction = {"error": "AI 提取器不可用", "status": "skipped"}

                result = {**mail, "extraction": extraction}
                results.append(result)
                status = "✅" if "error" not in extraction else "⚠️"
                logger.info(f"  [{i+1}] {status} {mail['subject'][:50]}")

                # 上传到 RAGFlow 知识库
                if "error" not in extraction:
                    metadata = {
                        "message_id": mail.get("message_id", ""),
                        "subject": mail.get("subject", ""),
                        "from_addr": mail.get("from_addr", ""),
                        "date": mail.get("date", ""),
                    }
                    doc_id = rf.upload_email_extraction(metadata, extraction)
                    if doc_id:
                        doc_ids.append(doc_id)
                        logger.info(f"     → RAGFlow 文档: {doc_id}")

            except Exception as e:
                logger.error(f"  [{i+1}] ❌ {mail['subject'][:50]}: {e}")
                results.append({**mail, "extraction": {"error": str(e)}})

        # 触发 RAGFlow 解析并等待完成
        if doc_ids:
            logger.info(f"触发 RAGFlow 解析 {len(doc_ids)} 个文档...")
            rf.start_parsing(doc_ids)
            rf.wait_for_parsing(doc_ids)

        # 保存提取结果（用于调试）
        output_file = self.cfg.resolve_data_path("extracted_mails.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.info(f"已保存提取结果到 {output_file}")

        return results
