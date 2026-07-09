"""
MailGraphAgent 主程序 - CLI 入口
================================
子命令：fetch(拉取→Redis) / ingest(Redis→RAGFlow GraphRAG) /
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
from src.pipeline import Pipeline
from src.storage.redis_cache import MailCache

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


@app.command("fetch")
def fetch_emails(
    limit: int = typer.Option(100, help="拉取邮件数量上限"),
    folder: str = typer.Option("INBOX", help="邮件夹名称"),
    since: Optional[str] = typer.Option(None, help="只拉取该日期之后 (YYYY-MM-DD)"),
    account: Optional[str] = typer.Option(None, help="账号 id，缺省用默认账号"),
):
    """拉取邮箱邮件，清洗后暂存到 Redis（待 ingest）"""
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
# ingest：Redis → RAGFlow GraphRAG（原文 + 附件，单遍建图）
# ════════════════════════════════════════════════════════════════════


@app.command("ingest")
def ingest_emails(
    limit: Optional[int] = typer.Option(None, help="限制本次导入数量"),
    account: Optional[str] = typer.Option(None, help="账号 id，缺省用默认账号"),
):
    """把暂存的邮件（原文 + 附件）导入 RAGFlow，GraphRAG 跨文档建图"""
    log_section("📚 知识图谱导入 (GraphRAG)")
    try:
        stats = Pipeline(account).run_ingest(limit=limit,
                                             on_log=lambda m: console.print(f"  {m}"))
        log_success(f"导入完成：{stats['uploaded']}/{stats['total']} 封"
                    f"（附件 {stats['attachments']}，失败 {stats['failed']}）")
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
):
    """完整流程：拉取 → 清洗 → GraphRAG 建图"""
    log_section("🚀 完整流程")
    try:
        pipe = Pipeline(account)
        emit = lambda m: console.print(f"  {m}")
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
# web：启动 Streamlit 前端
# ════════════════════════════════════════════════════════════════════


@app.command("web")
def start_web(
    host: str = typer.Option("localhost", help="服务器地址"),
    port: int = typer.Option(8501, help="服务器端口"),
):
    """启动 Streamlit Web 前端"""
    import subprocess
    from pathlib import Path

    log_section("🌐 启动 Web 前端")
    console.print(f"访问地址: http://{host}:{port}")
    try:
        subprocess.run([
            "streamlit", "run",
            str(Path(__file__).parent / "web" / "app.py"),
            "--server.address", host,
            "--server.port", str(port),
            "--logger.level=info",
        ], check=True)
    except KeyboardInterrupt:
        console.print("\n[yellow]服务器已停止[/yellow]")
    except Exception as e:
        logger.error("启动 Web 前端失败: %s", e, exc_info=True)
        log_error(f"启动 Web 前端失败: {e}")
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
        ("RAGFlow URL", bool(settings.ragflow_base_url)),
        ("OpenAI API Key (query 用)", bool(settings.openai_api_key)),
    ]:
        status = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {status} {name}")

    console.print("\n[bold]连接检查:[/bold]")
    try:
        from src.mail.imap_client import IMAPClient
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
        from src.attachment.ragflow_client import get_ragflow_client
        rf = get_ragflow_client()
        ds = rf.get_or_create_dataset(settings.ragflow_dataset_name)
        console.print("  [green]✓[/green] RAGFlow 连接" if ds
                      else "  [yellow]⚠[/yellow] RAGFlow 连接异常")
    except Exception as e:
        console.print(f"  [red]✗[/red] RAGFlow 连接 ({e})")

    console.print(f"\n[bold]数据目录:[/bold] {settings.data_dir}")
    log_success("系统检查完成！")


if __name__ == "__main__":
    app()
