from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from src.config_loader import NewsSourceConfig, SourceType, JsonFieldMapping


@dataclass
class NewsItem:
    title: str
    url: str
    summary: str
    source: str
    published_at: Optional[datetime] = None


class BaseCollector(ABC):
    def __init__(self, config: NewsSourceConfig, max_age_days: int = 1):
        self.config = config
        self.max_age = timedelta(days=max_age_days)

    @abstractmethod
    def collect(self) -> list[NewsItem]:
        """Collect news items from the source."""

    def _is_recent(self, published_at: Optional[datetime]) -> bool:
        if published_at is None:
            return True
        now = datetime.now(timezone.utc)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        return now - published_at <= self.max_age

    @staticmethod
    def _clean_html(html_text: str) -> str:
        soup = BeautifulSoup(html_text, "html.parser")
        return soup.get_text(separator=" ", strip=True)


class RSSCollector(BaseCollector):
    def collect(self) -> list[NewsItem]:
        items = []
        feed = feedparser.parse(self.config.url)
        if feed.bozo and not feed.entries:
            raise RuntimeError(
                f"Failed to parse RSS feed '{self.config.name}': {feed.bozo_exception}"
            )
        for entry in feed.entries:
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                from calendar import timegm
                timestamp = timegm(entry.published_parsed)
                published = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            if not self._is_recent(published):
                continue
            summary = entry.get("summary", "") or entry.get("description", "")
            items.append(NewsItem(
                title=entry.get("title", ""),
                url=entry.get("link", ""),
                summary=self._clean_html(summary),
                source=self.config.name,
                published_at=published,
            ))
        return items


class HTMLCollector(BaseCollector):
    def collect(self) -> list[NewsItem]:
        resp = requests.get(self.config.url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (compatible; InfoCollector/1.0)"
        })
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items = []
        for link in soup.find_all("a", href=True):
            text = link.get_text(strip=True)
            if len(text) < 10:
                continue
            items.append(NewsItem(
                title=text,
                url=link["href"],
                summary=text,
                source=self.config.name,
            ))
        return items[:30]


class JSONCollector(BaseCollector):
    def collect(self) -> list[NewsItem]:
        resp = requests.get(self.config.url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (compatible; InfoCollector/1.0)"
        })
        resp.raise_for_status()
        data = resp.json()
        mapping = self.config.json_mapping or JsonFieldMapping()
        items_data = self._resolve_path(data, mapping.items_path)
        if not isinstance(items_data, list):
            raise RuntimeError(
                f"JSON path '{mapping.items_path}' did not resolve to a list in '{self.config.name}'"
            )
        items = []
        for entry in items_data:
            if not isinstance(entry, dict):
                continue
            title = str(self._get_field(entry, mapping.title_field) or "")
            url = str(self._get_field(entry, mapping.url_field) or "")
            summary = str(self._get_field(entry, mapping.summary_field) or "")
            if not title:
                continue
            if mapping.url_prefix and url and not url.startswith("http"):
                url = mapping.url_prefix + url
            items.append(NewsItem(
                title=title,
                url=url,
                summary=self._clean_html(summary),
                source=self.config.name,
            ))
        return items[:30]

    @staticmethod
    def _resolve_path(data, path: str):
        for key in path.split("."):
            if isinstance(data, dict):
                data = data.get(key)
            elif isinstance(data, list):
                try:
                    data = data[int(key)]
                except (IndexError, ValueError):
                    return []
            else:
                return []
        return data

    @staticmethod
    def _get_field(entry: dict, field: str):
        return entry.get(field) or entry.get(field.replace("_", "-"))


def create_collector(config: NewsSourceConfig, max_age_days: int = 1) -> BaseCollector:
    collector_map = {
        SourceType.RSS: RSSCollector,
        SourceType.HTML: HTMLCollector,
        SourceType.JSON: JSONCollector,
    }
    collector_cls = collector_map.get(config.type)
    if collector_cls is None:
        raise ValueError(f"Unsupported source type: {config.type}")
    return collector_cls(config, max_age_days=max_age_days)
