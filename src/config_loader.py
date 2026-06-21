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


class HtmlFieldMapping(BaseModel):
    item_selector: str = ""        # CSS 选择器，定位每个新闻条目
    title_selector: str = ""       # 在条目内定位标题的 CSS 选择器；为空则用条目自身文本
    link_selector: str = ""        # 在条目内定位链接的 CSS 选择器；为空则找第一个 <a>
    summary_selector: str = ""     # 在条目内定位摘要的 CSS 选择器（可选）
    link_attr: str = "href"        # 从哪个属性提取链接
    url_prefix: str = ""           # 相对链接前缀


class NewsSourceConfig(BaseModel):
    name: str
    url: str
    type: SourceType = SourceType.RSS
    json_mapping: JsonFieldMapping | None = None
    html_mapping: HtmlFieldMapping | None = None


DEFAULT_ANALYSIS_PROMPT = (
    "你是一个专业的AI产业新闻分析师，请根据以下新闻列表，提取和整理AI全产业链相关的新闻。\n\n"
    "## 关注范围（按产业链顺序）\n"
    "1. **上游矿产与原材料**：锂、钴、稀土、硅材料、高纯化学品等\n"
    "2. **半导体与芯片**：AI芯片（GPU/TPU/NPU）、CPU、存储芯片（HBM/DRAM/NAND）、光模块/光芯片、先进封装、EDA/IP、制造设备\n"
    "3. **硬件与基础设施**：服务器、数据中心、液冷散热、光通信、网络设备、电力配套\n"
    "4. **AI平台与模型**：大模型训练/推理、开源模型、多模态、Agent框架、MLOps\n"
    "5. **AI应用与产业化**：各行业AI落地（金融、医疗、制造、自动驾驶等）、大厂AI战略布局、AI Agent产品、企业级AI工具\n"
    "6. **政策与监管**：各国AI政策、出口管制、补贴扶持、数据安全法规、伦理规范\n"
    "7. **投融资与市场**：AI相关融资、IPO、并购、市值变动、行业趋势分析\n\n"
    "## 要求\n"
    "1. 优先选取AI产业链相关的新闻，无关新闻（如纯体育、娱乐、非科技类社会新闻）可直接忽略\n"
    "2. 按上述产业链分类组织，如果某个分类没有相关新闻则跳过\n"
    "3. 每条新闻用一句话概括核心要点（不超过80字），必须包含原文标题和链接，格式为：[原文标题](链接) - 一句话摘要\n"
    "4. 选出5-8条最重要的AI产业新闻作为重点关注\n"
    "5. 如果新闻较少或没有AI相关新闻，如实说明即可\n"
    "6. 输出严格的JSON格式，不要包含其他文字\n\n"
    "## 输出JSON格式\n"
    '{{\n'
    '    "title": "AI产业新闻简报",\n'
    '    "highlights": ["重点新闻1", "重点新闻2"],\n'
    '    "categories": [\n'
    '        {{\n'
    '            "category": "半导体与芯片",\n'
    '            "items": ["[新闻标题](链接) - 一句话摘要"]\n'
    "        }}\n"
    "    ]\n"
    "}}\n\n"
    "以下是新闻列表：\n\n"
    "{news_text}"
)


class LLMConfig(BaseModel):
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    temperature: float = Field(default=0.7, ge=0, le=2)
    analysis_prompt: str = DEFAULT_ANALYSIS_PROMPT


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
    email: EmailConfig | None = None


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
