import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from openai import OpenAI

from src.collector import NewsItem
from src.config_loader import LLMConfig

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


@dataclass
class CategoryBrief:
    category: str
    items: list[str] = field(default_factory=list)


@dataclass
class Briefing:
    title: str
    generated_at: datetime
    categories: list[CategoryBrief] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)

    def to_plain_text(self) -> str:
        return self.to_markdown()

    def to_markdown(self) -> str:
        lines = [f"# {self.title}", ""]
        if self.highlights:
            lines.append("## 重点关注")
            for h in self.highlights:
                lines.append(f"- {h}")
            lines.append("")
        for cat in self.categories:
            lines.append(f"## {cat.category}")
            for item in cat.items:
                lines.append(f"- {item}")
            lines.append("")
        lines.append(f"*生成时间: {self.generated_at.strftime('%Y-%m-%d %H:%M')}*")
        return "\n".join(lines)

    def to_html(self) -> str:
        return "\n".join(self._build_html_parts())

    def _build_html_parts(self) -> list[str]:
        parts = [
            "<!DOCTYPE html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
            "<style>",
            "  body { font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; "
            "max-width: 720px; margin: 0 auto; padding: 20px; color: #333; line-height: 1.7; }",
            "  h1 { color: #1a1a1a; border-bottom: 2px solid #0066cc; padding-bottom: 12px; }",
            "  h2 { color: #0066cc; margin-top: 28px; }",
            "  .highlights h2 { color: #c00; }",
            "  ul { padding-left: 20px; }",
            "  li { margin-bottom: 6px; }",
            "  a { color: #0066cc; text-decoration: none; }",
            "  a:hover { text-decoration: underline; }",
            "  .meta { color: #888; font-size: 14px; margin-top: 30px; "
            "border-top: 1px solid #e0e0e0; padding-top: 15px; }",
            "</style>",
            "</head>",
            "<body>",
            f"<h1>{self.title}</h1>",
        ]
        if self.highlights:
            parts.append('<div class="highlights">')
            parts.append("<h2>重点关注</h2><ul>")
            for h in self.highlights:
                parts.append(f"<li>{self._md_to_html(h)}</li>")
            parts.append("</ul></div>")
        for cat in self.categories:
            parts.append(f"<h2>{cat.category}</h2><ul>")
            for item in cat.items:
                parts.append(f"<li>{self._md_to_html(item)}</li>")
            parts.append("</ul>")
        parts.append(
            f'<p class="meta">生成时间: {self.generated_at.strftime("%Y-%m-%d %H:%M")}</p>'
        )
        parts.append("</body></html>")
        return parts

    @staticmethod
    def _md_to_html(text: str) -> str:
        return _MD_LINK_RE.sub(r'<a href="\2">\1</a>', text)


class NewsAnalyzer:
    def __init__(self, config: LLMConfig, max_retries: int = 2):
        self.config = config
        self.max_retries = max_retries
        self.client = OpenAI(api_key=config.api_key, base_url=config.base_url)

    def analyze(self, news_items: list[NewsItem]) -> Briefing:
        if not news_items:
            return Briefing(
                title="今日新闻简报",
                generated_at=datetime.now(),
                highlights=["今日无新闻数据"],
            )

        news_text = self._format_news(news_items)
        prompt = self._build_prompt(news_text)

        response_text = self._call_llm(prompt)
        return self._parse_response(response_text)

    def _format_news(self, items: list[NewsItem]) -> str:
        lines = []
        for i, item in enumerate(items, 1):
            lines.append(
                f"[{i}] 来源: {item.source}\n"
                f"    标题: {item.title}\n"
                f"    链接: {item.url}\n"
                f"    摘要: {item.summary}\n"
            )
        return "\n".join(lines)

    def _build_prompt(self, news_text: str) -> str:
        return f"""你是一个专业的新闻编辑，请根据以下新闻列表生成一份新闻简报。

要求：
1. 将新闻按主题分类（科技、财经、社会、国际等）
2. 每条新闻用一句话概括（不超过80字），包含标题和链接
3. 选出3-5条重点关注新闻
4. 输出严格的JSON格式，不要包含其他文字

输出JSON格式：
{{
    "title": "每日新闻简报",
    "highlights": ["重点新闻1", "重点新闻2"],
    "categories": [
        {{
            "category": "科技",
            "items": ["[新闻标题](链接) - 一句话摘要"]
        }}
    ]
}}

以下是新闻列表：

{news_text}"""

    def _call_llm(self, prompt: str) -> str:
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.config.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self.config.temperature,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    import time
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"LLM call failed after {self.max_retries + 1} attempts: {last_error}")

    def _parse_response(self, text: str) -> Briefing:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[: text.rfind("```")].strip()
            else:
                text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return Briefing(
                title="新闻简报（原始格式）",
                generated_at=datetime.now(),
                highlights=["LLM返回格式异常，以下是原始内容"],
                categories=[CategoryBrief(category="全部新闻", items=[text[:2000]])],
            )

        highlights = data.get("highlights", [])
        categories = [
            CategoryBrief(category=cat["category"], items=cat["items"])
            for cat in data.get("categories", [])
        ]

        return Briefing(
            title=data.get("title", "每日新闻简报"),
            generated_at=datetime.now(),
            highlights=highlights,
            categories=categories,
        )
