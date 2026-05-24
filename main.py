import argparse
import io
import sys
from datetime import datetime
from pathlib import Path

from src.config_loader import load_config
from src.collector import create_collector, NewsItem
from src.analyzer import NewsAnalyzer
from src.email_sender import EmailSender

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


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
        "--dry-run", action="store_true", help="仅采集和分析，不发送邮件"
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
    args = parser.parse_args()

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

    if args.dry_run:
        print("\n[Dry-Run 模式] 跳过邮件发送。")
        return

    print("发送邮件...")
    sender = EmailSender(config.email)
    success = sender.send(briefing)
    _archive_briefing(briefing)
    if success:
        print(f"邮件已发送至: {config.email.recipients}")
    else:
        print("邮件发送失败！")
        sys.exit(1)


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
