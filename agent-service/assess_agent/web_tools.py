"""智能体可用的联网检索工具。

三条能力：
- search_school_web：按「学校 + 官网域名 + 主题」下发定向检索指令，抓取正文并归档；
- fetch_school_page：抓取指定的官网页面；
- search_web_archive：只查归档库、不联网，复用此前已入库的官网资料。

工具统一把结果注入 EvidenceStore，模型随后用 read_evidence / search_evidence 引用，
与人工上传资料走同一套取证与校验链路。
"""
import re

from langchain.tools import tool

from .errors import AgentError
from .web_archive import entry_evidence
from .web_contracts import TOPIC_TEMPLATES, WebSearchDirective, normalize_site, normalize_year


def optional_year(value):
    """工具参数里的年份：0/空表示不限，25 这类简写补成 2025。"""
    if value in (None, "", 0):
        return None
    year = normalize_year(value)
    if year is None:
        raise AgentError("WEB_SEARCH_DIRECTIVE", "年份需为 2000–2100 的四位年份或 25 这样的简写")
    return year


def inject_web_evidence(store, entries, category="REFERENCE", max_pages=8) -> list[str]:
    """把归档条目转成证据，同步 corpus 与 by_id，返回可用的证据 ID。"""
    identifiers = []
    seen = set()
    for entry in entries[:max_pages]:
        for evidence in entry_evidence(entry, entry.get("category") or category):
            if evidence.id in seen:
                continue
            seen.add(evidence.id)
            identifiers.append(evidence.id)
            store.by_id[evidence.id] = evidence
            if not any(item.id == evidence.id for item in store.corpus.evidence):
                store.corpus.evidence.append(evidence)
    return identifiers


def _error(error: AgentError) -> dict:
    return {"error": error.code, "message": error.message}


def _brief(entry: dict, evidence_ids: list[str]) -> dict:
    return {
        "entryId": entry.get("id"), "title": entry.get("title"), "url": entry.get("url"),
        "host": entry.get("host"), "topic": entry.get("topic"), "topicLabel": entry.get("topicLabel"),
        "category": entry.get("category"), "fetchedAt": entry.get("fetchedAt"),
        "reused": bool(entry.get("reused")), "contentChars": entry.get("contentChars", len(entry.get("content", ""))),
        "snippet": (entry.get("snippet") or "")[:300], "evidenceIds": evidence_ids,
    }


def web_tools(store, service, school: str, year: int):
    """构造绑定到本次任务的联网工具集合。"""
    def guarded(fn):
        if store.cancel_check:
            store.cancel_check()
        try:
            return fn()
        except AgentError as error:
            return _error(error)
        except (ValueError, TypeError) as error:
            return _error(AgentError("WEB_SEARCH_DIRECTIVE", f"检索指令无效：{type(error).__name__}"))
        except Exception:
            # Web access is an enhancement: never let it abort a chapter.
            return _error(AgentError("WEB_SEARCH_UNAVAILABLE", "联网检索暂时不可用，请继续使用已上传资料取证"))

    @tool
    def list_web_search_topics() -> dict:
        """列出可用的学校官网定向检索主题及其默认资料类别，用于构造搜索指令。"""
        return {"topics": [{"id": key, "label": value["label"], "category": value["category"],
                            "terms": value["terms"]} for key, value in TOPIC_TEMPLATES.items()],
                "school": school, "year": year,
                "usage": "用 search_school_web 指定 topic、官网域名 site、补充关键词与 year_from/year_to 年份区间；结果会自动归档并生成可引用证据 ID"}

    @tool
    def search_school_web(topic: str = "leadership_speech", site: str = "", keywords: str = "",
                          max_results: int = 3, year_from: int = 0, year_to: int = 0,
                          refresh: bool = False) -> dict:
        """定向检索特定学校的官网公开信息，如领导讲话、战略规划、办学定位、人才培养、质量报告。
        topic 取值先用 list_web_search_topics 查询；site 填学校官网域名（如 bistu.edu.cn）可确保结果只来自官网；
        keywords 补充更具体的要求；max_results 为 1–5；refresh=true 忽略归档缓存重新联网。
        year_from/year_to 指定资料年份区间（含端点，可用 25 这样的简写）；例如两个都填 2025 就只归档 2025 年的网页，
        两个都填 0 表示不限年份。无法判定年份的页面会被排除，避免混入其他年度资料。
        结果自动归档并形成 evidenceIds，可用 read_evidence 读取正文后写入报告。
        """
        def execute():
            if topic not in TOPIC_TEMPLATES:
                raise AgentError("WEB_SEARCH_DIRECTIVE", "不支持的检索主题，请先调用 list_web_search_topics")
            directive = WebSearchDirective.model_validate({
                "enabled": True, "school": school, "site": normalize_site(site),
                "topics": [topic], "keywords": keywords,
                "maxResults": max(1, min(int(max_results), 5)), "maxPages": max(1, min(int(max_results), 5)),
                "yearFrom": optional_year(year_from), "yearTo": optional_year(year_to),
                "includeUndated": False, "afterYear": None,
                "refresh": bool(refresh), "fetchContent": True,
            })
            result = service.run(directive)
            briefs = []
            for entry in result["entries"]:
                ids = inject_web_evidence(store, [entry], directive.category_for(topic), max_pages=1)
                briefs.append(_brief(entry, ids))
            return {"school": result["school"], "site": result["site"],
                    "queries": result["queries"], "pages": briefs, "reused": result["reused"],
                    "errors": result["errors"],
                    "note": "网页内容已归档并形成证据；请先用 read_evidence 核实，再写入章节，不要把网址写进正文"}
        return guarded(execute)

    @tool
    def fetch_school_page(url: str, topic: str = "custom") -> dict:
        """抓取并归档一个指定的学校官网页面（须为 http/https 且含主机名）。
        url 应来自 search_school_web 或归档库；抓取结果会形成可引用的 evidenceIds。
        """
        def execute():
            entry = service.fetch_page(url, school=school, topic=topic)
            ids = inject_web_evidence(store, [entry], entry.get("category") or "REFERENCE", max_pages=1)
            return {**_brief(entry, ids), "note": "正文已归档，请用 read_evidence 读取后再引用"}
        return guarded(execute)

    @tool
    def search_web_archive(query: str, topic: str = "", limit: int = 5) -> dict:
        """离线检索已归档的学校官网资料，不产生联网请求与费用，供后续任务直接复用。
        query 为关键词，topic 可按主题过滤，limit 为 1–10。命中结果会重新注入本次证据。
        """
        def execute():
            words = [word for word in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", (query or "").lower()) if word]
            if not words or len(query or "") > 200:
                raise AgentError("WEB_SEARCH_DIRECTIVE", "请提供 200 字以内的检索关键词")
            count = max(1, min(int(limit), 10))
            candidates = service.archive.list(school=school, topic=topic, limit=200)
            scored = []
            for item in candidates:
                text = " ".join(str(item.get(key, "")) for key in ("title", "snippet", "topicLabel", "url")).lower()
                score = sum(min(text.count(word), 5) * len(word) for word in set(words))
                if score:
                    scored.append((score, item))
            scored.sort(key=lambda pair: (-pair[0], pair[1]["fetchedAt"]))
            briefs = []
            for _, item in scored[:count]:
                entry = service.archive.get(item["id"])
                ids = inject_web_evidence(store, [entry], entry.get("category") or "REFERENCE", max_pages=1)
                briefs.append({**_brief(entry, ids), "reused": True})
            return {"query": query, "total": len(scored), "pages": briefs,
                    "note": "这些是此前归档的官网资料，可直接引用；需要最新信息时改用 search_school_web"}
        return guarded(execute)

    return [list_web_search_topics, search_school_web, fetch_school_page, search_web_archive]
