"""新闻信息收集与简报生成系统 — CLI + Web 双模式入口。"""

import argparse
import io
import sys
from datetime import datetime
from pathlib import Path

from src.config_loader import load_config
from src.collector import NewsItem, create_collector
from src.analyzer import NewsAnalyzer

# Ensure UTF-8 console output (dev mode only; PyInstaller --console handles this natively).
if not getattr(sys, "frozen", False):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except (ValueError, AttributeError, OSError):
        pass


def _is_frozen() -> bool:
    """Check if running as a PyInstaller-frozen executable."""
    return getattr(sys, "frozen", False)


def _open_browser(port: int) -> None:
    """Open the default browser to the application URL (non-blocking)."""
    import threading
    import webbrowser

    def _open():
        import time
        time.sleep(1.0)
        webbrowser.open(f"http://localhost:{port}")

    t = threading.Thread(target=_open, daemon=True)
    t.start()


def collect_all_news(config_path: str, max_age_days: int) -> list[NewsItem]:
    config = load_config(config_path)
    all_items: list[NewsItem] = []
    for source_cfg in config.news_sources:
        try:
            collector = create_collector(source_cfg, max_age_days=max_age_days)
            items = collector.collect()
            print(f"[{source_cfg.name}] 采集到 {len(items)} 条新闻")
            all_items.extend(items)
        except Exception as e:
            print(f"[{source_cfg.name}] 采集失败: {e}")
    print(f"共采集 {len(all_items)} 条新闻")
    return all_items


def main():
    parser = argparse.ArgumentParser(description="新闻信息收集与简报生成系统")
    parser.add_argument(
        "-c", "--config", default="config.yaml", help="配置文件路径 (默认: config.yaml)"
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None, help="将简报保存到指定文件"
    )
    parser.add_argument(
        "--max-age", type=int, default=1, help="采集最近N天的新闻 (默认: 1)"
    )
    parser.add_argument(
        "--output-format", choices=["md", "html", "text"], default="md",
        help="输出文件格式 (默认: md)"
    )
    parser.add_argument(
        "--serve", action="store_true", help="启动 Web 服务"
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Web 服务端口 (默认: 8080)"
    )
    args = parser.parse_args()

    # ── Web 模式 ──
    if args.serve or _is_frozen():
        import uvicorn
        from src.server import app

        print("=" * 50)
        print("  新闻信息收集系统")
        print("=" * 50)
        print(f"  服务地址: http://localhost:{args.port}")
        print(f"  浏览器将自动打开，如未打开请手动访问上述地址。")
        print()
        print("  关闭浏览器后，请手动关闭此窗口退出程序。")
        print("=" * 50)

        _open_browser(args.port)
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
        return

    # ── CLI 模式 ──
    print(f"加载配置: {args.config}")
    config = load_config(args.config)

    print("开始采集新闻...")
    all_items = collect_all_news(args.config, max_age_days=args.max_age)

    if not all_items:
        print("未采集到任何新闻，退出。")
        return

    print("调用大模型分析...")
    analyzer = NewsAnalyzer(config.llm)
    try:
        briefing = analyzer.analyze(all_items)
    except Exception as e:
        print(f"大模型分析失败: {e}")
        sys.exit(1)

    print("\n" + "=" * 50)
    print(briefing.to_markdown())
    print("=" * 50)

    if args.output:
        format_handlers = {
            "md": briefing.to_markdown,
            "html": briefing.to_html,
            "text": briefing.to_plain_text,
        }
        content = format_handlers[args.output_format]()
        Path(args.output).write_text(content, encoding="utf-8")
        print(f"简报已保存至: {args.output}")

    _archive_briefing(briefing)


def _archive_briefing(briefing):
    archive_dir = Path("archive")
    archive_dir.mkdir(exist_ok=True)
    timestamp = briefing.generated_at.strftime("%Y%m%d_%H%M%S")
    html_path = archive_dir / f"{timestamp}.html"
    md_path = archive_dir / f"{timestamp}.md"
    html_path.write_text(briefing.to_html(), encoding="utf-8")
    md_path.write_text(briefing.to_markdown(), encoding="utf-8")
    print(f"简报已归档至: {html_path}")


if __name__ == "__main__":
    main()
