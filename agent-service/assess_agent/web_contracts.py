"""联网搜索的公共契约与纯函数。

这一层不依赖 schemas / evidence，避免与 JobRequest、WebArchive 形成循环导入。
"""
from hashlib import sha256
import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

QUERY_LIMIT = 128
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 检索式策略版本：改变搜索式生成规则时递增，使旧批次归档缓存自动失效。
# v2 = 移除 after:/before: 时间操作符，年份改为检索关键词 + 结果侧判定。
QUERY_STRATEGY = "v2"

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


YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
# 高校 CMS 常见 /2025/0512/、t20250512_123.html、/info/202505.htm 这类紧凑日期。
# 只认「恰好 8 位且月份/日期合法」或「4 位年 + 2 位月」，并要求前后不是数字，
# 避免把 12 位的文章 ID（如 202405150001）误判成 2024 年。
COMPACT_YEAR_PATTERN = re.compile(
    r"(?<!\d)(20\d{2})[-/.]?(0[1-9]|1[0-2])(?:(0[1-9]|[12]\d|3[01]))?(?!\d)")


def normalize_year(value) -> int | None:
    """把 2025 / "2025" / 25 / "25" 统一成四位年份；非法值返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            return None
        value = int(value)
    if not isinstance(value, int):
        return None
    if 0 <= value <= 99:
        value += 2000
    return value if 2000 <= value <= 2100 else None


def page_years(*, published: str = "", title: str = "", url: str = "") -> list[int]:
    """从发布时间、标题和 URL 中抽取可判定的年份。

    不用正文/摘要作依据：官网页面常同时提到规划起止年、往届数据，容易误判。
    正则带数字边界，避免把 20250315 这类长数字误当年份。
    """
    found: set[int] = set()
    for text in (published, title, url):
        if not isinstance(text, str):
            continue
        for match in YEAR_PATTERN.finditer(text):
            year = int(match.group())
            if 2000 <= year <= 2100:
                found.add(year)
        # 补上 20250512 / 2025-05-12 / 2025/05 这类紧凑写法：YEAR_PATTERN 因
        # 「年份后紧跟数字」会漏掉，但它们恰恰是高校官网最常见的日期目录格式。
        for match in COMPACT_YEAR_PATTERN.finditer(text):
            year = int(match.group(1))
            if 2000 <= year <= 2100:
                found.add(year)
    return sorted(found)


def year_allowed(years, year_from: int | None, year_to: int | None,
                 include_undated: bool = True) -> bool:
    """判定网页是否落在年份区间内；这是「只获取指定年份资料」的硬约束。

    未限定年份时一律放行；能判定年份但全部落在区间外时丢弃（真正保证不混入
    其他年度）；完全无法判定年份时默认**放行**——官网页面 date 常为 null、标题
    与 URL 也常不含年份，若默认丢弃会把目标年度的讲话/规划整批误杀。这类页面
    是带着年份关键词的搜索式召回的，检索结果会单独统计未标注年份的数量；
    需要绝对严格时传 include_undated=False 排除。
    """
    if not year_from and not year_to:
        return True
    if not years:
        return bool(include_undated)
    low = year_from or 2000
    high = year_to or 2100
    return any(low <= year <= high for year in years)


def year_terms(year_from: int | None, year_to: int | None) -> str:
    """把年份区间转成检索关键词，而不是 after:/before: 时间操作符。

    原因：DeepSeek 的 after:/before: 按网页发布时间过滤，而官方 results.date
    「未知时为 null」——学校官网的领导讲话、规划页面绝大多数没有机器可读的
    发布时间，带时间操作符会被搜索引擎整批排除；且 after:2025 语义是「2025 年
    之后」，单年区间 after:2025 before:2026 会把 2025 当年内容一起挤掉。
    """
    if not year_from and not year_to:
        return ""
    low = year_from or year_to
    high = year_to or year_from
    if low == high:
        return str(low)
    if high - low <= 3:
        return " ".join(str(year) for year in range(low, high + 1))
    return f"{low} {high}"


def compose_query(school: str, terms: str, keywords: str, site: str,
                  year_from: int | None = None, year_to: int | None = None) -> str:
    """构造不超过 128 字符的搜索式，优先保留学校名、年份词与 site: 限定。

    年份只作为检索关键词参与相关性排序，不做日期硬过滤；年份的精确性由
    year_allowed 对每条结果二次校验保证（排除明确落在区间外的页面）。
    """
    operators = ""
    if site:
        operators += f" site:{site}"
    operators += " lang:zh"
    budget = QUERY_LIMIT - len(operators)
    school = clean_text(school, 60)
    years = year_terms(year_from, year_to)
    body = " ".join(part for part in (school, years, clean_text(terms, 120), clean_text(keywords, 120)) if part).strip()
    if len(body) <= budget:
        return (body + operators).strip() or school
    # 超预算时学校名与年份词属于「定位信息」，优先保留，再逐词补主题词。
    head = " ".join(part for part in (school, years) if part).strip()
    head = head[:max(1, min(len(head), budget // 2))]
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
    # 年份区间：year_from ~ year_to（含端点）。两个都填同一个值即「只要这一年」。
    year_from: int | None = Field(default=None, ge=2000, le=2100)
    year_to: int | None = Field(default=None, ge=2000, le=2100)
    # 官网页面常不标发布时间，默认放行无法判定年份的页面（结果里单独计数）；
    # 取消勾选则严格排除，只保留能判定年份且落在区间内的页面。
    include_undated: bool = True
    # 兼容旧版「起始年份」指令；等价于只设 year_from。
    after_year: int | None = Field(default=None, ge=2000, le=2100)
    refresh: bool = False
    fetch_content: bool = True

    @field_validator("year_from", "year_to", "after_year", mode="before")
    @classmethod
    def widen_short_year(cls, value):
        """允许填 25 这样的两位简写；非法值给出可读的 422 提示。"""
        if value is None or value == "":
            return None
        year = normalize_year(value)
        if year is None:
            raise ValueError("年份需为 2000–2100 之间的四位年份，或 25 这样的两位简写")
        return year

    @model_validator(mode="after")
    def settle_year_range(self):
        low, high = self.year_from, self.year_to
        if low is None and high is None and self.after_year is not None:
            low = self.after_year
        if low is None and high is not None:
            low = high
        if low is not None and high is not None and low > high:
            raise ValueError("起始年份不能大于结束年份")
        self.year_from, self.year_to = low, high
        return self

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

    def year_label(self) -> str:
        """给界面与入库抬头用的可读年份区间。"""
        low, high = self.year_from, self.year_to
        if not low and not high:
            return ""
        if low and high:
            return str(low) if low == high else f"{low}–{high}"
        return f"{low} 年起" if low else f"截至 {high} 年"

    def query_key(self, topic: str) -> str:
        # 年份区间与检索式策略都进入批次键：不同区间、不同策略的归档互不复用，
        # 升级搜索式后旧的空批次/错误结果不会继续命中缓存。
        seed = "|".join((QUERY_STRATEGY, self.school.strip().lower(), self.site, topic,
                         self.keywords.strip(), str(self.after_year or ""),
                         str(self.year_from or ""), str(self.year_to or ""),
                         str(self.include_undated), str(self.max_results)))
        return sha256(seed.encode()).hexdigest()[:20]

    def queries(self) -> list[dict]:
        output = []
        for topic in self.topics:
            template = TOPIC_TEMPLATES[topic]
            query = compose_query(self.school, template["terms"], self.keywords,
                                  self.site, self.year_from, self.year_to)
            output.append({"topic": topic, "label": template["label"],
                           "category": self.category_for(topic), "query": query,
                           "queryKey": self.query_key(topic),
                           "yearFrom": self.year_from, "yearTo": self.year_to})
        return output
