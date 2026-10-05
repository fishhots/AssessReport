"""Exercise the real SDK stream and retry path without paid model requests."""
import json
import time

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from assess_agent.checkpoints import RetryCache, workflow_identity, section_payload, restore_section
from assess_agent.errors import AgentError
from assess_agent.evidence import EvidenceStore
from assess_agent.model_config import ModelConfigInput, ModelConfigStore
from assess_agent.models import SectionWriter, build_model
from assess_agent.parsers import parse_job
from assess_agent.schemas import SectionDraft
from assess_agent.settings import CHAPTERS
from assess_agent.store import JobStore
from assess_agent.workflow import run_workflow
from fake_model import ToolCallingTestModel
from test_agent import setup_task


class ScriptedStream(httpx.SyncByteStream):
    def __init__(self, writer, fault=None):
        self.writer, self.fault, self.closed = writer, fault, False

    def __iter__(self):
        body = {'id': 'offline', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'qwen3.8-max'}
        yield ('data: ' + json.dumps({**body, 'choices': [{'index': 0, 'delta': {'role': 'assistant',
            'content': 'READY', 'reasoning_content': 'private-reasoning-do-not-save'}, 'finish_reason': None}]}) + '\n\n').encode()
        if self.fault == 'timeout':
            raise httpx.ReadTimeout('private-key-and-school-data')
        if self.fault == 'cancel':
            self.writer.budget.cancel_check = lambda: (_ for _ in ()).throw(AgentError('JOB_CANCELLED', '任务已终止'))
        if self.fault == 'deadline':
            self.writer.budget.deadline = time.monotonic() - 1
        yield ('data: ' + json.dumps({**body, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}) + '\n\n').encode()
        yield ('data: ' + json.dumps({**body, 'choices': [], 'usage': {'prompt_tokens': 11,
            'completion_tokens': 23, 'total_tokens': 34, 'completion_tokens_details': {'reasoning_tokens': 7}}}) + '\n\n').encode()
        yield b'data: [DONE]\n\n'

    def close(self):
        self.closed = True


@pytest.mark.parametrize('fault,code', [(None, None), ('timeout', 'MODEL_TIMEOUT'),
                                      ('cancel', 'JOB_CANCELLED'), ('deadline', 'JOB_TIMEOUT')])
def test_qwen_streams_composition_tracks_usage_and_closes_on_failure(tmp_path, monkeypatch, fault, code, caplog):
    import assess_agent.models as module
    settings, request = setup_task(tmp_path)
    ModelConfigStore(settings.data_root).save('qwen', ModelConfigInput(model='qwen3.8-max',
        baseUrl='https://example.invalid', apiKey='test-only-secret', revision=0, reasoningMode='off'))
    requests, streams = [], []
    writer = None

    def respond(req):
        body = json.loads(req.content)
        requests.append(body)
        assert body['stream'] and body['stream_options'] == {'include_usage': True}
        assert body['enable_thinking'] is False
        stream = ScriptedStream(writer, fault)
        streams.append(stream)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)

    factory = build_model
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        def build(*args, **kwargs):
            client, meta = factory(*args, **kwargs)
            client.client = openai.OpenAI(api_key='test-only-secret', base_url='https://example.invalid',
                http_client=transport, max_retries=0).chat.completions
            return client, meta
        monkeypatch.setattr(module, 'build_model', build)
        writer = SectionWriter(settings, request, EvidenceStore(parse_job(request, settings)))
        assert writer.model.request_timeout.read == 180
        assert writer.model.request_timeout.connect == 30
        assert writer.model.max_retries == 0
        if code:
            with pytest.raises(AgentError) as failure:
                writer.measured_invoke(writer.model, [HumanMessage(content='test')], CHAPTERS[1], 'compose')
            assert failure.value.code == code
            assert writer.call_trace[-1]['usageStatus'] == 'unknown'
            assert writer.call_trace[-1]['errorCode'] == code
        else:
            answer = writer.measured_invoke(writer.model, [HumanMessage(content='test')], CHAPTERS[1], 'compose')
            assert answer.content == 'READY'
            assert writer.budget.input_tokens == 11 and writer.budget.output_tokens == 23
            assert writer.call_trace[-1]['reasoningTokens'] == 7
            assert writer.call_trace[-1]['reasoningObserved']
        assert len(requests) == 1 and streams[0].closed
        assert 'private-key' not in caplog.text + json.dumps(writer.call_trace)
        assert 'private-reasoning' not in json.dumps(writer.call_trace)


def test_qwen_connection_test_keeps_short_timeout(tmp_path):
    settings, _ = setup_task(tmp_path)
    ModelConfigStore(settings.data_root).save('qwen', ModelConfigInput(model='qwen3.8-max',
        baseUrl='https://example.invalid', apiKey='test-only-secret', revision=0))
    client, _ = build_model(settings.model_copy(update={'model_timeout': 30}), 'qwen', stage='test')
    assert client.request_timeout == 30 and client.streaming and client.max_retries == 0


def test_completed_chapter_is_not_regenerated_after_later_timeout(tmp_path, monkeypatch):
    settings, request = setup_task(tmp_path, CHAPTERS[1:3])
    writes, failed = [], False

    def write(self, title, *args, **kwargs):
        nonlocal failed
        writes.append(title)
        if title == CHAPTERS[2] and not failed:
            failed = True
            raise AgentError('MODEL_TIMEOUT', '测试超时')
        key = self.evidence.search('就业')[0]['id']
        return SectionDraft(title=title, blocks=[{'kind': 'finding', 'text': '就业人数80人。', 'source_ids': [key]}])

    monkeypatch.setattr(SectionWriter, 'write', write)
    jobs = JobStore(settings.data_root)
    jobs.create(request)
    with pytest.raises(AgentError) as failure:
        run_workflow(request, settings, jobs, 1, model=ToolCallingTestModel())
    jobs.fail(str(request.task_id), 1, failure.value)
    jobs.retry(str(request.task_id))
    result = run_workflow(request, settings, jobs, 2, model=ToolCallingTestModel())
    assert writes.count(CHAPTERS[1]) == 1
    assert writes.count(CHAPTERS[2]) == 2
    assert result['resume']['sections'] == [CHAPTERS[1]]
    assert (settings.jobs_root() / str(request.task_id) / 'attempt-2' / 'report.docx').exists()


def test_completed_analysis_is_reused_without_counting_a_new_paid_call(tmp_path):
    settings, request = setup_task(tmp_path)
    messages = [HumanMessage(content='本章分析')]
    evidence = EvidenceStore(parse_job(request, settings))
    identity = workflow_identity(request, evidence.corpus, settings, {'model': 'qwen-test', 'key': 'secret'})
    class Analyzer:
        def __init__(self): self.calls = 0
        def invoke(self, messages):
            self.calls += 1
            return AIMessage(content='公开的分析结果', additional_kwargs={'reasoning_content': 'private-reasoning'},
                usage_metadata={'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30})
    model = Analyzer()
    root = settings.jobs_root() / str(request.task_id)
    for attempt in (1, 2):
        writer = SectionWriter(settings, request, evidence, model=model)
        writer.cache = RetryCache(root / f'attempt-{attempt}', identity)
        answer = writer.measured_invoke(model, messages, request.chapters[0], 'analysis')
        assert answer.content == '公开的分析结果'
    assert model.calls == 1 and writer.budget.calls == 0 and writer.budget.input_tokens == 0
    assert writer.call_trace[-1]['cacheHit']
    for path in root.rglob('*.json'):
        assert 'private-reasoning' not in path.read_text(encoding='utf-8')


def test_cache_rejects_different_model_inputs_rules_or_corrupt_files(tmp_path):
    settings, request = setup_task(tmp_path)
    corpus = parse_job(request, settings)
    identity = workflow_identity(request, corpus, settings, {'model': 'qwen-test', 'key': 'secret'})
    assert identity == workflow_identity(request, corpus, settings, {'model': 'qwen-test', 'key': 'new-secret'})
    assert identity != workflow_identity(request, corpus, settings, {'model': 'another-model', 'key': 'secret'})
    assert identity != workflow_identity(request.model_copy(update={'instructions': '新的要求'}), corpus, settings, {'model': 'qwen-test'})
    changed = corpus.model_copy(deep=True)
    changed.evidence[0].text += '新增资料'
    assert identity != workflow_identity(request, changed, settings, {'model': 'qwen-test', 'key': 'secret'})
    cache = RetryCache(settings.jobs_root() / str(request.task_id) / 'attempt-1', identity)
    key = cache.key({'analysis': 'test'})
    cache.save('response', key, {'answer': 'old'})
    assert RetryCache(cache.directory.parent / 'attempt-2', 'changed-rules').load('response', key) is None
    (cache.directory / 'checkpoints' / f'response-{key}.json').write_text('{broken', encoding='utf-8')
    assert cache.load('response', key) is None


def test_resumed_visuals_are_rebuilt_in_current_attempt(tmp_path):
    from test_visuals import registry, spec
    from assess_agent.visuals import VisualRegistry
    charts, table = registry(tmp_path / 'attempt-1', ['学院名称', '专业相关度（%）'], [['甲学院', '80'], ['乙学院', '60']])
    data = charts.prepare(spec(table))
    asset = charts.render(data['datasetId'])
    draft = SectionDraft(title=CHAPTERS[2], blocks=[{'kind': 'finding', 'text': '甲学院专业相关度80%。',
        'source_ids': [data['datasetId']]}], visuals=[{'asset_id': asset['assetId'], 'template_id': asset['templateId']}])
    saved = section_payload(draft, charts.evidence)
    raw = charts.evidence.corpus.model_copy(deep=True)
    raw.evidence = [e for e in raw.evidence if not e.computed]
    current = EvidenceStore(raw)
    current.visuals = VisualRegistry(current, tmp_path / 'attempt-2' / 'visuals', 2025, CHAPTERS)
    resumed = restore_section(saved, current, draft.title, 2025)
    assert resumed and resumed.blocks[0] == draft.blocks[0]
    files = current.visuals.assets[resumed.visuals[0].asset_id]['files']
    assert files and all((current.visuals.directory / f['filename']).exists() for f in files)
