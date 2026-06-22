"""FastAPI server for the news info collector web application."""

import io
import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
import yaml
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from src.analyzer import Briefing, NewsAnalyzer
from src.collector import NewsItem, create_collector, get_user_agent
from src.config_loader import (
    AppConfig,
    DEFAULT_ANALYSIS_PROMPT,
    LLMConfig,
    NewsSourceConfig,
    SourceType,
    load_config,
)

# Ensure UTF-8 console output on Windows; macOS / Linux terminals are UTF-8 by default.
import platform as _platform
if _platform.system() == "Windows" and not getattr(sys, "frozen", False):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except (ValueError, AttributeError, OSError):
        pass


def _resolve_static_dir() -> Path:
    """Resolve the static directory, supporting PyInstaller bundled mode."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "static"


CONFIG_PATH = Path("config.yaml")
STATIC_DIR = _resolve_static_dir()

app = FastAPI(title="新闻信息收集系统")


def _ensure_default_config():
    """Create a default config.yaml if none exists."""
    if CONFIG_PATH.exists():
        return
    default = {
        "news_sources": [
            {"name": "36氪", "url": "https://36kr.com/feed", "type": "rss"},
            {"name": "少数派", "url": "https://sspai.com/feed", "type": "rss"},
        ],
        "llm": {
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o",
            "temperature": 0.7,
            "analysis_prompt": DEFAULT_ANALYSIS_PROMPT,
        },
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(default, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


_ensure_default_config()


# ── API Models ──────────────────────────────────────────────

class JsonMappingModel(BaseModel):
    items_path: str = "data.items"
    title_field: str = "title"
    url_field: str = "uri"
    summary_field: str = "content"
    url_prefix: str = ""


class NewsSourceModel(BaseModel):
    name: str
    url: str
    type: SourceType = SourceType.RSS
    json_mapping: Optional[JsonMappingModel] = None


class LLMModel(BaseModel):
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    temperature: float = Field(default=0.7, ge=0, le=2)
    analysis_prompt: str = ""


class ConfigModel(BaseModel):
    news_sources: list[NewsSourceModel]
    llm: LLMModel


class SourceCollectResult(BaseModel):
    name: str
    success: bool
    count: int = 0
    error: Optional[str] = None


class CollectResponse(BaseModel):
    success: bool
    briefing: Optional[dict] = None
    error: Optional[str] = None
    news_count: int = 0
    sources: list[SourceCollectResult] = []


# ── Helpers ──────────────────────────────────────────────────

def _config_to_model(config: AppConfig) -> ConfigModel:
    return ConfigModel(
        news_sources=[
            NewsSourceModel(
                name=s.name,
                url=s.url,
                type=s.type,
                json_mapping=(
                    JsonMappingModel(**s.json_mapping.model_dump())
                    if s.json_mapping
                    else None
                ),
            )
            for s in config.news_sources
        ],
        llm=LLMModel(
            api_key=config.llm.api_key,
            base_url=config.llm.base_url,
            model=config.llm.model,
            temperature=config.llm.temperature,
            analysis_prompt=config.llm.analysis_prompt,
        ),
    )


def _briefing_to_dict(briefing: Briefing) -> dict:
    return {
        "title": briefing.title,
        "generated_at": briefing.generated_at.isoformat(),
        "highlights": briefing.highlights,
        "categories": [
            {"category": c.category, "items": c.items} for c in briefing.categories
        ],
    }


def _collect_and_analyze(
    config: AppConfig, max_age_days: int = 1
) -> tuple[list[NewsItem], Briefing, list[SourceCollectResult]]:
    all_items: list[NewsItem] = []
    sources_result: list[SourceCollectResult] = []
    for source_cfg in config.news_sources:
        try:
            collector = create_collector(source_cfg, max_age_days=max_age_days)
            items = collector.collect()
            all_items.extend(items)
            sources_result.append(SourceCollectResult(
                name=source_cfg.name, success=True, count=len(items)
            ))
            print(f"[{source_cfg.name}] 采集到 {len(items)} 条新闻")
        except Exception as e:
            print(f"[{source_cfg.name}] 采集失败: {e}")
            sources_result.append(SourceCollectResult(
                name=source_cfg.name, success=False, count=0, error=str(e)
            ))

    if not all_items:
        briefing = Briefing(
            title="今日新闻简报",
            generated_at=datetime.now(),
            highlights=["今日无新闻数据"],
        )
        return all_items, briefing, sources_result

    analyzer = NewsAnalyzer(config.llm)
    briefing = analyzer.analyze(all_items)
    return all_items, briefing, sources_result


# ── Routes ───────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    """获取当前配置。"""
    try:
        config = load_config(str(CONFIG_PATH))
        return _config_to_model(config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置失败: {e}")


@app.put("/api/config")
async def save_config(data: ConfigModel):
    """保存配置到 config.yaml。"""
    try:
        yaml_data: dict = {
            "news_sources": [],
            "llm": {
                "api_key": data.llm.api_key,
                "base_url": data.llm.base_url,
                "model": data.llm.model,
                "temperature": data.llm.temperature,
                "analysis_prompt": data.llm.analysis_prompt,
            },
        }
        for src in data.news_sources:
            src_dict: dict = {"name": src.name, "url": src.url, "type": src.type.value}
            if src.json_mapping:
                src_dict["json_mapping"] = src.json_mapping.model_dump(
                    exclude_defaults=True
                )
            yaml_data["news_sources"].append(src_dict)

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(yaml_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        return {"success": True, "message": "配置已保存"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存配置失败: {e}")


@app.post("/api/collect", response_model=CollectResponse)
async def collect_news():
    """采集新闻并调用大模型分析，返回简报。"""
    try:
        config = load_config(str(CONFIG_PATH))
        all_items, briefing, sources_result = _collect_and_analyze(config)
        return CollectResponse(
            success=True,
            briefing=_briefing_to_dict(briefing),
            news_count=len(all_items),
            sources=[s.model_dump() for s in sources_result],
        )
    except Exception as e:
        traceback.print_exc()
        return CollectResponse(
            success=False,
            error=str(e),
        )


# ── Chat (streaming) ─────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    enable_search: bool = False


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """流式对话接口 —— 接收对话历史，通过 SSE 流式返回大模型回复。
    自动检测用户消息中的 URL，抓取网页内容后注入到消息中供模型分析。
    """
    config = load_config(str(CONFIG_PATH))
    client = AsyncOpenAI(api_key=config.llm.api_key, base_url=config.llm.base_url)

    async def generate():
        try:
            # ── 1. 构建消息列表，处理链接抓取和联网搜索 ──
            messages = []
            for m in request.messages:
                content = m.content
                if m.role == "user":
                    urls = _extract_urls(content)

                    if urls:
                        # 用户提供了链接 → 抓取链接内容
                        yield f"data: {json.dumps({'status': f'正在读取 {len(urls)} 个链接内容...'}, ensure_ascii=False)}\n\n"
                        fetched_parts = []
                        for url in urls:
                            fetched = _fetch_url_content(url)
                            if fetched:
                                fetched_parts.append(fetched)
                        if fetched_parts:
                            content = (
                                content
                                + "\n\n--- 以下是从链接中读取的网页内容 ---\n"
                                + "\n\n".join(fetched_parts)
                                + "\n--- 网页内容结束 ---"
                            )
                        yield f"data: {json.dumps({'status': '链接内容读取完成，正在分析...'}, ensure_ascii=False)}\n\n"

                    elif request.enable_search:
                        # 开启了联网搜索但无具体链接 → 自动搜索
                        yield f"data: {json.dumps({'status': '🔍 正在联网搜索: ' + content[:60] + '...'}, ensure_ascii=False)}\n\n"
                        search_results = _web_search(content)
                        if search_results:
                            content = (
                                content
                                + "\n\n--- 以下是联网搜索结果 ---\n"
                                + search_results
                                + "\n--- 搜索结果结束 ---\n"
                                + "请基于以上搜索结果回答用户问题。如果搜索结果不足以回答问题，请如实说明。"
                            )
                            yield f"data: {json.dumps({'status': f'联网搜索完成，正在分析...'}, ensure_ascii=False)}\n\n"
                        else:
                            yield f"data: {json.dumps({'status': '联网搜索未获取到有效结果，直接分析...'}, ensure_ascii=False)}\n\n"

                messages.append({"role": m.role, "content": content})

            # ── 2. 调用大模型流式生成 ──
            stream = await client.chat.completions.create(
                model=config.llm.model,
                messages=messages,
                temperature=config.llm.temperature,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue
                data: dict = {}
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    data["reasoning"] = reasoning
                if delta.content:
                    data["content"] = delta.content
                if data:
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            traceback.print_exc()
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Web Search ────────────────────────────────────────────────

_SEARCH_HEADERS = {
    "User-Agent": get_user_agent(),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _search_duckduckgo(query: str, max_results: int) -> str | None:
    """Try DuckDuckGo HTML search (best: clean HTML, no JS)."""
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=_SEARCH_HEADERS,
            timeout=8,
            verify=True,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".result")
        if not items:
            return None
        return _format_search_results(items, max_results, "DuckDuckGo")
    except requests.exceptions.SSLError:
        # SSL blocked — try without verification
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers=_SEARCH_HEADERS,
                timeout=8,
                verify=False,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.select(".result")
            if items:
                return _format_search_results(items, max_results, "DuckDuckGo")
        except Exception:
            pass
        return None
    except Exception:
        return None


def _search_bing(query: str, max_results: int) -> str | None:
    """Fallback: Bing search (works better in some regions)."""
    try:
        resp = requests.get(
            "https://www.bing.com/search",
            params={"q": query, "setlang": "zh-Hans"},
            headers=_SEARCH_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Bing's main result selectors
        items = soup.select("li.b_algo") or soup.select(".b_algo") or soup.select("ol#b_results > li")
        if not items:
            return None
        return _format_search_results_bing(items, max_results)
    except requests.exceptions.SSLError:
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = requests.get(
                "https://www.bing.com/search",
                params={"q": query, "setlang": "zh-Hans"},
                headers=_SEARCH_HEADERS,
                timeout=10,
                verify=False,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.select("li.b_algo") or soup.select(".b_algo") or soup.select("ol#b_results > li")
            if items:
                return _format_search_results_bing(items, max_results)
        except Exception:
            pass
        return None
    except Exception:
        return None


def _format_search_results(items, max_results: int, engine: str) -> str:
    """Format DuckDuckGo search results."""
    lines = [f"[联网搜索结果 (来源: {engine})]"]
    count = 0
    for item in items:
        if count >= max_results:
            break
        title_el = item.select_one("a")
        snippet_el = item.select_one(".result__snippet")
        if title_el:
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if title and url:
                count += 1
                lines.append(f"{count}. {title}")
                lines.append(f"   链接: {url}")
                if snippet:
                    lines.append(f"   摘要: {snippet}")
    return "\n".join(lines) if count > 0 else ""


def _format_search_results_bing(items, max_results: int) -> str:
    """Format Bing search results."""
    lines = ["[联网搜索结果 (来源: Bing)]"]
    count = 0
    for item in items:
        if count >= max_results:
            break
        title_el = item.select_one("h2 a") or item.select_one("a")
        snippet_el = item.select_one(".b_caption p") or item.select_one("p")
        if title_el:
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if title:
                count += 1
                lines.append(f"{count}. {title}")
                if url:
                    lines.append(f"   链接: {url}")
                if snippet:
                    lines.append(f"   摘要: {snippet}")
    return "\n".join(lines) if count > 0 else ""


def _web_search(query: str, max_results: int = 5) -> str:
    """Search the web using multiple engines with fallback."""
    # Try DuckDuckGo first (clean HTML, fast)
    result = _search_duckduckgo(query, max_results)
    if result:
        return result

    # Fallback to Bing
    result = _search_bing(query, max_results)
    if result:
        return result

    return "[联网搜索] 当前网络环境无法连接搜索引擎，请尝试提供具体网页链接。"


# ── URL Fetch Helpers ─────────────────────────────────────────

_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _extract_urls(text: str) -> list[str]:
    """Extract unique HTTP(S) URLs from text."""
    seen = set()
    urls = []
    for m in _URL_RE.findall(text):
        url = m.rstrip(".,;:!?)】」）")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _fetch_url_content(url: str, timeout: int = 8, max_chars: int = 8000) -> str | None:
    """Fetch a URL and extract its text content."""
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": get_user_agent(),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove script/style tags
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # Clean up whitespace
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        result = "\n".join(lines)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n... [内容已截断]"
        return f"[来源: {url}]\n{result}" if result else None
    except Exception as e:
        return f"[来源: {url}]\n[无法读取: {e}]"


# ── Static Files / SPA ───────────────────────────────────────

@app.get("/")
async def serve_index():
    """Serve the frontend page."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse({"message": "Frontend not found"}, status_code=404)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
