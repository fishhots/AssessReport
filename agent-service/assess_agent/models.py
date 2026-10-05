from dataclasses import dataclass
from typing import Literal
import json
import logging
import time
from collections.abc import Callable
from urllib.parse import urlparse

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call
from langchain.agents.middleware.types import ModelResponse
from langchain.agents.structured_output import ToolStrategy
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.utils.function_calling import convert_to_openai_tool
from langsmith import tracing_context
from pydantic import Field, ValidationError, create_model

from .errors import AgentError, safe_model_error
from .evidence import numeric_values
from .schemas import ContentBlock, ContentTable, ContentVisual, JobRequest, SectionDraft
from .settings import PROVIDERS, REPORT_CONFIG, SERVICE_ROOT, Settings
from .reasoning import answer_text, parameters

logger = logging.getLogger(__name__)
MAX_RESEARCH_ROUNDS = 4
MAX_FORMAT_REPAIRS = 2


def validate_provider(settings: Settings, provider: str) -> dict:
    if provider not in PROVIDERS:
        raise AgentError("MODEL_PROVIDER", "不支持的模型系列")
    config = settings.provider_config(provider)
    if not all(config[key].strip() for key in ("key", "model", "base_url")):
        raise AgentError("MODEL_NOT_CONFIGURED", f"{provider} 尚未配置密钥、模型名称或接口地址")
    url = urlparse(config["base_url"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise AgentError("MODEL_CONFIG", "模型接口地址格式无效")
    try:
        extra = json.loads(config["extra_body"])
        if not isinstance(extra, dict):
            raise ValueError()
        config["extra_body"] = extra
    except (ValueError, TypeError) as exc:
        raise AgentError("MODEL_CONFIG", "模型额外参数必须为 JSON 对象") from exc
    return config


def provider_status(settings: Settings) -> list[dict]:
    result = []
    for provider in PROVIDERS:
        try:
            config = validate_provider(settings, provider)
            result.append({"id": provider, "configured": True, "model": config["model"]})
        except AgentError:
            result.append({"id": provider, "configured": False, "model": ""})
    return result


def build_model(settings: Settings, provider: str, max_retries: int = 0, stage: str = 'tools', provider_config=None):
    config = dict(provider_config if provider_config is not None else validate_provider(settings, provider))
    from .provider_routes import validate_channel
    channel = config.get('channel', 'official')
    validate_channel(provider, channel)
    extra, options, reasoning_metadata = parameters(config, provider, stage)
    config['extra_body'] = extra
    analysis = stage.startswith('analysis') and reasoning_metadata['analysisEnabled']
    timeout = min(600, max(settings.model_timeout, 180)) if analysis and stage != 'analysis-test' else settings.model_timeout
    relay = channel == 'zhizengzeng'
    if relay:
        import httpx
        testing = stage in ('test', 'analysis-test')
        relay_seconds = settings.relay_test_timeout if testing else settings.relay_model_timeout
        relay_timeout = {'connect': 10 if testing else 30, 'write': 15 if testing else 60,
                         'pool': 5 if testing else 30, 'read': relay_seconds}
        timeout = relay_seconds if provider == 'claude' else httpx.Timeout(**relay_timeout)
        # A timed-out paid request must never be retried silently by the SDK.
        max_retries = 0
        if not testing:
            options['streaming'] = True
            if provider in ('gpt', 'gemini'):
                options['stream_usage'] = True
    if provider == 'qwen' and stage not in ('test', 'analysis-test'):
        import httpx
        timeout = httpx.Timeout(connect=min(settings.model_timeout, 30), write=30, pool=30,
                                read=max(settings.model_timeout, 180))
    output_tokens = settings.analysis_output_tokens if analysis else settings.max_output_tokens
    if provider == "claude":
        from langchain_anthropic import ChatAnthropic
        from .relay_models import RelayChatAnthropic
        anthropic_model = RelayChatAnthropic if relay else ChatAnthropic
        model = anthropic_model(model=config["model"], api_key=config["key"], base_url=config["base_url"],
                              timeout=timeout, max_retries=max_retries, max_tokens=max(output_tokens, config.get('thinkingBudget', 4096)+2000) if analysis else output_tokens,
                              **options,
                              model_kwargs={"extra_body": config["extra_body"]} if config["extra_body"] else {})
        if relay:
            import anthropic
            # LangChain accepts scalar timeouts and caches its default HTTP
            # clients by that scalar. Construct SDK clients directly to use
            # distinct connection/read limits without its hash-based cache.
            sdk_params = {**model._client_params, 'timeout': anthropic.Timeout(**relay_timeout)}
            sync_params, async_params = dict(sdk_params), dict(sdk_params)
            if model.anthropic_proxy:
                sync_params['http_client'] = anthropic.DefaultHttpxClient(proxy=model.anthropic_proxy)
                async_params['http_client'] = anthropic.DefaultAsyncHttpxClient(proxy=model.anthropic_proxy)
            model._client = anthropic.Anthropic(**sync_params)
            model._async_client = anthropic.AsyncAnthropic(**async_params)
    elif provider == "gpt":
        from langchain_openai import ChatOpenAI
        official = channel == 'official'
        route_options = {'store': False, 'include': ['reasoning.encrypted_content']} if official else {}
        # GPT Chat Completions uses max_completion_tokens; other compatible
        # providers continue using max_tokens in the base adapter below.
        model = ChatOpenAI(model=config["model"], api_key=config["key"], base_url=config["base_url"],
                           timeout=timeout, max_retries=max_retries, max_tokens=output_tokens, **options,
                           extra_body=config["extra_body"], use_responses_api=official, **route_options,
                           disabled_params={"parallel_tool_calls": None})
    else:
        # The base implementation preserves max_tokens for compatible provider APIs.
        from .provider_models import CompatibleChatModel
        model = CompatibleChatModel(model=config["model"], api_key=config["key"], base_url=config["base_url"],
                              timeout=timeout, max_retries=max_retries, max_tokens=output_tokens, **options,
                              extra_body=config["extra_body"], use_responses_api=False,
                              disabled_params={"parallel_tool_calls": None})
    return model, {"provider": provider, "model": config["model"], "channel": channel, **reasoning_metadata,
                   **({'relayTimeoutSeconds': relay_seconds} if relay else {})}


@dataclass
class Budget:
    max_calls: int
    deadline: float
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cancel_check: Callable[[], None] | None = None

    def check_cancelled(self):
        if self.cancel_check:
            self.cancel_check()

    def check(self):
        self.check_cancelled()
        if time.monotonic() >= self.deadline:
            raise AgentError("JOB_TIMEOUT", "任务执行超过时间上限")
        if self.calls >= self.max_calls:
            raise AgentError("MODEL_CALL_LIMIT", "任务已达到模型调用次数上限")


class StreamGuard(BaseCallbackHandler):
    """Interrupt Qwen and relay streams on cancellation or a bounded deadline."""
    raise_error = True

    def __init__(self, budget, call_timeout=600):
        self.budget = budget
        self.call_timeout = call_timeout
        self.started = 0

    def on_chat_model_start(self, *args, **kwargs):
        self.started = time.monotonic()
        self.check()

    def check(self):
        self.budget.check_cancelled()
        if time.monotonic() >= self.budget.deadline:
            raise AgentError('JOB_TIMEOUT', '任务执行超过时间上限，已停止后续调用，可继续生成')
        if time.monotonic() - self.started >= self.call_timeout:
            raise AgentError('MODEL_TIMEOUT', '单次模型生成超过时间上限，已停止后续调用，可继续生成')

    def on_llm_new_token(self, *args, **kwargs):
        # Called for public-answer and reasoning chunks. The SDK closes its
        # response context when this callback raises; no retry is issued here.
        self.check()


class SectionWriter:
    def __init__(self, settings: Settings, request: JobRequest, evidence, model=None, provider_config=None):
        self.settings, self.request, self.evidence = settings, request, evidence
        if model is None:
            provider_config = provider_config if provider_config is not None else validate_provider(settings, request.model_id)
            self.model, self.metadata = build_model(settings, request.model_id, max_retries=0, provider_config=provider_config)
        else:
            self.model, self.metadata = model, {"provider": "test", "model": "offline-test-only"}
        self.analysis_model = None
        if model is None and self.metadata.get('analysisEnabled'):
            self.analysis_model, _ = build_model(settings, request.model_id, max_retries=0, stage='analysis', provider_config=provider_config)
        self.budget = Budget(settings.max_model_calls, time.monotonic() + settings.job_timeout)
        self.versions = {}
        self.call_trace = []
        self.cache = None
        if self.metadata.get('provider') == 'qwen' or self.metadata.get('channel') == 'zhizengzeng':
            guard = StreamGuard(self.budget, settings.relay_model_timeout
                                if self.metadata.get('channel') == 'zhizengzeng' else 600)
            for client in (self.model, self.analysis_model):
                if client is not None:
                    client.callbacks = [*(client.callbacks or []), guard]

    def skill(self, name: str) -> str:
        from hashlib import sha256
        text = (SERVICE_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        self.versions[name] = sha256(text.encode()).hexdigest()
        return text

    def write(self, title: str, query: str, diagnosis: bool = False,
              question: str = "", issues: list[str] | None = None, draft: dict | None = None,
              guidance: str = "", related_sections: list[dict] | None = None,
              repair_source_ids: list[str] | None = None, focus_paths: list[str] | None = None) -> SectionDraft:
        system = (SERVICE_ROOT / "prompts/system.md").read_text(encoding="utf-8")
        system += "\n\n" + "\n\n".join(self.skill(name) for name in (
            "source-evidence", "table-analysis", "deep-diagnosis" if diagnosis else "quality-report", "charts-tables", "review-export"))
        if self.evidence.visuals:
            self.evidence.visuals.active_title = title
        chapter = next((c for c in REPORT_CONFIG['chapters'] if c['title'] == title), {})
        statistics = self.evidence.survey.context(title, query + ' ' + question) if self.evidence.survey else []
        tokens = chapter.get('query',query).split()
        context = {
            "school": self.request.school, "year": self.request.year, "title": title,
            "question": question, "kind": "diagnosis" if diagnosis else "report",
            "chapterRequirements": guidance,
            "verifiedSectionSummaries": related_sections or [],
            "userRequirements": self.request.instructions,
            "materials": [{"id": str(m.id), "name": m.name, "category": m.category} for m in self.request.materials],
            "uploadedDataCoverage": self.evidence.inventory.summary() if self.evidence.inventory else [],
            "studentCommentStatistics": self.evidence.inventory.qualitative if self.evidence.inventory and (title.startswith('第六章') or diagnosis) else [],
            "initialEvidence": self.evidence.balanced_speech(query) if statistics else self.evidence.search(query, limit=4),
            "fixedOutline": chapter.get('outline',[]),
            "templateTopics": chapter.get('topics', []),
            "recommendationPaths": chapter.get('recommendationPaths', []),
            "verifiedStatistics": statistics,
            "tables": [{"materialId": t.material_id, "sheet": t.sheet,
                        "columns": ([c for c in t.columns if any(k in c for k in tokens)][:30] if len(t.columns)>60 else t.columns),
                        "columnCount": len(t.columns), "fieldIndexIsPartial": len(t.columns)>60,
                        "moreFields": "inspect_table 按关键词或offset查看全部字段"} for t in self.evidence.corpus.tables],
            "parseWarnings": self.evidence.corpus.warnings[:20],
            "visualRequirements": self.evidence.visuals.context(title) if self.evidence.visuals else [],
        }
        from .topic_evidence import topic_packets
        from .writing_limits import writing_limits
        context['writingLimits'] = writing_limits(title, diagnosis, self.request.instructions+' '+question)
        context['topicEvidence'] = topic_packets(self.evidence, title, focus_paths) if not issues else []
        context['focusPaths'] = focus_paths or []
        if focus_paths:
            context['templateTopics'] = [t for t in context['templateTopics'] if t['parent']+' / '+t['title'] in focus_paths]
            context['visualRequirements'] = [v for v in context['visualRequirements'] if v.get('subsection') in focus_paths]
            context['verifiedStatistics'] = []
            context['initialEvidence'] = []
            context['chapterRequirements'] += ' 本次只补写focusPaths列出的遗漏小节，不重写整章，不输出已完成主题；必须使用topicEvidence中对应统计和source_ids。每项1段即可。'
        if issues and draft:
            cited = dict.fromkeys(key for entry in [*draft.get("blocks", []), *draft.get("tables", [])]
                                  for key in entry.get("source_ids", []) if key in self.evidence.by_id)
            cited.update(dict.fromkeys(key for key in (repair_source_ids or []) if key in self.evidence.by_id))
            # Carry the original cited evidence into repair; do not make the model
            # guess from a fresh keyword search after closing the research tools.
            context["repairEvidence"] = [self.evidence.for_model(key) for key in list(cited)[:24]]
            source_numbers = {key: numeric_values(self.evidence.by_id[key].text) for key in list(cited)[:24]
                              if self.evidence.by_id[key].category != "REFERENCE"}
            hints = []
            for kind, entries in (("段", draft.get("blocks", [])), ("表格", draft.get("tables", []))):
                for index, entry in enumerate(entries, 1):
                    if kind == "段" and entry.get("kind") not in ("finding", "inference"):
                        continue
                    text = entry["text"] if kind == "段" else " ".join(cell for row in entry["rows"] for cell in row)
                    allowed = set().union(*(source_numbers.get(key, set()) for key in entry.get("source_ids", [])))
                    for value in sorted(numeric_values(text) - allowed - {self.request.year}):
                        matches = [key for key, values in source_numbers.items() if value in values]
                        hints.append({"scope": f"{kind}{index}", "value": str(value), "candidateSourceIds": matches,
                            "action": "核对同指标同口径后补充正确引用" if matches else "修订证据中未找到此数值，必须删除或改为定性描述"})
            context["numericRepairHints"] = hints
        prompt = ChatPromptTemplate.from_messages([
            ("human", (SERVICE_ROOT / "prompts/section.md").read_text(encoding="utf-8")),
        ]).invoke({"task_context": json.dumps(context, ensure_ascii=False)}).to_messages()
        if issues:
            prompt += ChatPromptTemplate.from_messages([
                ("human", (SERVICE_ROOT / "prompts/repair.md").read_text(encoding="utf-8")),
            ]).invoke({"issues": json.dumps(issues, ensure_ascii=False),
                       "draft": json.dumps(draft, ensure_ascii=False)}).to_messages()

        if self.analysis_model is not None or self.metadata.get('toolFree'):
            return self.thinking_write(title, system, context, issues, draft)

        research_rounds = 0
        format_repairs = 0
        section_calls = 0
        finalizing = bool(issues)
        signatures = {}
        pending_trace = None
        evidence_tools = self.evidence.tools()
        allowed_tool_names = {"SectionDraft", *(tool.name for tool in evidence_tools)}
        # Reserve enough graph steps for tool results and bounded format repairs.
        max_section_calls = MAX_RESEARCH_ROUNDS + MAX_FORMAT_REPAIRS + 1
        final_prompt = (
            "取证阶段已结束。现在只提交一次 SectionDraft 工具调用，完成当前章节；不得继续调用取证或图表工具。"
            "依据已经取得的证据写作，无法确认的指标写为 gap，不得编造数值或引用。"
            "prepared/rendered 图表由系统自动校验和附加，无需逐图检查。"
            "这些自动图表不填写 visuals，不得把 datasetId 当作 asset_id。引用ID只写 source_ids，不写入正文。"
            "避免重复抄录大量图表数据；title 必须逐字保持当前标题。"
            "blocks 的 kind 只能是 finding、inference、recommendation、gap；source_ids 为证据ID数组。"
        )
        final_prompt += ("本次只修订输入草稿列出的段落和表格。blocks、tables 数量和顺序必须与输入草稿一致，不扩写其他段落或图表。保留每个待修订段落的原主题；不得替换为本章其他主题或重复已通过段落，无依据的原主题改为定性说明或具体gap。"
                         if issues else "按 fixedOutline 和 templateTopics 小节成文，每个有证据的主题1–3段，使用 subsection 标明原小节路径。复杂章节最多96段。不能用题目介绍、工具过程或反复资料缺口代替实际统计分析。")
        if focus_paths:
            final_prompt += f'本次只补写focusPaths，每项最多2段，每段最多280字，blocks最多{len(focus_paths)*2}段，tables与visuals必须为空；subsection逐字使用focusPaths中的路径。'
        limits=context['writingLimits']
        if diagnosis and not issues:
            final_prompt += f'当前只写一个维度或专题，按writingLimits，最多{limits["maxBlocks"]}段，每段最多{limits["maxParagraphCharacters"]}字，tables最多{limits["maxTables"]}项。'
        block_schema=create_model('FocusedContentBlock',__base__=ContentBlock,
            subsection=(Literal[tuple(focus_paths)],Field(...)),text=(str,Field(min_length=1,max_length=280))) if focus_paths else create_model(
                'EditorialContentBlock',__base__=ContentBlock,text=(str,Field(min_length=1,max_length=limits['maxParagraphCharacters'])))
        output_schema = create_model("SectionDraft", __base__=SectionDraft,
            blocks=(list[block_schema], Field(min_length=1, max_length=min(96,len(focus_paths)*2) if focus_paths else limits['maxBlocks'])),
            tables=(list[ContentTable], Field(default_factory=list, max_length=0 if focus_paths else limits['maxTables'])),
            visuals=(list[ContentVisual],Field(default_factory=list,max_length=0 if focus_paths else 60)))
        if issues and draft:
            block_count, table_count = len(draft.get("blocks", [])), len(draft.get("tables", []))
            # Constrain the tool schema itself, so a changed revision scope is
            # repaired inside the bounded model loop before merging content.
            output_schema = create_model("SectionDraft", __base__=SectionDraft,
                blocks=(list[ContentBlock], Field(min_length=block_count, max_length=block_count)),
                tables=(list[ContentTable], Field(min_length=table_count, max_length=table_count)
                        if table_count else Field(default_factory=list, max_length=0)))
            final_prompt += f"本次 blocks 必须恰好 {block_count} 个，tables 必须恰好 {table_count} 个。"

        def repair_format(error):
            nonlocal format_repairs, finalizing
            format_repairs += 1
            finalizing = True
            if format_repairs > MAX_FORMAT_REPAIRS:
                raise AgentError("MODEL_STRUCTURE", f"{title} 的结构化格式在限定修订次数内仍未通过，请重试")
            fields = []
            source = error if isinstance(error, ValidationError) else getattr(error, "source", None)
            while source is not None and not isinstance(source, ValidationError):
                source = getattr(source, "__cause__", None)
            if isinstance(source, ValidationError):
                allowed = {"title", "blocks", "kind", "text", "summary", "source_ids", "subsection", "tables", "columns", "rows", "visuals", "asset_id", "template_id"}
                for entry in source.errors(include_input=False, include_context=False, include_url=False)[:8]:
                    path = ".".join(str(part) if isinstance(part, int) or part in allowed else "field" for part in entry["loc"])
                    fields.append(path + ": " + entry["type"])
            logger.warning("Section format repair: attempt=%s exception=%s", format_repairs, type(error).__name__)
            return "上次章节格式不合规，请按 Schema 修正，只提交一个 SectionDraft。" + (
                "需修正字段：" + "；".join(fields) if fields else "不要同时提交多份结构化结果。")

        @wrap_model_call
        def meter_call(model_request, handler):
            nonlocal research_rounds, section_calls, finalizing, pending_trace
            self.budget.check()
            if section_calls >= max_section_calls:
                raise AgentError("MODEL_STRUCTURE", f"{title} 未能在限定步骤内提交章节内容，请重试")
            if research_rounds >= MAX_RESEARCH_ROUNDS or any(count >= 2 for count in signatures.values()):
                finalizing = True
            phase = "finalize" if finalizing else "research"
            if finalizing:
                original_system = model_request.system_message
                system_text = original_system.content if original_system else system
                model_request = model_request.override(tools=[],
                    system_message=SystemMessage(content=str(system_text) + "\n\n" + final_prompt))
            else:
                research_rounds += 1
            section_calls += 1
            self.budget.calls += 1
            pending_trace = {'section': title, 'round': section_calls, 'phase': phase,
                             'tools': [], 'started': time.monotonic()}
            if finalizing:
                # ToolStrategy requests any tool, which some compatible providers
                # resolve to a historical evidence tool. Force the actual schema
                # by name and validate it here; never execute business tools now.
                bound = self.model.bind_tools([convert_to_openai_tool(output_schema)], tool_choice="SectionDraft")
                observations = []
                for message in model_request.messages:
                    if isinstance(message, ToolMessage):
                        try:
                            observations.append(json.loads(message.content))
                        except (TypeError, ValueError):
                            observations.append(str(message.content))
                # Fresh writing messages retain verified tool results, without
                # historical function calls inviting the provider to repeat them.
                final_messages = [model_request.system_message,
                                  *(message for message in model_request.messages if isinstance(message, HumanMessage))]
                if observations:
                    final_messages.append(HumanMessage(content="已取得的证据与统计结果如下，仅供当前章节引用：\n" +
                        json.dumps(observations, ensure_ascii=False)))
                output = bound.invoke(final_messages)
                calls = output.tool_calls or []
                if len(calls) == 1 and calls[0]["name"] == "SectionDraft" and not output.invalid_tool_calls:
                    call = calls[0]
                    try:
                        parsed = output_schema.model_validate(call["args"])
                    except ValidationError as error:
                        feedback = repair_format(error)
                        response = ModelResponse(result=[output, ToolMessage(content=feedback,
                            tool_call_id=call["id"], name="SectionDraft")])
                    else:
                        response = ModelResponse(result=[output, ToolMessage(content="章节结构化内容已接收",
                            tool_call_id=call["id"], name="SectionDraft")], structured_response=parsed)
                else:
                    # Finish this graph invocation without executing stale or
                    # malformed calls. The bounded finalization retry below will
                    # ask for a fresh schema result.
                    response = ModelResponse(result=[output.model_copy(update={"tool_calls": [], "invalid_tool_calls": []})])
            else:
                response = handler(model_request)
            self.budget.check_cancelled()
            tool_names = []
            finish_reasons = []
            for message in response.result:
                usage = getattr(message, "usage_metadata", None) or {}
                self.budget.input_tokens += usage.get("input_tokens", 0)
                self.budget.output_tokens += usage.get("output_tokens", 0)
                reason = (getattr(message, "response_metadata", None) or {}).get("finish_reason", "")
                if reason in {"stop", "tool_calls", "length", "content_filter"}:
                    finish_reasons.append(reason)
                if reason == "length":
                    finalizing = True
                for call in getattr(message, "tool_calls", []) or []:
                    name = call["name"]
                    if name == "SectionDraft":
                        finalizing = True
                    if name in allowed_tool_names:
                        tool_names.append(name)
                    signature = name + "|" + json.dumps(call.get("args", {}), ensure_ascii=False, sort_keys=True)
                    signatures[signature] = signatures.get(signature, 0) + 1
            trace = {"section": title, "round": section_calls, "phase": phase,
                     "tools": tool_names, "finishReasons": finish_reasons,
                     'status': 'COMPLETED', 'elapsedSeconds': round(time.monotonic() - pending_trace['started'], 2)}
            self.call_trace.append(trace)
            pending_trace = None
            logger.info("Section model call: round=%s phase=%s tools=%s finish=%s",
                           section_calls, phase, ",".join(tool_names), ",".join(finish_reasons))
            return response

        try:
            agent = create_agent(model=self.model, tools=evidence_tools, system_prompt=system,
                                 response_format=ToolStrategy(output_schema, handle_errors=repair_format,
                                                             tool_message_content="章节结构化内容已接收"),
                                 middleware=[meter_call])
            with tracing_context(enabled=False):
                for missing_retry in range(MAX_FORMAT_REPAIRS + 1):
                    result = agent.invoke({"messages": prompt}, config={"recursion_limit": max_section_calls * 2 + 8})
                    response = result.get("structured_response")
                    if isinstance(response, SectionDraft):
                        return response
                    finalizing = True
                    # Incomplete function arguments must not become an unmatched tool call
                    # in the next provider request. No invalid call has been executed.
                    prompt = [message.model_copy(update={"invalid_tool_calls": []})
                              if getattr(message, "invalid_tool_calls", None) else message
                              for message in result.get("messages", prompt)]
                    prompt.append(HumanMessage(content="上一回复没有形成可用的结构化结果。请缩短内容，并只调用一次 SectionDraft 提交当前章节。"))
            raise AgentError("MODEL_STRUCTURE", f"{title} 在限定修订次数内未返回有效的结构化章节，请重试")
        except AgentError as error:
            if pending_trace:
                trace = {**pending_trace, 'status': 'FAILED', 'usageStatus': 'unknown', 'errorCode': error.code}
                trace['elapsedSeconds'] = round(time.monotonic() - trace.pop('started'), 2)
                self.call_trace.append(trace)
            raise
        except Exception as exc:
            error = safe_model_error(exc)
            if pending_trace:
                trace = {**pending_trace, 'status': 'FAILED', 'usageStatus': 'unknown', 'errorCode': error.code}
                trace['elapsedSeconds'] = round(time.monotonic() - trace.pop('started'), 2)
                self.call_trace.append(trace)
            raise error from exc

    def thinking_write(self, title, system, context, issues, draft):
        """Analyze verified data, then compose JSON in fresh tool-free requests."""
        context = {**context, 'revisionIssues': issues or [], 'revisionDraft': draft}
        if issues and draft:
            context = {**context, 'fixedOutline': [], 'templateTopics': [], 'visualRequirements': [],
                       'verifiedStatistics': [], 'verifiedSectionSummaries': [],
                       'chapterRequirements': '仅修订revisionDraft的内容，不扩写整章。'}
            system += (f"\n本次是局部修订，忽略整章主题覆盖要求。只返回revisionDraft中的{len(draft['blocks'])}个blocks和"
                       f"{len(draft.get('tables', []))}个tables，保持顺序、原主题与subsection路径。"
                       '没有fixedOutline不表示清空原subsection；不得补写其他主题或整章小结。')
        # Expand source coverage using the same registered evidence search implementation.
        evidence = {item['id']: item for item in context['initialEvidence']}
        # Topic-specific packets preserve every supported metric. Broad searches
        # must not crowd later topics out of a first-60 evidence slice.
        if not context.get('focusPaths'):
            for group in context['fixedOutline']:
                for item in self.evidence.search(group['title'], category='SPEECH', limit=1):
                    evidence[item['id']] = item
        for summary in context['verifiedSectionSummaries']:
            for block in summary.get('blocks', []):
                for key in block.get('source_ids', []):
                    if key in self.evidence.by_id:
                        evidence.setdefault(key, self.evidence.for_model(key))
        context['initialEvidence'] = list(evidence.values())[:24]
        context['evidenceCoverage'] = {'matchedCount': len(evidence), 'includedCount': len(context['initialEvidence'])}
        focus=context.get('focusPaths')
        limits=context.get('writingLimits',{'maxBlocks':96,'maxParagraphCharacters':600,'maxTables':12})
        block_schema=create_model('FocusedContentBlock',__base__=ContentBlock,
            subsection=(Literal[tuple(focus)],Field(...)),text=(str,Field(min_length=1,max_length=280))) if focus else create_model(
                'EditorialContentBlock',__base__=ContentBlock,text=(str,Field(min_length=1,max_length=limits['maxParagraphCharacters'])))
        output_schema = create_model('SectionDraft', __base__=SectionDraft,
            blocks=(list[block_schema], Field(min_length=1, max_length=min(96,len(focus)*2) if focus else limits['maxBlocks'])),
            tables=(list[ContentTable], Field(default_factory=list, max_length=0 if focus else limits['maxTables'])),
            visuals=(list[ContentVisual],Field(default_factory=list,max_length=0 if focus else 60)))
        if issues and draft:
            output_schema = create_model('SectionDraft', __base__=SectionDraft,
                blocks=(list[ContentBlock], Field(min_length=len(draft['blocks']), max_length=len(draft['blocks']))),
                tables=(list[ContentTable], Field(default_factory=list, min_length=len(draft.get('tables', [])), max_length=len(draft.get('tables', [])))))
        analysis_prompt = (SERVICE_ROOT / 'prompts/analysis.md').read_text(encoding='utf-8')
        try:
            with tracing_context(enabled=False):
                memo = ''
                if self.analysis_model is not None:
                    try:
                        analysis = self.measured_invoke(self.analysis_model,
                            [SystemMessage(content=system + '\n' + analysis_prompt),
                             HumanMessage(content=json.dumps(context, ensure_ascii=False))], title, 'analysis')
                        memo = answer_text(analysis).strip()
                    except AgentError as error:
                        if error.code != 'MODEL_UNAVAILABLE':
                            raise
                        # Analysis is an aid; composition also receives the full
                        # verified evidence. Do not repeat a failed paid analysis.
                        self.call_trace[-1]['analysisFallback'] = 'verified_evidence'
                    if not memo and 'analysisFallback' not in self.call_trace[-1]:
                        # Some thinking providers occasionally return reasoning
                        # usage without public answer text. Compose from verified
                        # evidence anyway; private reasoning is never substituted.
                        self.call_trace[-1]['analysisAnswerMissing'] = True
                compose = (SERVICE_ROOT / 'prompts/compose.md').read_text(encoding='utf-8')
                base = [SystemMessage(content=system + '\n' + compose),
                        HumanMessage(content=json.dumps(context, ensure_ascii=False)),
                        HumanMessage(content='本章分析建议（仍须核对引用与数值）：\n' + memo[:24000]),
                        HumanMessage(content='输出 JSON Schema：\n' + json.dumps(output_schema.model_json_schema(), ensure_ascii=False))]
                feedback = []
                for attempt in range(MAX_FORMAT_REPAIRS + 1):
                    response = self.measured_invoke(self.model, base + feedback, title, 'compose' if not attempt else 'format_repair')
                    text = answer_text(response).strip()
                    if text.startswith('```'):
                        text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
                    try:
                        result = output_schema.model_validate_json(text)
                        if result.title != title or len(result.blocks) > 96 or len(result.tables) > 12:
                            raise ValueError('scope')
                        return result
                    except (ValidationError, ValueError) as error:
                        # No prior assistant message is replayed, so provider reasoning
                        # history and Gemini signatures cannot be dropped between turns.
                        fields = []
                        if isinstance(error, ValidationError):
                            allowed = {'title','blocks','tables','kind','text','summary','source_ids','subsection','visuals','columns','rows'}
                            for item in error.errors(include_input=False,include_context=False,include_url=False)[:8]:
                                fields.append({'field':'.'.join(str(k) if isinstance(k,int) or k in allowed else 'field' for k in item['loc']),
                                               'type':item['type']})
                        self.call_trace[-1]['formatErrors'] = fields or [{'field':'scope','type':'invalid'}]
                        counts = (f"blocks恰好{len(draft['blocks'])}段、tables恰好{len(draft.get('tables', []))}项。" if issues and draft
                                  else f'blocks最多{len(focus)*2}段，每段最多280字，subsection必须属于focusPaths，tables与visuals必须为空。' if focus
                                  else f'blocks最多{limits["maxBlocks"]}段，每段最多{limits["maxParagraphCharacters"]}字，tables最多{limits["maxTables"]}项。')
                        feedback = [HumanMessage(content='上次未返回合规JSON。需修正字段：'+json.dumps(fields,ensure_ascii=False)+
                            '；请严格遵循Schema，保持标题，'+counts+'仅返回JSON对象，不加Markdown。')]
                raise AgentError('MODEL_STRUCTURE', f'{title} 未返回合规的结构化章节，请重试')
        except AgentError:
            raise
        except Exception as exc:
            raise safe_model_error(exc) from exc

    def measured_invoke(self, model, messages, title, phase):
        self.budget.check_cancelled()
        # Retain completed public analysis across a failed composition. Malformed
        # or unvalidated chapter JSON must not be replayed on the next attempt.
        cache = self.cache if phase == 'analysis' else None
        cache_key = cache.key({'title': title, 'phase': phase,
            'messages': [{'type': m.type, 'content': m.content} for m in messages]}) if cache else None
        cached = cache.load('response', cache_key) if cache else None
        if isinstance(cached, dict) and isinstance(cached.get('answer'), str):
            self.call_trace.append({'section': title, 'phase': phase, 'tools': [], 'cacheHit': True,
                                    'elapsedSeconds': 0, 'usageStatus': 'reused'})
            return AIMessage(content=cached['answer'], response_metadata=cached.get('metadata', {}))
        self.budget.check()
        self.budget.calls += 1
        started = time.monotonic()
        trace = {'section': title, 'phase': phase, 'tools': [], 'status': 'FAILED', 'usageStatus': 'unknown'}
        try:
            response = model.invoke(messages)
        except Exception as error:
            failure = error if isinstance(error, AgentError) else safe_model_error(error)
            trace['errorCode'] = failure.code
            if getattr(failure, 'diagnostics', None):
                trace['errorDetails'] = failure.diagnostics
            if isinstance(error, AgentError):
                raise
            raise failure from error
        finally:
            trace['elapsedSeconds'] = round(time.monotonic() - started, 2)
            self.call_trace.append(trace)
        usage = response.usage_metadata or {}
        trace.update(status='COMPLETED', usageStatus='reported' if usage else 'not_returned')
        self.budget.input_tokens += usage.get('input_tokens', 0)
        self.budget.output_tokens += usage.get('output_tokens', 0)
        self.call_trace[-1].update({'inputTokens': usage.get('input_tokens', 0),
            'outputTokens': usage.get('output_tokens', 0),
            'reasoningTokens': usage.get('output_token_details', {}).get('reasoning', 0)})
        self.call_trace[-1]['reasoningObserved'] = bool(response.response_metadata.get('reasoningObserved') or
            usage.get('output_token_details', {}).get('reasoning', 0) or
            (isinstance(response.content, list) and any(isinstance(b, dict) and b.get('type') in ('thinking','reasoning') for b in response.content)))
        finish = response.response_metadata.get('finish_reason') or response.response_metadata.get('stop_reason')
        if finish in ('stop','length','max_tokens','end_turn','tool_use'):
            self.call_trace[-1]['finishReason'] = finish
        self.budget.check_cancelled()
        if cache:
            # Retain only the public answer and allowlisted usage metadata.
            # Private thinking, signatures, credentials and raw responses stay out.
            cache.save('response', cache_key, {'answer': answer_text(response),
                'metadata': {k: response.response_metadata[k] for k in ('finish_reason', 'stop_reason', 'reasoningObserved')
                             if k in response.response_metadata}})
        return response
