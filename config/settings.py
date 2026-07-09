"""
Pydantic 配置管理
所有路径参数化，切换环境只需改 .env
"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # ── OpenAI API ──
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="http://43.160.245.179:8080", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-5.4", alias="OPENAI_MODEL")
    openai_review_model: str = Field(default="gpt-5.4", alias="OPENAI_REVIEW_MODEL")
    model_reasoning_effort: str = Field(default="xhigh", alias="MODEL_REASONING_EFFORT")

    # ── IMAP 邮件 ──
    imap_server: str = Field(default="imap.gmail.com", alias="IMAP_SERVER")
    imap_port: int = Field(default=993, alias="IMAP_PORT")
    email_user: str = Field(default="", alias="EMAIL_USER")
    email_pass: str = Field(default="", alias="EMAIL_PASS")

    # ── Redis (进度追踪) ──
    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")
    redis_password: str = Field(default="", alias="REDIS_PASSWORD")

    # ── RAGFlow ──
    ragflow_base_url: str = Field(default="http://localhost:9380", alias="RAGFLOW_BASE_URL")
    ragflow_api_key: str = Field(default="", alias="RAGFLOW_API_KEY")
    # 空 = 用 RAGFlow 租户默认 embedding；否则须为 <model_name>@<provider> 格式
    ragflow_embedding_model: str = Field(default="", alias="RAGFLOW_EMBEDDING_MODEL")

    # ── RAGFlow GraphRAG ──
    ragflow_dataset_name: str = Field(default="MailGraph", alias="RAGFLOW_DATASET_NAME")
    # GraphRAG 抽取提示词方案: light(LightRAG, 省 token) | general(微软 GraphRAG, 含社区报告)
    ragflow_graphrag_method: str = Field(default="light", alias="RAGFLOW_GRAPHRAG_METHOD")
    # GraphRAG 要抽取的实体类型（跨文档统一建图）
    ragflow_entity_types: list[str] = Field(
        default=["organization", "person", "project"],
        description="GraphRAG 抽取的实体类型",
    )
    # 实体消解开关：合并 dataset 内相似实体（跨文档去重/对齐）
    ragflow_graphrag_resolution: bool = Field(default=True, alias="RAGFLOW_GRAPHRAG_RESOLUTION")
    # 社区报告：light 方案通常不需要，关掉省 token
    ragflow_graphrag_community: bool = Field(default=False, alias="RAGFLOW_GRAPHRAG_COMMUNITY")

    # ── 应用配置 ──
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ── 邮件处理配置 ──
    imap_batch_size: int = Field(default=50, description="每批拉取邮件数")
    imap_search_chunk_months: int = Field(default=1, description="按几个月分片搜索")
    imap_request_delay_min: float = Field(default=0.5, description="每批次最小延时(秒)")
    imap_request_delay_max: float = Field(default=2.0, description="每批次最大延时(秒)")
    max_attachment_size_mb: int = Field(default=50, description="单附件最大体积(MB)")
    allowed_attachment_extensions: list[str] = Field(
        default=[".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".csv", ".zip", ".rar"],
        description="允许处理的附件后缀",
    )

    # ── 邮件抓取配置 ──
    mail_fetch_limit: int = Field(default=100, description="首次抓取邮件数量限制")
    start_date: str = Field(default="2024-01-01", description="邮件开始日期（YYYY-MM-DD）")
    end_date: str = Field(default="2024-12-31", description="邮件结束日期（YYYY-MM-DD）")
    email_provider: str = Field(default="qq", description="邮箱提供商: qq, gmail, aliyun")
    cache_db_path: str = Field(default="./data/cache.db", description="SQLite 缓存数据库路径")
    attachments_dir: str = Field(default="./data/attachments", description="附件保存目录")
    max_retries: int = Field(default=3, description="IMAP 连接最大重试次数")

    # ── 正文暂存 (Redis) ──
    # fetch 与 ingest 之间在 Redis 暂存正文的存活天数（即用即删，跑完批即过期）
    fetched_body_ttl_days: int = Field(default=7, description="Redis 暂存正文存活天数")

    # ── 文本处理配置 ──
    enable_noise_filter: bool = Field(default=True, description="是否启用噪音过滤")
    enable_html_stripping: bool = Field(default=True, description="是否启用 HTML 剥离")
    enable_history_cleanup: bool = Field(default=True, description="是否启用历史邮件清理")
    max_text_length: int = Field(default=8000, description="邮件正文最大长度")

    # ── AI 提取配置 ──
    ai_max_tokens: int = Field(default=2000, description="AI 单次请求最大输出 token")
    ai_temperature: float = Field(default=0.1, description="AI 提取温度")
    ai_max_retries: int = Field(default=3, description="AI 请求最大重试次数")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def resolve_data_path(self, relative_path: str) -> Path:
        """将相对路径解析为 data_dir 下的绝对路径"""
        p = self.data_dir / relative_path
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def setup_directories(self):
        """创建必要的目录"""
        import logging
        logger = logging.getLogger(__name__)
        for directory in [self.data_dir, self.attachments_dir, "data/logs"]:
            Path(directory).mkdir(parents=True, exist_ok=True)
            logger.info(f"✓ 目录已创建/确认存在: {directory}")

    def get_imap_config(self) -> dict:
        """获取 IMAP 连接配置"""
        return {
            "server": self.imap_server,
            "port": self.imap_port,
            "email": self.email_user,
            "password": self.email_pass,
            "provider": self.email_provider,
        }


# 全局单例
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
