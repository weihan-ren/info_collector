import os
import re
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError


class SourceType(str, Enum):
    RSS = "rss"
    HTML = "html"
    JSON = "json"


class JsonFieldMapping(BaseModel):
    items_path: str = "data.items"
    title_field: str = "title"
    url_field: str = "uri"
    summary_field: str = "content"
    url_prefix: str = ""


class NewsSourceConfig(BaseModel):
    name: str
    url: str
    type: SourceType = SourceType.RSS
    json_mapping: JsonFieldMapping | None = None


class LLMConfig(BaseModel):
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    temperature: float = Field(default=0.7, ge=0, le=2)


class EmailConfig(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    sender: str
    password: str
    recipients: list[str]
    use_tls: bool = True


class AppConfig(BaseModel):
    news_sources: list[NewsSourceConfig]
    llm: LLMConfig
    email: EmailConfig


def _resolve_env_vars(value: str) -> str:
    """Replace ${VAR_NAME} placeholders with environment variable values."""
    pattern = re.compile(r"\$\{(\w+)\}")
    matches = pattern.findall(value)
    for var_name in matches:
        env_val = os.environ.get(var_name, "")
        value = value.replace(f"${{{var_name}}}", env_val)
    return value


def _resolve_dict_env_vars(data: dict) -> dict:
    """Recursively resolve environment variables in all string values of a dict."""
    resolved = {}
    for key, value in data.items():
        if isinstance(value, str):
            resolved[key] = _resolve_env_vars(value)
        elif isinstance(value, dict):
            resolved[key] = _resolve_dict_env_vars(value)
        elif isinstance(value, list):
            resolved[key] = [
                _resolve_env_vars(item) if isinstance(item, str) else item
                for item in value
            ]
        else:
            resolved[key] = value
    return resolved


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """Load and validate configuration from a YAML file."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    resolved = _resolve_dict_env_vars(raw)
    return AppConfig(**resolved)
