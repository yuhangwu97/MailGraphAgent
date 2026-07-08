"""
MailGraphAgent 主程序 - CLI 入口
支持邮件拉取、实体提取、图谱导入、完整流程、前端启动
"""
import logging
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.panel import Panel

from config.settings import get_settings
from src.mail.imap_client import IMAPClient
from src.mail.cleaner import MailCleaner
from src.mail.parser import ParsedEmail
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

# ── CLI 应用 ──
app = typer.Typer(help="MailGraphAgent - 邮件图谱分析系统")
console = Console()

# ── 全局配置 ──
settings = get_settings()


def log_section(title: str):
    """打印日志分隔符"""
    console.print(Panel(title, style="bold blue"))


def log_success(msg: str):
    """打印成功消息"""
    console.print(f"✓ {msg}", style="green")


def log_error(msg: str):
    """打印错误消息"""
    console.print(f"✗ {msg}", style="red")


def log_warning(msg: str):
    """打印警告消息"""
    console.print(f"! {msg}", style="yellow")


# ════════════════════════════════════════════════════════════════════
# 子命令：邮件拉取
# ════════════════════════════════════════════════════════════════════


@app.command("fetch")
def fetch_emails(
    limit: int = typer.Option(100, help="拉取邮件数量限制"),
    days: int = typer.Option(30, help="拉取最近 N 天的邮件"),
    folder: str = typer.Option("INBOX", help="邮件夹名称"),
    save_attachments: bool = typer.Option(True, help="是否保存附件"),
):
    """拉取邮箱邮件并保存进度到 Redis"""
    log_section("📧 邮件拉取")

    try:
        client = IMAPClient()
        cache = MailCache()
        cleaner = MailCleaner()

        # 连接邮箱
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("连接邮箱...", start=False)
            conn = client.connect()
            log_success(f"已连接 {settings.imap_server}")

            # 计算日期范围
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            progress.add_task(
                f"搜索 {start_date.date()} 到 {end_date.date()} 的邮件...",
                start=False,
            )

            # 搜索邮件
            email_ids = client.search_by_date(folder, start_date, end_date, limit)
            total = len(email_ids)
            log_success(f"找到 {total} 封邮件")

            # 批量处理邮件
            processed = 0
            task = progress.add_task(
                "[cyan]处理邮件...", total=total, start=True
            )

            for email_id in email_ids:
                try:
                    raw_email = client.fetch_email(folder, email_id)
                    if raw_email is None:
                        continue

                    # 解析邮件
                    parsed = ParsedEmail.from_raw(raw_email)
                    
                    # 清洗邮件内容
                    parsed.body = cleaner.clean(parsed.body)
                    parsed.subject = cleaner.clean(parsed.subject)

                    # 保存进度到 Redis
                    cache.mark_processing(
                        parsed.message_id, email_id, folder,
                        parsed.subject, parsed.from_addr,
                        parsed.date.isoformat(),
                    )
                    cache.mark_done(parsed.message_id)

                    # 保存附件
                    if save_attachments and parsed.attachments:
                        for attach in parsed.attachments:
                            attach_path = settings.resolve_data_path(
                                f"attachments/{parsed.hash_id}"
                            )
                            attach_path.mkdir(parents=True, exist_ok=True)
                            attach_path = attach_path / attach.filename
                            attach_path.write_bytes(attach.content)

                    processed += 1
                    progress.update(task, advance=1)

                except Exception as e:
                    logger.error(f"处理邮件 {email_id} 失败: {e}")
                    log_warning(f"邮件 {email_id} 处理失败（已跳过）")

        cache.close()
        client.close()

        # 摘要
        console.print()
        table = Table(title="拉取摘要")
        table.add_column("指标", style="cyan")
        table.add_column("数值", style="green")
        table.add_row("总邮件数", str(total))
        table.add_row("已处理", str(processed))
        table.add_row("失败", str(total - processed))
        table.add_row("时间范围", f"{start_date.date()} 到 {end_date.date()}")
        console.print(table)

        log_success("邮件拉取完成！")

    except Exception as e:
        logger.error(f"邮件拉取失败: {e}", exc_info=True)
        log_error(f"邮件拉取失败: {e}")
        raise typer.Exit(code=1)


# ════════════════════════════════════════════════════════════════════
# 子命令：实体提取
# ════════════════════════════════════════════════════════════════════


@app.command("extract")
def extract_entities(
    limit: Optional[int] = typer.Option(None, help="限制处理邮件数（默认全部）"),
    force: bool = typer.Option(False, help="是否强制重新提取"),
):
    """从邮件中提取实体（公司、联系人、项目等）"""
    log_section("🤖 实体提取")

    try:
        from src.ai.extractor import Extractor

        extractor = Extractor()

        # 从 fetched_mails.json 读取邮件
        fetched_file = settings.resolve_data_path("fetched_mails.json")
        if not fetched_file.exists():
            log_warning("没有已拉取的邮件，请先运行 fetch 命令拉取邮件")
            return

        with open(fetched_file, "r", encoding="utf-8") as f:
            mails = json.load(f)

        if limit:
            mails = mails[:limit]
        total = len(mails)

        log_success(f"准备提取 {total} 封邮件的实体")

        results = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]提取实体...", total=total)

            for mail in mails:
                try:
                    extraction = extractor.extract_from_email(
                        subject=mail.get("subject", ""),
                        body=mail.get("cleaned_body", ""),
                        from_addr=mail.get("from_addr", ""),
                        to_addrs=mail.get("to_addrs", []),
                        date=mail.get("date", ""),
                    )
                    results.append({**mail, "extraction": extraction})
                    progress.update(task, advance=1)

                except Exception as e:
                    logger.error(f"提取失败: {e}")
                    log_warning(f"提取失败（已跳过）")
                    results.append({**mail, "extraction": {"error": str(e)}})
                    progress.update(task, advance=1)

        # 保存提取结果
        output_file = settings.resolve_data_path("extracted_mails.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        log_success(f"实体提取完成！结果保存到 {output_file}")

    except Exception as e:
        logger.error(f"实体提取失败: {e}", exc_info=True)
        log_error(f"实体提取失败: {e}")
        raise typer.Exit(code=1)


# ════════════════════════════════════════════════════════════════════
# 子命令：知识库导入
# ════════════════════════════════════════════════════════════════════


@app.command("import-kb")
def import_knowledge_base(
    limit: Optional[int] = typer.Option(None, help="限制导入数量"),
):
    """将 AI 提取结果导入 RAGFlow 知识库"""
    log_section("📚 知识库导入")

    try:
        from src.attachment.ragflow_client import get_ragflow_client

        rf = get_ragflow_client()
        rf.get_or_create_dataset("MailGraph")
        rf.enable_graphrag()

        # 从 extracted_mails.json 读取
        extracted_file = settings.resolve_data_path("extracted_mails.json")
        if not extracted_file.exists():
            log_warning("没有已提取的邮件，请先运行 extract 命令")
            return

        with open(extracted_file, "r", encoding="utf-8") as f:
            mails = json.load(f)

        if limit:
            mails = mails[:limit]
        total = len(mails)

        log_success(f"准备导入 {total} 条记录到 RAGFlow 知识库")

        doc_ids = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]导入知识库...", total=total)

            for mail in mails:
                try:
                    extraction = mail.get("extraction", {})
                    if "error" in extraction:
                        progress.update(task, advance=1)
                        continue

                    doc_id = rf.upload_email_extraction(
                        metadata={
                            "message_id": mail.get("message_id", ""),
                            "subject": mail.get("subject", ""),
                            "from_addr": mail.get("from_addr", ""),
                            "date": mail.get("date", ""),
                        },
                        extraction=extraction,
                    )
                    if doc_id:
                        doc_ids.append(doc_id)

                    progress.update(task, advance=1)

                except Exception as e:
                    logger.error(f"导入失败: {e}")
                    log_warning(f"导入失败（已跳过）")
                    progress.update(task, advance=1)

        if doc_ids:
            log_success(f"等待 RAGFlow 解析 {len(doc_ids)} 个文档...")
            rf.wait_for_parsing(doc_ids)

        log_success(f"知识库导入完成！成功: {len(doc_ids)}/{total}")

    except Exception as e:
        logger.error(f"知识库导入失败: {e}", exc_info=True)
        log_error(f"知识库导入失败: {e}")
        raise typer.Exit(code=1)


# ════════════════════════════════════════════════════════════════════
# 子命令：完整流程
# ════════════════════════════════════════════════════════════════════


@app.command("full-pipeline")
def full_pipeline(
    start_date: Optional[str] = typer.Option(None, help="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = typer.Option(None, help="结束日期 (YYYY-MM-DD)"),
    limit: int = typer.Option(1000, help="最大处理邮件数"),
    skip_fetch: bool = typer.Option(False, help="跳过拉取步骤"),
    skip_extract: bool = typer.Option(False, help="跳过提取步骤"),
    skip_import: bool = typer.Option(False, help="跳过导入步骤"),
):
    """执行完整流程：拉取 → 清洗 → 提取 → 导入 → 统计"""
    log_section("🚀 完整流程")

    try:
        steps = []

        # 步骤 1: 拉取邮件
        if not skip_fetch:
            steps.append(("📧 拉取邮件", fetch_emails, {"limit": limit}))

        # 步骤 2: 提取实体
        if not skip_extract:
            steps.append(("🤖 提取实体", extract_entities, {"limit": limit}))

        # 步骤 3: 导入知识库
        if not skip_import:
            steps.append(("📚 知识库导入", import_knowledge_base, {"limit": limit}))

        # 执行步骤
        for step_name, step_func, step_kwargs in steps:
            console.print()
            log_section(step_name)
            try:
                step_func(**step_kwargs)
            except typer.Exit:
                log_error(f"{step_name} 失败，停止流程")
                raise
            except Exception as e:
                log_error(f"{step_name} 异常: {e}")
                raise

        # 统计摘要
        console.print()
        cache = MailCache()
        stats = cache.get_statistics()
        cache.close()

        log_section("📈 统计摘要")
        table = Table(title="处理统计")
        table.add_column("指标", style="cyan")
        table.add_column("数值", style="green")
        for key, value in stats.items():
            table.add_row(key, str(value))
        console.print(table)

        log_success("完整流程执行完成！")

    except Exception as e:
        logger.error(f"完整流程执行失败: {e}", exc_info=True)
        log_error(f"完整流程执行失败: {e}")
        raise typer.Exit(code=1)


# ════════════════════════════════════════════════════════════════════
# 子命令：启动 Web 前端
# ════════════════════════════════════════════════════════════════════


@app.command("web")
def start_web(
    host: str = typer.Option("localhost", help="服务器地址"),
    port: int = typer.Option(8501, help="服务器端口"),
    browser: bool = typer.Option(True, help="是否自动打开浏览器"),
):
    """启动 Streamlit Web 前端"""
    import subprocess

    log_section("🌐 启动 Web 前端")

    try:
        console.print(f"正在启动 Streamlit 应用... ({host}:{port})")
        console.print(f"访问地址: http://{host}:{port}")
        console.print("[dim]按 Ctrl+C 停止服务器[/dim]")

        # 启动 Streamlit
        cmd = [
            "streamlit",
            "run",
            str(Path(__file__).parent / "web" / "app.py"),
            "--server.address",
            host,
            "--server.port",
            str(port),
            "--logger.level=info",
        ]

        if not browser:
            cmd.extend(["--client.showErrorDetails=false"])

        subprocess.run(cmd, check=True)

    except KeyboardInterrupt:
        console.print("\n[yellow]服务器已停止[/yellow]")
    except Exception as e:
        logger.error(f"启动 Web 前端失败: {e}", exc_info=True)
        log_error(f"启动 Web 前端失败: {e}")
        raise typer.Exit(code=1)


# ════════════════════════════════════════════════════════════════════
# 子命令：系统检查
# ════════════════════════════════════════════════════════════════════


@app.command("check")
def system_check():
    """检查系统配置和依赖"""
    log_section("🔍 系统检查")

    checks = []

    # 检查配置
    console.print("\n[bold]配置检查:[/bold]")
    checks.append(("OpenAI API Key", bool(settings.openai_api_key)))
    checks.append(("IMAP 邮箱用户", bool(settings.email_user)))
    checks.append(("IMAP 邮箱密码", bool(settings.email_pass)))
    checks.append(("RAGFlow URL", bool(settings.ragflow_base_url)))

    for check_name, result in checks:
        status = "[green]✓[/green]" if result else "[red]✗[/red]"
        console.print(f"  {status} {check_name}")

    # 检查连接
    console.print("\n[bold]连接检查:[/bold]")

    try:
        client = IMAPClient()
        client.connect()
        client.close()
        console.print("  [green]✓[/green] IMAP 连接")
    except Exception as e:
        console.print(f"  [red]✗[/red] IMAP 连接 ({e})")

    try:
        from src.attachment.ragflow_client import get_ragflow_client
        rf = get_ragflow_client()
        ds = rf.get_or_create_dataset("MailGraph")
        if ds:
            console.print("  [green]✓[/green] RAGFlow 连接")
        else:
            console.print("  [yellow]⚠[/yellow] RAGFlow 连接异常")
    except Exception as e:
        console.print(f"  [red]✗[/red] RAGFlow 连接 ({e})")

    # 检查数据目录
    console.print("\n[bold]数据目录:[/bold]")
    console.print(f"  {settings.data_dir}")

    log_success("系统检查完成！")


# ════════════════════════════════════════════════════════════════════
# 主程序
# ════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    app()
