"""DeepSeek 联网搜索模块。

对接 DeepSeek 官方 Search / Fetch 接口（/v1/tools/search、/v1/tools/fetch），
为智能体提供显式的「定向学校检索指令」：

    WebSearchDirective(school="北京信息科技大学", site="bistu.edu.cn",
                       topics=["leadership_speech", "strategic_plan"])

指令会展开成带 site:/lang:/after: 语法的搜索式，结果经安全过滤后写入 WebArchive
归档，后续相同指令在缓存期内直接复用，不再联网、不再计费。

密钥默认复用「模型设置」里已保存的 DeepSeek API Key，也可用
ASSESS_WEB_SEARCH_API_KEY 单独指定。Search/Fetch 是官方接口，不走模型配置的
中转地址，可用 ASSESS_WEB_SEARCH_BASE_URL 覆盖。
"""
import httpx

from .errors import AgentError
from .web_archive import WebArchive
from .web_contracts import (QUERY_LIMIT, TOPIC_TEMPLATES, WebSearchDirective, clean_text,
                            host_allowed, host_of, page_years, safe_url, year_allowed)

DEFAULT_BASE_URL = "https://api.deepseek.com"
MIN_USEFUL_CONTENT = 400


class DeepSeekSearchClient:
    """DeepSeek Search / Fetch 原始 JSON API 的轻量客户端。"""
    SEARCH_PATH = "/v1/tools/search"
    FETCH_PATH = "/v1/tools/fetch"

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 20.0, transport=None):
        if not api_key or not api_key.strip():
            raise AgentError("WEB_SEARCH_NOT_CONFIGURED",
                             "尚未配置联网搜索密钥：请在模型设置中保存 DeepSeek API Key，或设置 ASSESS_WEB_SEARCH_API_KEY")
        self.api_key = api_key.strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def _post(self, path: str, payload: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport, follow_redirects=False) as client:
                response = client.post(self.base_url + path, headers=headers, json=payload)
        except httpx.TimeoutException as error:
            raise AgentError("WEB_SEARCH_TIMEOUT", "联网搜索超时，请稍后重试") from error
        except httpx.RequestError as error:
            raise AgentError("WEB_SEARCH_CONNECTION", "无法连接联网搜索服务，请检查网络或代理") from error
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError as error:
                raise AgentError("WEB_SEARCH_RESPONSE", "联网搜索服务返回了无法解析的结果") from error
            if not isinstance(body, dict):
                raise AgentError("WEB_SEARCH_RESPONSE", "联网搜索服务返回了无法解析的结果")
            return body
        # Never copy upstream text into UI or logs: it can contain school data or keys.
        mapping = {
            400: ("WEB_SEARCH_BAD_REQUEST", "联网搜索请求参数无效，请检查学校、域名与检索主题"),
            401: ("WEB_SEARCH_AUTH", "联网搜索认证失败，请检查 DeepSeek API Key"),
            402: ("WEB_SEARCH_QUOTA", "DeepSeek 账号余额不足，联网搜索不可用"),
            403: ("WEB_SEARCH_FORBIDDEN", "联网搜索请求被安全策略拒绝"),
            404: ("WEB_SEARCH_NOT_FOUND", "未获取到可用的网页内容"),
            408: ("WEB_SEARCH_TIMEOUT", "联网搜索超时，请稍后重试"),
            413: ("WEB_SEARCH_LIMIT", "联网搜索请求内容过大"),
            429: ("WEB_SEARCH_RATE_LIMIT", "联网搜索已限流，请稍后重试"),
        }
        code, message = mapping.get(response.status_code,
                                    ("WEB_SEARCH_UNAVAILABLE", "联网搜索服务暂时不可用，请稍后重试")
                                    if response.status_code >= 500 else
                                    ("WEB_SEARCH_BAD_REQUEST", "联网搜索请求被拒绝"))
        raise AgentError(code, message)

    def search(self, query: str, count: int = 5, need_content: bool = True, ori_question: str = "") -> list[dict]:
        payload = {"query": query[:QUERY_LIMIT], "count": max(1, min(count, 50)), "need_content": bool(need_content)}
        if ori_question:
            payload["ori_question"] = clean_text(ori_question, 128)
        body = self._post(self.SEARCH_PATH, payload)
        results = body.get("results")
        if not isinstance(results, list):
            raise AgentError("WEB_SEARCH_RESPONSE", "联网搜索服务返回了无法解析的结果")
        return [item for item in results if isinstance(item, dict)]

    def fetch(self, url: str, max_chars: int = 40000) -> dict:
        body = self._post(self.FETCH_PATH, {"url": url})
        return {"title": clean_text(body.get("title", ""), 300),
                "content": clean_text(body.get("content", ""), max_chars)}


def resolve_credentials(settings, data_root) -> tuple[str, str, str]:
    """密钥优先取环境变量，否则复用模型设置中的 DeepSeek Key。"""
    from .model_config import ModelConfigStore
    key = settings.web_search_api_key.get_secret_value().strip()
    source = "environment" if key else "none"
    if not key:
        try:
            key = (ModelConfigStore(data_root).runtime("deepseek") or {}).get("key", "").strip()
        except AgentError:
            key = ""
        source = "model-config" if key else "none"
    base = (settings.web_search_base_url or DEFAULT_BASE_URL).strip()
    return key, base, source


class WebSearchService:
    """指令展开 → 联网检索 → 安全过滤 → 归档复用。"""
    def __init__(self, settings, data_root, client=None, archive=None):
        self.settings = settings
        self.data_root = data_root
        self.archive = archive or WebArchive(data_root / "web-archive.sqlite3")
        self._client = client

    @property
    def client(self):
        if self._client is None:
            key, base, _ = resolve_credentials(self.settings, self.data_root)
            self._client = DeepSeekSearchClient(key, base, float(self.settings.web_search_timeout))
        return self._client

    def status(self) -> dict:
        key, base, source = resolve_credentials(self.settings, self.data_root)
        return {
            "provider": "deepseek", "configured": bool(key), "keySource": source,
            "baseUrl": base, "searchEndpoint": DeepSeekSearchClient.SEARCH_PATH,
            "fetchEndpoint": DeepSeekSearchClient.FETCH_PATH,
            "enabled": self.settings.web_search_enabled,
            "cacheDays": self.settings.web_search_cache_days,
            "maxPages": self.settings.web_search_max_pages,
            "topics": [{"id": key_, "label": value["label"], "category": value["category"],
                        "terms": value["terms"]} for key_, value in TOPIC_TEMPLATES.items()],
            "archive": self.archive.stats(),
            "hint": "复用模型设置中的 DeepSeek API Key；Search/Fetch 按成功请求次数单独计费",
        }

    def preview(self, directive: WebSearchDirective) -> dict:
        specs = directive.queries()
        hint = ""
        if directive.year_from or directive.year_to:
            hint = (f"年份 {directive.year_label()} 作为检索关键词参与排序，不使用 after:/before: 时间过滤；"
                    "能判定年份但落在区间外的页面会被排除，未标注年份的页面默认放行并单独计数。")
        return {"school": directive.school, "site": directive.site,
                "maxResults": directive.max_results, "maxPages": min(directive.max_pages, self.settings.web_search_max_pages),
                "afterYear": directive.after_year,
                "yearFrom": directive.year_from, "yearTo": directive.year_to,
                "yearLabel": directive.year_label(), "includeUndated": directive.include_undated,
                "yearHint": hint, "refresh": directive.refresh, "queries": specs}

    @staticmethod
    def _years_of(entry: dict) -> list[int]:
        """归档条目（camelCase）与原始搜索结果（snake_case）都能判定年份。

        只用发布时间/标题/URL，不用摘要与正文：2025 年的讲话常回顾「2024 年
        工作」，用摘要判定会把目标年度页面误判成区间外并丢弃，反而加重误杀。
        无法判定年份的页面由 include_undated 决定放行，并在结果里单独计数。
        """
        return page_years(published=str(entry.get("publishedAt") or entry.get("date") or ""),
                          title=str(entry.get("title") or ""), url=str(entry.get("url") or ""))

    @staticmethod
    def _year_allowed(entry: dict, directive: WebSearchDirective) -> bool:
        return year_allowed(WebSearchService._years_of(entry),
                            directive.year_from, directive.year_to, directive.include_undated)

    def _materialize(self, result: dict, spec: dict, directive: WebSearchDirective) -> dict | None:
        url = safe_url(result.get("url"))
        if not url or not host_allowed(url, directive.site):
            return None
        title = clean_text(result.get("title", ""), 300)
        snippet = clean_text(result.get("snippet") or result.get("summary") or "", 800)
        content = clean_text(result.get("content", ""), self.settings.web_search_max_page_chars)
        source = "search"
        fetch_error = ""
        if directive.fetch_content and len(content) < MIN_USEFUL_CONTENT:
            try:
                page = self.client.fetch(url, self.settings.web_search_max_page_chars)
                if len(page["content"]) > len(content):
                    content, source = page["content"], "fetch"
                    if not title:
                        title = page["title"]
            except AgentError as error:
                fetch_error = error.code
                # A failed Fetch must not discard a perfectly usable search result.
        if not content:
            content = snippet
        if not content.strip() or not title.strip():
            return None
        page = {
            "url": url, "title": title, "site_name": clean_text(result.get("site_name", ""), 200),
            "host": host_of(url), "school": directive.school, "site": directive.site,
            "topic": spec["topic"], "topic_label": spec["label"], "query": spec["query"],
            "query_key": spec["queryKey"], "category": spec["category"],
            "snippet": snippet, "content": content, "source": source,
            "authority": result.get("authority") if isinstance(result.get("authority"), int) else None,
            "rerank_score": result.get("rerank_score") if isinstance(result.get("rerank_score"), (int, float)) else None,
            "published_at": clean_text(result.get("date", ""), 60),
        }
        entry = self.archive.save(page)
        entry["fetchError"] = fetch_error
        return entry

    def run(self, directive: WebSearchDirective, *, use_cache: bool = True) -> dict:
        directive = directive.with_school(self.settings.web_search_default_school)
        if not directive.school.strip():
            raise AgentError("WEB_SEARCH_DIRECTIVE", "请填写要检索的学校名称")
        specs = directive.queries()
        page_budget = min(directive.max_pages, self.settings.web_search_max_pages)
        entries, errors = {}, []
        reused = 0
        filtered_years = 0
        undated = 0
        seen_urls = set()
        for spec in specs:
            if len(entries) >= page_budget:
                break
            cached = self.archive.recent(spec["queryKey"], self.settings.web_search_cache_days) if (
                use_cache and not directive.refresh) else []
            if cached:
                kept = 0
                for entry in cached:
                    if not self._year_allowed(entry, directive):
                        filtered_years += 1
                        continue
                    entries[entry["id"]] = {**entry, "reused": True}
                    kept += 1
                reused += kept
                continue
            try:
                results = self.client.search(spec["query"], count=directive.max_results,
                                             need_content=True,
                                             ori_question=f"检索{directive.school}官网{directive.year_label()}公开信息：{spec['label']}")
            except AgentError as error:
                errors.append({"topic": spec["topic"], "query": spec["query"],
                               "code": error.code, "message": error.message})
                continue
            ordered = sorted(results, key=lambda item: (
                -(item.get("authority") or 0) * 10 - (item.get("rerank_score") or 0)))
            batch_ids = []
            for result in ordered:
                if len(entries) >= page_budget:
                    break
                url = safe_url(result.get("url"))
                if not url or url in seen_urls or not host_allowed(url, directive.site):
                    continue
                seen_urls.add(url)
                # 年份硬过滤必须早于 Fetch：不为区间外的页面多产生一次计费请求。
                if not self._year_allowed(result, directive):
                    filtered_years += 1
                    continue
                try:
                    entry = self._materialize(result, spec, directive)
                except AgentError as error:
                    errors.append({"topic": spec["topic"], "code": error.code, "message": error.message})
                    continue
                if entry:
                    entry["reused"] = False
                    entries[entry["id"]] = entry
                    batch_ids.append(entry["id"])
            self.archive.record_batch(spec["queryKey"], batch_ids)
        result_entries = sorted(entries.values(), key=lambda item: (
            item.get("reused", False), -(item.get("authority") or 0), -(item.get("rerankScore") or 0)))
        # 明确落在区间外的页面已丢弃；这里统计「无年份依据、按年份关键词召回」的页面，
        # 让界面如实提示哪些资料属于推定，便于用户决定是否改走严格模式。
        undated = sum(1 for item in result_entries
                      if (directive.year_from or directive.year_to) and not self._years_of(item))
        for item in result_entries:
            item["undated"] = bool((directive.year_from or directive.year_to) and not self._years_of(item))
        return {"school": directive.school, "site": directive.site, "queries": specs,
                "entries": result_entries, "reused": reused, "errors": errors,
                "count": len(result_entries), "filteredYears": filtered_years,
                "undated": undated, "yearFrom": directive.year_from, "yearTo": directive.year_to,
                "yearLabel": directive.year_label(), "includeUndated": directive.include_undated}

    def fetch_page(self, url: str, *, school: str, topic: str = "custom",
                   category: str = "REFERENCE") -> dict:
        """智能体工具：定向抓取单个官网页面并归档。"""
        safe = safe_url(url)
        if not safe:
            raise AgentError("WEB_SEARCH_URL", "请提供有效的学校官网 HTTP(S) 页面地址")
        page = self.client.fetch(safe, self.settings.web_search_max_page_chars)
        if not page["content"].strip():
            raise AgentError("WEB_SEARCH_EMPTY", "该页面没有可提取的正文")
        spec = {"topic": topic, "label": TOPIC_TEMPLATES.get(topic, {}).get("label", "定向抓取"),
                "category": category, "query": safe, "queryKey": ""}
        saved = self.archive.save({
            "url": safe, "title": page["title"], "host": host_of(safe), "school": school,
            "site": "", "topic": topic, "topic_label": spec["label"], "query": safe, "query_key": "",
            "category": category, "snippet": clean_text(page["content"], 800), "content": page["content"],
            "source": "fetch", "authority": None, "rerank_score": None, "published_at": "",
        })
        return {**saved, "reused": False}
