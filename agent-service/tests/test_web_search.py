"""联网搜索模块离线测试：不发起真实网络请求。"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from assess_agent.app import create_app
from assess_agent.errors import AgentError
from assess_agent.evidence import EvidenceStore
from assess_agent.schemas import Corpus, JobRequest
from assess_agent.settings import CHAPTERS, Settings
from assess_agent.web_archive import WebArchive, entry_evidence
from assess_agent.web_contracts import (WebSearchDirective, compose_query, host_allowed,
                                       normalize_site, normalize_year, page_years, safe_url,
                                       year_allowed)
from assess_agent.web_search import DeepSeekSearchClient, WebSearchService
from assess_agent.web_tools import web_tools

SITE = "bistu.edu.cn"
OFFICIAL = f"https://www.{SITE}/news/1"
OFFSITE = "https://news.example.org/other"


def make_settings(tmp_path, **changes):
    defaults = dict(data_root=tmp_path, web_search_api_key="test-only-key",
                    web_search_cache_days=30, web_search_max_pages=5,
                    web_search_max_page_chars=20000, web_search_timeout=5)
    defaults.update(changes)
    return Settings(**defaults)


def search_results(content="学校公开信息正文。" * 60):
    return [
        {"title": "校长在人才培养工作会议上的讲话", "url": OFFICIAL,
         "snippet": "学校坚持立德树人", "summary": "讲话摘要", "content": content,
         "site_name": "学校官网", "authority": 3, "rerank_score": 2.4,
         "date": "2026-05-01T00:00:00+00:00"},
        {"title": "站外转载", "url": OFFSITE, "snippet": "站外内容",
         "content": "不应归档" * 100, "authority": 1, "rerank_score": 1.0},
    ]


def mock_client(results=None, status=200, body=None):
    calls = []

    def handler(request):
        calls.append((request.url.path, json.loads(request.content or b"{}"),
                      request.headers.get("authorization")))
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "upstream"}})
        if body is not None:
            return httpx.Response(200, json=body)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": results if results is not None else search_results()})
        if request.url.path.endswith("/fetch"):
            return httpx.Response(200, json={"title": "抓取标题", "content": "抓取正文。" * 80})
        return httpx.Response(404, json={})

    return DeepSeekSearchClient("test-only-key", transport=httpx.MockTransport(handler)), calls


def directive(**changes):
    values = {"school": "测试大学", "site": SITE, "topics": ["leadership_speech"],
              "keywords": "", "enabled": True, "maxResults": 3, "maxPages": 3,
              "fetchContent": False}
    values.update(changes)
    return WebSearchDirective.model_validate(values)


def test_directive_builds_site_scoped_bounded_queries():
    spec = directive(afterYear=2024)
    query = spec.queries()[0]["query"]
    assert f"site:{SITE}" in query and "lang:zh" in query and "2024" in query and "after:" not in query
    assert "测试大学" in query and len(query) <= 128
    assert spec.queries()[0]["category"] == "SPEECH"
    assert directive(topics=["strategic_plan"]).queries()[0]["category"] == "REFERENCE"
    assert len(compose_query("测试大学", "词 " * 200, "", SITE, None)) <= 128
    assert spec.query_key("leadership_speech") != directive(keywords="补充").query_key("leadership_speech")


def test_directive_rejects_unsafe_input():
    assert normalize_site("https://www.bistu.edu.cn/xxgk") == "www." + SITE
    with pytest.raises(ValidationError):
        directive(site="not a domain!")
    with pytest.raises(ValidationError):
        directive(topics=["unknown_topic"])
    with pytest.raises(ValidationError):
        directive(keywords="site:evil.com")


def test_safe_url_and_host_scope():
    assert safe_url(OFFICIAL) == OFFICIAL
    assert not safe_url("http://127.0.0.1/admin")
    assert not safe_url("http://localhost/a")
    assert not safe_url("ftp://www.bistu.edu.cn/a")
    assert not safe_url("https://user:pw@www.bistu.edu.cn/a")
    assert not safe_url("https://www.bistu.edu.cn/a with space")
    assert host_allowed(f"https://xx.{SITE}/a", SITE)
    assert not host_allowed("https://bistu.edu.cn.evil.com/a", SITE)


def test_archive_dedupe_batch_reuse_and_invalidation(tmp_path):
    archive = WebArchive(tmp_path / "archive.sqlite3")
    page = {"url": OFFICIAL, "title": "讲话", "host": "www." + SITE, "school": "测试大学",
            "site": SITE, "topic": "leadership_speech", "category": "SPEECH",
            "content": "正文内容。" * 100, "snippet": "摘要"}
    first = archive.save(page)
    assert first["id"].startswith("W-") and first["materialId"] == ""
    archive.mark_materialized([{"entryId": first["id"], "materialId": "material-1"}])
    same = archive.save(page)
    assert same["id"] == first["id"] and same["materialId"] == "material-1"
    assert not same["contentChanged"]
    changed = archive.save({**page, "content": "更新后的正文。" * 120})
    assert changed["contentChanged"] and changed["materialId"] == ""

    key = "batch-key"
    archive.record_batch(key, [first["id"]])
    assert [item["id"] for item in archive.recent(key, 30)] == [first["id"]]
    assert archive.recent(key, 0) == []
    assert archive.recent("missing", 30) == []
    assert archive.stats()["total"] == 1
    assert len(archive.list(school="测试大学")) == 1
    archive.delete(first["id"])
    with pytest.raises(AgentError):
        archive.get(first["id"])


def test_entry_evidence_is_citable_and_scoped():
    pieces = entry_evidence({"id": "W-abc", "url": OFFICIAL, "title": "讲话",
                             "host": "www." + SITE, "content": "正文" * 3000}, "SPEECH")
    assert len(pieces) > 1
    assert all(item.id.startswith("W-") and item.category == "SPEECH" for item in pieces)
    assert all(item.material_id.startswith("web-") for item in pieces)
    assert OFFICIAL in pieces[0].text and "官网" in pieces[0].location


def test_search_client_protocol_and_error_mapping():
    client, calls = mock_client()
    results = client.search("测试", count=2)
    assert len(results) == 2
    assert calls[0][0].endswith("/v1/tools/search")
    assert calls[0][1]["need_content"] is True and calls[0][1]["count"] == 2
    assert calls[0][2] == "Bearer test-only-key"
    page = client.fetch(OFFICIAL)
    assert page["title"] and page["content"]

    denied, _ = mock_client(status=401)
    with pytest.raises(AgentError) as failure:
        denied.search("测试")
    assert failure.value.code == "WEB_SEARCH_AUTH"


def test_service_filters_offsite_archives_and_reuses_cache(tmp_path):
    settings = make_settings(tmp_path)
    client, calls = mock_client()
    archive = WebArchive(tmp_path / "archive.sqlite3")
    service = WebSearchService(settings, tmp_path, client=client, archive=archive)

    preview = service.preview(directive())
    assert preview["queries"][0]["query"].startswith("测试大学")

    first = service.run(directive())
    assert first["count"] == 1
    assert first["entries"][0]["url"] == OFFICIAL
    assert all(entry["url"] != OFFSITE for entry in first["entries"])
    assert archive.stats()["total"] == 1
    assert len([call for call in calls if call[0].endswith("/search")]) == 1

    second = service.run(directive())
    assert second["reused"] == 1 and second["count"] == 1
    assert second["entries"][0]["reused"] is True
    assert len([call for call in calls if call[0].endswith("/search")]) == 1

    refreshed = service.run(directive(refresh=True))
    assert refreshed["reused"] == 0
    assert len([call for call in calls if call[0].endswith("/search")]) == 2


def test_service_fetch_fallback_and_direct_page(tmp_path):
    settings = make_settings(tmp_path)
    client, calls = mock_client(results=[{**search_results()[0], "content": ""}])
    service = WebSearchService(settings, tmp_path, client=client)
    outcome = service.run(directive(fetchContent=True))
    assert outcome["count"] == 1
    assert any(call[0].endswith("/fetch") for call in calls)
    assert outcome["entries"][0]["source"] == "fetch"

    page = service.fetch_page(OFFICIAL, school="测试大学")
    assert page["url"] == OFFICIAL and page["content"]
    with pytest.raises(AgentError):
        service.fetch_page("http://127.0.0.1/x", school="测试大学")


def test_web_tools_inject_evidence_into_corpus(tmp_path):
    settings = make_settings(tmp_path)
    client, _ = mock_client()
    service = WebSearchService(settings, tmp_path, client=client)
    store = EvidenceStore(Corpus())
    tools = {item.name: item for item in web_tools(store, service, "测试大学", 2025)}
    assert "list_web_search_topics" in tools
    result = tools["search_school_web"].invoke({"topic": "leadership_speech", "site": SITE, "max_results": 2})
    assert result["pages"] and result["pages"][0]["evidenceIds"]
    assert store.corpus.evidence and result["pages"][0]["evidenceIds"][0] in store.by_id
    store.web_tools_provider = {"service": service, "school": "测试大学", "year": 2025}
    assert any(item.name == "search_web_archive" for item in store.tools())

    reused = tools["search_web_archive"].invoke({"query": "人才培养", "limit": 3})
    assert reused["total"] >= 1 and reused["pages"][0]["reused"] is True


def test_web_search_api_contract(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        status = client.get("/web-search/status").json()
        assert status["configured"] and status["provider"] == "deepseek"
        assert status["topics"] and "test-only-key" not in json.dumps(status)
        preview = client.post("/web-search/preview", json={
            "school": "测试大学", "site": SITE, "topics": ["strategic_plan"]}).json()
        assert f"site:{SITE}" in preview["queries"][0]["query"]
        assert client.post("/web-search/preview", json={"school": "x", "site": "bad domain"}).status_code == 422
        archive = client.get("/web-archive").json()
        assert archive["stats"]["total"] == 0 and archive["entries"] == []
        assert client.get("/web-archive/pending").json()["entries"] == []


def test_web_search_run_materialize_and_delete_api(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    fake, _ = mock_client()
    real_service = WebSearchService
    monkeypatch.setattr("assess_agent.app.WebSearchService",
                        lambda service_settings, data_root: real_service(service_settings, data_root, client=fake))
    payload = {"school": "测试大学", "site": SITE, "topics": ["leadership_speech"],
               "maxResults": 3, "maxPages": 3, "fetchContent": False}
    with TestClient(create_app(settings)) as client:
        outcome = client.post("/web-search/run", json=payload).json()
        assert outcome["count"] == 1 and outcome["entries"][0]["content"]
        entry = outcome["entries"][0]
        assert client.get("/web-archive").json()["stats"]["total"] == 1
        assert client.get("/web-archive/pending").json()["entries"][0]["id"] == entry["id"]
        assert client.post("/web-archive/materialized", json={
            "items": [{"entryId": entry["id"], "materialId": "material-1"}]}).status_code == 204
        assert client.get(f"/web-archive/{entry['id']}").json()["materialId"] == "material-1"
        assert client.get("/web-archive/pending").json()["entries"] == []
        assert client.delete(f"/web-archive/{entry['id']}").status_code == 204


def test_backend_job_request_round_trips_web_search_contract():
    """Spring Boot 传来的 camelCase 任务必须能被 JobRequest 严格解析。"""
    payload = {
        "taskId": "11111111-1111-4111-8111-111111111111",
        "school": "测试大学", "year": 2025, "modelId": "deepseek", "templateId": "graduate",
        "chapters": [CHAPTERS[0]],
        "materials": [{"id": "22222222-2222-4222-8222-222222222222",
                       "name": "data.xlsx", "category": "DATA", "extractedText": None}],
        "instructions": "", "customTopic": None,
        "webSearch": {"enabled": True, "school": "测试大学", "site": SITE,
                      "topics": ["leadership_speech", "strategic_plan"],
                      "maxResults": 4, "maxPages": 8, "refresh": False, "fetchContent": True},
    }
    request = JobRequest.model_validate(payload)
    assert request.web_search and request.web_search.enabled
    assert request.web_search.site == SITE
    assert request.model_dump(by_alias=True)["webSearch"]["topics"] == ["leadership_speech", "strategic_plan"]
    assert JobRequest.model_validate({**payload, "webSearch": None}).web_search is None


# ---------------------------------------------------------------- 年份区间


def dated_results():
    """三条结果：区间内、区间外、无年份线索；正文够长以免触发 Fetch。"""
    base = search_results()[0]
    return [
        {**base, "url": f"https://www.{SITE}/2025/a", "title": "2025年人才培养工作会议讲话",
         "date": "2025-05-01T00:00:00+00:00"},
        {**base, "url": f"https://www.{SITE}/2024/b", "title": "2024年学校工作总结",
         "date": "2024-05-01T00:00:00+00:00"},
        {**base, "url": f"https://www.{SITE}/news/c", "title": "学校简介", "date": None},
    ]


def test_year_range_directive_accepts_short_years_and_builds_operators():
    spec = directive(yearFrom=25, yearTo=25)
    assert (spec.year_from, spec.year_to) == (2025, 2025)
    assert spec.year_label() == "2025"
    query = spec.queries()[0]["query"]
    assert "2025" in query and "after:" not in query and "before:" not in query and len(query) <= 128

    # 只填结束年份自动补成单年区间；旧版 afterYear 仍等价于只设下界。
    assert directive(yearTo=2025).year_from == 2025
    legacy = directive(afterYear=2024)
    assert "2024" in legacy.queries()[0]["query"] and "after:" not in legacy.queries()[0]["query"]
    assert normalize_year("25") == 2025 and normalize_year(1999) is None

    with pytest.raises(ValidationError):
        directive(yearFrom=2026, yearTo=2025)
    with pytest.raises(ValidationError):
        directive(yearFrom=3000)


def test_page_years_extraction_and_verdict():
    assert page_years(published="2025-05-01T00:00:00+00:00") == [2025]
    assert page_years(title="2023—2025 事业发展规划") == [2023, 2025]
    # 8 位日期编号不能被误判成年份
    assert page_years(url=f"https://www.{SITE}/202405150001/a") == []
    assert year_allowed([2023, 2025], 2025, 2025)    # 任一命中即保留
    assert not year_allowed([2024], 2025, 2025)      # 明确在区间外
    assert year_allowed([], 2025, 2025)              # 无年份默认放行（官网多不标日期）
    assert not year_allowed([], 2025, 2025, False)   # 显式严格排除
    assert year_allowed([], None, None)              # 未限定年份一律放行


def test_run_keeps_only_in_range_pages_before_any_fetch(tmp_path):
    settings = make_settings(tmp_path)
    client, calls = mock_client(results=dated_results())
    archive = WebArchive(tmp_path / "archive.sqlite3")
    service = WebSearchService(settings, tmp_path, client=client, archive=archive)

    outcome = service.run(directive(yearFrom=2025, yearTo=2025, fetchContent=True))
    assert outcome["count"] == 2 and "2025" in outcome["entries"][0]["title"]
    assert outcome["yearLabel"] == "2025" and outcome["filteredYears"] == 1
    assert archive.stats()["total"] == 2
    # 明确落在区间外的页面在 Fetch 之前就被丢弃，不产生额外计费请求。
    assert not any(call[0].endswith("/fetch") for call in calls)

    preview = service.preview(directive(yearFrom=25, yearTo=25))
    assert preview["yearLabel"] == "2025" and preview["yearFrom"] == 2025

    strict = service.run(directive(yearFrom=2025, yearTo=2025, includeUndated=False))
    assert strict["count"] == 1 and strict["filteredYears"] == 2


def test_year_range_changes_cache_key_and_is_not_reused_across_years(tmp_path):
    assert directive(yearFrom=2025, yearTo=2025).query_key("leadership_speech") != \
        directive(yearFrom=2024, yearTo=2024).query_key("leadership_speech")
    settings = make_settings(tmp_path)
    client, calls = mock_client(results=dated_results())
    service = WebSearchService(settings, tmp_path, client=client)
    service.run(directive(yearFrom=2025, yearTo=2025))
    service.run(directive(yearFrom=2024, yearTo=2024))
    assert len([call for call in calls if call[0].endswith("/search")]) == 2


def test_web_search_api_year_range_contract(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        preview = client.post("/web-search/preview", json={
            "school": "测试大学", "site": SITE, "topics": ["strategic_plan"],
            "yearFrom": 25, "yearTo": 25}).json()
        assert preview["yearLabel"] == "2025" and preview["yearFrom"] == 2025
        assert "2025" in preview["queries"][0]["query"]
        assert "after:" not in preview["queries"][0]["query"] and "before:" not in preview["queries"][0]["query"]
        assert client.post("/web-search/preview", json={
            "school": "测试大学", "yearFrom": 2026, "yearTo": 2025}).status_code == 422
        assert client.post("/web-search/preview", json={
            "school": "测试大学", "yearFrom": "不是年份"}).status_code == 422


def test_job_request_round_trips_year_range():
    payload = {
        "taskId": "11111111-1111-4111-8111-111111111111",
        "school": "测试大学", "year": 2025, "modelId": "deepseek", "templateId": "graduate",
        "chapters": [CHAPTERS[0]],
        "materials": [{"id": "22222222-2222-4222-8222-222222222222",
                       "name": "data.xlsx", "category": "DATA", "extractedText": None}],
        "instructions": "", "customTopic": None,
        "webSearch": {"enabled": True, "school": "测试大学", "site": SITE,
                      "topics": ["leadership_speech"], "maxResults": 4, "maxPages": 8,
                      "yearFrom": 2025, "yearTo": 2025, "includeUndated": False},
    }
    request = JobRequest.model_validate(payload)
    assert (request.web_search.year_from, request.web_search.year_to) == (2025, 2025)
    dumped = request.model_dump(by_alias=True)["webSearch"]
    assert dumped["yearFrom"] == 2025 and dumped["yearTo"] == 2025
