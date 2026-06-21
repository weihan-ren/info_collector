"""FastAPI server for the news info collector web application."""

import io
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.analyzer import Briefing, NewsAnalyzer
from src.collector import NewsItem, create_collector
from src.config_loader import (
    AppConfig,
    LLMConfig,
    NewsSourceConfig,
    SourceType,
    load_config,
)

# Ensure UTF-8 output in dev mode; in frozen (exe) mode, console handles it natively.
if not getattr(sys, "frozen", False):
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


class ConfigModel(BaseModel):
    news_sources: list[NewsSourceModel]
    llm: LLMModel


class CollectResponse(BaseModel):
    success: bool
    briefing: Optional[dict] = None
    error: Optional[str] = None
    news_count: int = 0


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


def _collect_and_analyze(config: AppConfig, max_age_days: int = 1) -> tuple[list[NewsItem], Briefing]:
    all_items: list[NewsItem] = []
    for source_cfg in config.news_sources:
        try:
            collector = create_collector(source_cfg, max_age_days=max_age_days)
            items = collector.collect()
            all_items.extend(items)
        except Exception as e:
            print(f"[{source_cfg.name}] 采集失败: {e}")

    if not all_items:
        briefing = Briefing(
            title="今日新闻简报",
            generated_at=datetime.now(),
            highlights=["今日无新闻数据"],
        )
        return all_items, briefing

    analyzer = NewsAnalyzer(config.llm)
    briefing = analyzer.analyze(all_items)
    return all_items, briefing


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
        all_items, briefing = _collect_and_analyze(config)
        return CollectResponse(
            success=True,
            briefing=_briefing_to_dict(briefing),
            news_count=len(all_items),
        )
    except Exception as e:
        traceback.print_exc()
        return CollectResponse(
            success=False,
            error=str(e),
        )


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
