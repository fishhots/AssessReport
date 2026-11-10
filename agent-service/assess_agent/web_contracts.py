"""联网搜索的公共契约与纯函数。

这一层不依赖 schemas / evidence，避免与 JobRequest、WebArchive 形成循环导入。
"""
from hashlib import sha256
import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

QUERY_LIMIT = 128
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 定向检索模板：一个 topic 对应一条可复现、可审计的搜索指令。
TOPIC_TEMPLATES = {
    "leadership_speech": {"label": "领导讲话", "category": "SPEECH",
                          "terms": "党委书记 校长 讲话 人才培养 高质量发展"},
    "strategic_plan": {"label": "战略规划", "category": "REFERENCE",
                       "terms": "事业发展规划 十四五 规划 战略目标"},
    "school_profile": {"label": "办学定位", "category": "REFERENCE",
                       "terms": "学校简介 办学定位 办学特色 发展目标"},
    "talent_training": {"label": "人才培养", "category": "REFERENCE",
                        "terms": "人才培养 培养目标 专业建设 教学改革"},
    "quality_report": {"label": "质量报告", "category": "REFERENCE",
                       "terms": "本科教学质量报告 毕业生就业质量报告"},
    "discipline_reform": {"label": "学科与改革", "category": "REFERENCE",
                          "terms": "学科建设 教育教学改革 一流专业 课程建设"},
}
DEFAULT_TOPICS = ["leadership_speech", "strategic_plan"]
SITE_PATTERN = re.compile(r"^[a-z0-9\u4e00-\u9fff\-]+(?:\.[a-z0-9\u4e00-\u9fff\-]+)+$")


def clean_text(value, limit: int) -> str:
    """清洗网页正文：去控制字符、压缩空白、限制长度。"""
    if not isinstance(value, str):
        return ""
    text = CONTROL_CHARS.sub(" ", value)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def normalize_site(value: str) -> str:
    """接受域名或完整 URL，只保留主机名，用于 site: 语法与结果域名校验。"""
    text = (value or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        text = (urlsplit(text).hostname or "").lower()
    else:
        text = text.split("/", 1)[0].split("?", 1)[0]
    text = text.lstrip("*.").strip()
    if not text or len(text) > 200 or not SITE_PATTERN.fullmatch(text):
        raise ValueError("请填写有效的学校官网域名，例如 bistu.edu.cn")
    return text


def safe_url(url) -> str:
    """只允许公网 HTTP(S) 网页，避免把内网地址或非网页 URL 交给 Fetch。"""
    if not isinstance(url, str) or not url or len(url) > 8192:
        return ""
    if any(ord(char) < 32 or char.isspace() for char in url):
        return ""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            return ""
        normalized = host.lower().strip(".")
        if normalized in ("localhost", "localhost.localdomain") or normalized.endswith(".localhost"):
            return ""
        try:
            address = ipaddress.ip_address(normalized)
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                return ""
        except ValueError:
            pass
    except ValueError:
        return ""
    return url


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def host_allowed(url: str, site: str) -> bool:
    """限定官网域名时，搜索引擎返回的站外结果一律丢弃。"""
    if not site:
        return True
    host = host_of(url)
    return host == site or host.endswith("." + site)


def compose_query(school: str, terms: str, keywords: str, site: str, after_year: int | None) -> str:
    """构造不超过 128 字符的搜索式，优先保留学校名与 site: 限定。"""
    operators = ""
    if site:
        operators += f" site:{site}"
    operators += " lang:zh"
    if after_year:
        operators += f" after:{after_year}"
    budget = QUERY_LIMIT - len(operators)
    school = clean_text(school, 60)
    body = " ".join(part for part in (school, clean_text(terms, 120), clean_text(keywords, 120)) if part).strip()
    if len(body) <= budget:
        return (body + operators).strip() or school
    head = school[:max(1, min(len(school), budget // 2))]
    remaining = budget - len(head) - 1
    picked = []
    for term in (clean_text(terms, 120) + " " + clean_text(keywords, 120)).split():
        candidate = " ".join(picked + [term])
        if len(candidate) > remaining:
            break
        picked.append(term)
    return (head + " " + " ".join(picked)).strip()[:budget] + operators


class WebSearchDirective(BaseModel):
    """给智能体的明确搜索指令：检索哪所学校、哪个官网、哪些主题、如何归档。"""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    enabled: bool = False
    school: str = Field(default="", max_length=100)
    site: str = Field(default="", max_length=200)
    topics: list[str] = Field(default_factory=lambda: list(DEFAULT_TOPICS))
    keywords: str = Field(default="", max_length=200)
    category: Literal["DATA", "SPEECH", "REFERENCE"] | None = None
    max_results: int = Field(default=5, ge=1, le=10)
    max_pages: int = Field(default=8, ge=1, le=30)
    after_year: int | None = Field(default=None, ge=2000, le=2100)
    refresh: bool = False
    fetch_content: bool = True

    @field_validator("school", "keywords")
    @classmethod
    def clean_plain(cls, value):
        text = clean_text(value, 200)
        if re.search(r"(?:site:|lang:|after:|before:|-site:)", text, re.IGNORECASE):
            raise ValueError("学校名与关键词不能包含搜索操作符")
        return text

    @field_validator("site")
    @classmethod
    def clean_site(cls, value):
        return normalize_site(value)

    @field_validator("topics")
    @classmethod
    def clean_topics(cls, value):
        if not isinstance(value, list) or not value:
            raise ValueError("请至少选择一个检索主题")
        unknown = [item for item in value if item not in TOPIC_TEMPLATES]
        if unknown:
            raise ValueError("不支持的检索主题：" + "、".join(unknown))
        return list(dict.fromkeys(value))[:6]

    def with_school(self, school: str) -> "WebSearchDirective":
        if self.school.strip():
            return self
        return self.model_copy(update={"school": clean_text(school, 100)})

    def category_for(self, topic: str) -> str:
        return self.category or TOPIC_TEMPLATES[topic]["category"]

    def query_key(self, topic: str) -> str:
        seed = "|".join((self.school.strip().lower(), self.site, topic,
                         self.keywords.strip(), str(self.after_year or ""), str(self.max_results)))
        return sha256(seed.encode()).hexdigest()[:20]

    def queries(self) -> list[dict]:
        output = []
        for topic in self.topics:
            template = TOPIC_TEMPLATES[topic]
            query = compose_query(self.school, template["terms"], self.keywords, self.site, self.after_year)
            output.append({"topic": topic, "label": template["label"],
                           "category": self.category_for(topic), "query": query,
                           "queryKey": self.query_key(topic)})
        return output
