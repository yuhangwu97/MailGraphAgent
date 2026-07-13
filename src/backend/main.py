"""
MailGraphAgent 主程序 - CLI 入口
================================
子命令：fetch(拉取→Redis) / ingest(Redis→LightRAG 知识图谱) /
        full-pipeline(fetch+ingest) / web(前端) / check(系统检查)
"""
import logging
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from config.settings import get_settings
from src.backend.pipeline import Pipeline
from src.backend.storage.redis_cache import MailCache

# ── 日志配置 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("mailgraph.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

app = typer.Typer(help="MailGraphAgent - 邮件图谱分析系统")
console = Console()
settings = get_settings()


def log_section(title: str):
    console.print(Panel(title, style="bold blue"))


def log_success(msg: str):
    console.print(f"✓ {msg}", style="green")


def log_error(msg: str):
    console.print(f"✗ {msg}", style="red")


# ════════════════════════════════════════════════════════════════════
# fetch：拉取邮件 → 清洗 → Redis 暂存
# ════════════════════════════════════════════════════════════════════


def _enqueue(job_type: str, account: Optional[str], params: dict) -> None:
    """把任务入队给 worker 处理（统一走 Redis 队列，与前端触发一致）。"""
    from src.backend.jobqueue import enqueue_job
    account_id = Pipeline(account).account_id
    job_id = enqueue_job(job_type, account_id, params)
    log_success(f"已提交 {job_type} 任务给 worker（job {job_id[:8]}）。"
                f"确保 worker 在运行： python -m src.backend.worker")


@app.command("fetch")
def fetch_emails(
    limit: int = typer.Option(100, help="拉取邮件数量上限"),
    folder: str = typer.Option("INBOX", help="邮件夹名称"),
    since: Optional[str] = typer.Option(None, help="只拉取该日期之后 (YYYY-MM-DD)"),
    account: Optional[str] = typer.Option(None, help="账号 id，缺省用默认账号"),
):
    """拉取邮箱邮件，清洗后暂存到 Redis（待 ingest）。IMAP 属 IO 型，直接在本进程跑。"""
    log_section("📧 邮件拉取")
    try:
        n = Pipeline(account).run_fetch(folder=folder, limit=limit, since=since,
                                        on_log=lambda m: console.print(f"  {m}"))
        log_success(f"已入队 {n} 封邮件，运行 `ingest` 导入知识图谱")
    except Exception as e:
        logger.error("邮件拉取失败: %s", e, exc_info=True)
        log_error(f"邮件拉取失败: {e}")
        raise typer.Exit(code=1)


# ════════════════════════════════════════════════════════════════════
# ingest：Redis → LightRAG 知识图谱（原文 + 附件，增量建图）
# ════════════════════════════════════════════════════════════════════


@app.command("ingest")
def ingest_emails(
    limit: Optional[int] = typer.Option(None, help="限制本次导入数量"),
    account: Optional[str] = typer.Option(None, help="账号 id，缺省用默认账号"),
    local: bool = typer.Option(False, "--local", help="离线直跑：在本进程执行，不入队给 worker"),
):
    """把暂存的邮件（原文 + 附件）导入 LightRAG，增量跨文档建图"""
    log_section("📚 知识图谱导入 (LightRAG)")
    try:
        if local:
            stats = Pipeline(account).run_ingest(limit=limit,
                                                 on_log=lambda m: console.print(f"  {m}"))
            log_success(f"导入完成：{stats['uploaded']}/{stats['total']} 封"
                        f"（附件 {stats['attachments']}，失败 {stats['failed']}）")
        else:
            _enqueue("ingest", account, {"limit": limit})
    except Exception as e:
        logger.error("知识图谱导入失败: %s", e, exc_info=True)
        log_error(f"导入失败: {e}")
        raise typer.Exit(code=1)


# ════════════════════════════════════════════════════════════════════
# full-pipeline：fetch + ingest
# ════════════════════════════════════════════════════════════════════


@app.command("full-pipeline")
def full_pipeline(
    limit: int = typer.Option(100, help="最大处理邮件数"),
    folder: str = typer.Option("INBOX", help="邮件夹名称"),
    since: Optional[str] = typer.Option(None, help="只处理该日期之后 (YYYY-MM-DD)"),
    skip_fetch: bool = typer.Option(False, help="跳过拉取，直接 ingest 队列"),
    account: Optional[str] = typer.Option(None, help="账号 id，缺省用默认账号"),
    local: bool = typer.Option(False, "--local", help="离线直跑：在本进程执行，不入队给 worker"),
):
    """完整流程：拉取 → 清洗 → GraphRAG 建图"""
    log_section("🚀 完整流程")
    try:
        emit = lambda m: console.print(f"  {m}")
        if not local:
            # fetch(IMAP IO) 在本进程直接跑；ingest(解析/建图) 入队给 worker
            pipe = Pipeline(account)
            if not skip_fetch:
                pipe.run_fetch(folder=folder, limit=limit, since=since, on_log=emit)
            _enqueue("ingest", account, {"limit": limit})
            log_success("fetch 已本地完成，ingest 已提交给 worker（确保 worker 在运行）")
            return

        pipe = Pipeline(account)
        if not skip_fetch:
            pipe.run_fetch(folder=folder, limit=limit, since=since, on_log=emit)
        stats = pipe.run_ingest(on_log=emit)

        cache = MailCache(pipe.account_id)
        s = cache.get_statistics()
        cache.close()

        console.print()
        table = Table(title="处理统计")
        table.add_column("指标", style="cyan")
        table.add_column("数值", style="green")
        for key, value in s.items():
            table.add_row(key, str(value))
        console.print(table)
        log_success(f"完整流程完成：本次导入 {stats['uploaded']} 封")
    except Exception as e:
        logger.error("完整流程失败: %s", e, exc_info=True)
        log_error(f"完整流程失败: {e}")
        raise typer.Exit(code=1)


# ════════════════════════════════════════════════════════════════════
# web：启动 FastAPI 后端 + 生产模式下托管 Vue SPA
# ════════════════════════════════════════════════════════════════════


@app.command("web")
def start_web(
    host: str = typer.Option("localhost", help="服务器地址"),
    port: int = typer.Option(8000, help="服务器端口"),
    reload: bool = typer.Option(True, help="开发模式自动重载"),
):
    """启动 FastAPI Web 服务"""
    import uvicorn

    log_section("🌐 启动 Web 服务 (FastAPI + Vue SPA)")
    console.print(f"API 地址: http://{host}:{port}/api/health")
    console.print(f"API 文档: http://{host}:{port}/docs")
    try:
        uvicorn.run(
            "src.backend.app:app",
            host=host,
            port=port,
            reload=reload,
            log_level="info",
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]服务器已停止[/yellow]")
    except Exception as e:
        logger.error("启动 Web 服务失败: %s", e, exc_info=True)
        log_error(f"启动 Web 服务失败: {e}")
        raise typer.Exit(code=1)


# ════════════════════════════════════════════════════════════════════
# check：系统检查
# ════════════════════════════════════════════════════════════════════


@app.command("check")
def system_check():
    """检查系统配置和依赖连接"""
    log_section("🔍 系统检查")

    console.print("\n[bold]配置检查:[/bold]")
    for name, ok in [
        ("IMAP 邮箱用户", bool(settings.email_user)),
        ("IMAP 邮箱密码", bool(settings.email_pass)),
        ("Neo4j URI", bool(settings.resolved_neo4j_uri())),
        ("Milvus URI", bool(settings.milvus_uri)),
        ("OpenAI API Key (query 用)", bool(settings.openai_api_key)),
    ]:
        status = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {status} {name}")

    console.print("\n[bold]连接检查:[/bold]")
    try:
        from src.backend.mail.imap_client import IMAPClient
        c = IMAPClient()
        c.connect()
        c.close()
        console.print("  [green]✓[/green] IMAP 连接")
    except Exception as e:
        console.print(f"  [red]✗[/red] IMAP 连接 ({e})")

    try:
        c = MailCache()
        c.get_stats()
        c.close()
        console.print("  [green]✓[/green] Redis 连接")
    except Exception as e:
        console.print(f"  [red]✗[/red] Redis 连接 ({e})")

    try:
        from src.backend.storage.neo4j_client import _get_driver
        _get_driver().verify_connectivity()
        console.print("  [green]✓[/green] Neo4j 连接")
    except Exception as e:
        console.print(f"  [red]✗[/red] Neo4j 连接 ({e})")

    console.print(f"\n[bold]数据目录:[/bold] {settings.data_dir}")
    log_success("系统检查完成！")


if __name__ == "__main__":
    app()
