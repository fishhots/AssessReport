"""Bound relay waits, cancellation and failures without paid requests or sleeps."""
import asyncio
import json
import time

import pytest
from langchain_core.messages import HumanMessage

from assess_agent.checkpoints import RetryCache
from assess_agent.errors import AgentError
from assess_agent.evidence import EvidenceStore
from assess_agent.models import SectionWriter, build_model
from assess_agent.parsers import parse_job
from assess_agent.reasoning import answer_text
from relay_transport import attach_client, response_events, text_delta, transport_for
from test_agent import setup_task

MODELS = {'gpt': 'gpt-6.1-sol', 'claude': 'claude-sonnet-5-5', 'gemini': 'gemini-3.8-flash'}


def configuration(provider, channel='zhizengzeng'):
    return {'key': 'offline-test-secret', 'model': MODELS[provider], 'base_url': 'https://example.invalid',
            'channel': channel, 'extra_body': {}, 'reasoningMode': 'auto'}


@pytest.mark.parametrize('provider', list(MODELS))
@pytest.mark.parametrize('stage', ['tools', 'analysis', 'structured', 'format_repair', 'test', 'analysis-test'])
def test_all_relay_stages_use_separate_wait_limits_and_never_silently_retry(tmp_path, provider, stage):
    settings, _ = setup_task(tmp_path)
    model, metadata = build_model(settings, provider, max_retries=3, stage=stage,
                                  provider_config=configuration(provider))
    testing = stage in ('test', 'analysis-test')
    seconds = 60 if testing else 600
    timeout = model._client.timeout if provider == 'claude' else model.request_timeout
    assert timeout.read == seconds and timeout.connect == (10 if testing else 30)
    assert model.streaming is (not testing) and model.max_retries == 0
    assert metadata['relayTimeoutSeconds'] == seconds
    if provider == 'claude':
        assert model._client.timeout.read == seconds
        assert model._async_client.timeout.connect == timeout.connect


@pytest.mark.parametrize('provider', list(MODELS))
@pytest.mark.parametrize('stage,seconds', [('analysis', 180), ('structured', 90), ('test', 90)])
def test_official_model_waits_and_transport_remain_unchanged(tmp_path, provider, stage, seconds):
    settings, _ = setup_task(tmp_path)
    model, metadata = build_model(settings, provider, max_retries=3, stage=stage,
                                  provider_config=configuration(provider, 'official'))
    timeout = model.default_request_timeout if provider == 'claude' else model.request_timeout
    assert timeout == seconds and not model.streaming and model.max_retries == 3
    assert 'relayTimeoutSeconds' not in metadata
    if provider == 'gpt':
        assert model.use_responses_api is True


@pytest.mark.parametrize('provider', list(MODELS))
def test_relay_wait_limits_are_configurable(tmp_path, provider):
    settings, _ = setup_task(tmp_path)
    settings.relay_model_timeout = 900
    settings.relay_test_timeout = 75
    for stage, seconds in [('analysis', 900), ('structured', 900), ('analysis-test', 75)]:
        model, _ = build_model(settings, provider, stage=stage, provider_config=configuration(provider))
        timeout = model._client.timeout if provider == 'claude' else model.request_timeout
        assert timeout.read == seconds


@pytest.mark.parametrize('provider', list(MODELS))
@pytest.mark.parametrize('fault,code', [(None, None), ('past_old_limit', None), ('timeout', 'MODEL_TIMEOUT'),
    ('cancel', 'JOB_CANCELLED'), ('deadline', 'JOB_TIMEOUT'), ('call_limit', 'MODEL_TIMEOUT')])
def test_real_relay_streams_report_usage_and_close_without_reissuing_requests(
        tmp_path, monkeypatch, caplog, provider, fault, code):
    import assess_agent.models as module
    settings, request = setup_task(tmp_path)
    request.model_id = provider
    transport_api = transport_for(provider)
    streams, requests = [], []
    writer = None

    class RelayStream(transport_api.SyncByteStream):
        closed = False

        def __iter__(self):
            for event in response_events(provider, writer.analysis_model):
                yield event
                if b'READY' not in event or fault in (None, 'past_old_limit'):
                    continue
                if fault == 'timeout':
                    raise transport_api.ReadTimeout('private-key-and-school-data')
                if fault == 'cancel':
                    writer.budget.cancel_check = lambda: (_ for _ in ()).throw(AgentError('JOB_CANCELLED', '任务已终止'))
                elif fault == 'deadline':
                    writer.budget.deadline = time.monotonic() - 1
                elif fault == 'call_limit':
                    writer.analysis_model.callbacks[-1].started = time.monotonic() - settings.relay_model_timeout - 1
                yield text_delta(provider, 'SHOULD_NOT_BE_RETURNED')

        def close(self):
            self.closed = True

    def respond(req):
        requests.append(req)
        assert req.extensions['timeout']['read'] == 600
        assert json.loads(req.content)['stream'] is True
        if fault == 'past_old_limit':
            writer.analysis_model.callbacks[-1].started -= 120
        stream = RelayStream()
        streams.append(stream)
        return transport_api.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)

    factory = build_model
    with transport_api.Client(transport=transport_api.MockTransport(respond)) as transport:
        def build(*args, **kwargs):
            model, metadata = factory(*args, **kwargs)
            attach_client(model, provider, transport)
            return model, metadata
        monkeypatch.setattr(module, 'build_model', build)
        writer = SectionWriter(settings, request, EvidenceStore(parse_job(request, settings)),
                               provider_config=configuration(provider))
        writer.cache = RetryCache(settings.jobs_root() / str(request.task_id) / 'attempt-1', 'offline-test')
        if code:
            with pytest.raises(AgentError) as failure:
                writer.measured_invoke(writer.analysis_model, [HumanMessage(content='test')], request.chapters[0], 'analysis')
            assert failure.value.code == code
            assert writer.call_trace[-1]['usageStatus'] == 'unknown'
            assert not list((writer.cache.directory / 'checkpoints').glob('response-*.json'))
        else:
            answer = writer.measured_invoke(writer.analysis_model, [HumanMessage(content='test')], request.chapters[0], 'analysis')
            assert answer_text(answer) == 'READY'
            assert writer.budget.input_tokens == 10 and writer.budget.output_tokens == 2
            assert writer.call_trace[-1]['usageStatus'] == 'reported'
        assert writer.budget.calls == 1 and len(requests) == 1 and streams[0].closed
        assert 'private-key' not in caplog.text + json.dumps(writer.call_trace)


@pytest.mark.parametrize('delta_usage,expected_input', [({}, 20), ({'input_tokens': 4}, 14),
    ({'input_tokens': 12, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0,
      'cache_creation': {'ephemeral_5m_input_tokens': 0, 'ephemeral_1h_input_tokens': 0}}, 12),
    ({'input_tokens': 0, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0,
      'cache_creation': {'ephemeral_5m_input_tokens': 0, 'ephemeral_1h_input_tokens': 0}}, 0)])
def test_claude_usage_preserves_start_cache_tokens_and_explicit_delta_values(tmp_path, delta_usage, expected_input):
    settings, _ = setup_task(tmp_path)
    model, _ = build_model(settings, 'claude', stage='analysis', provider_config=configuration('claude'))
    api = transport_for('claude')

    def respond(request):
        events = []
        for event in response_events('claude', model):
            body = json.loads(event.decode().split('data: ', 1)[1])
            if body['type'] == 'message_start':
                body['message']['usage'].update(cache_read_input_tokens=3, cache_creation_input_tokens=7,
                    cache_creation={'ephemeral_5m_input_tokens': 7, 'ephemeral_1h_input_tokens': 0})
            elif body['type'] == 'message_delta':
                body['usage'].update(delta_usage)
            events.append(f'event: {body["type"]}\ndata: {json.dumps(body)}\n\n'.encode())
        return api.Response(200, headers={'content-type': 'text/event-stream'}, content=b''.join(events))

    with api.Client(transport=api.MockTransport(respond)) as transport:
        attach_client(model, 'claude', transport)
        answer = model.invoke([HumanMessage(content='test')])
    assert answer.usage_metadata['input_tokens'] == expected_input
    assert answer.usage_metadata['output_tokens'] == 2


def test_claude_async_relay_stream_closes_and_reports_start_usage(tmp_path):
    import anthropic
    settings, _ = setup_task(tmp_path)
    model, _ = build_model(settings, 'claude', stage='analysis', provider_config=configuration('claude'))
    api = transport_for('claude')
    streams = []

    class AsyncRelayStream(api.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            for event in response_events('claude', model):
                yield event

        async def aclose(self):
            self.closed = True

    def respond(request):
        assert request.extensions['timeout']['read'] == 600
        stream = AsyncRelayStream()
        streams.append(stream)
        return api.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)

    async def invoke():
        async with api.AsyncClient(transport=api.MockTransport(respond)) as transport:
            model._async_client = anthropic.AsyncAnthropic(api_key='offline-test-secret',
                base_url='https://example.invalid', timeout=model._async_client.timeout,
                http_client=transport, max_retries=0)
            return await model.ainvoke([HumanMessage(content='test')])

    answer = asyncio.run(invoke())
    assert answer_text(answer) == 'READY' and streams[0].closed
    assert answer.usage_metadata['input_tokens'] == 10


@pytest.mark.parametrize('provider', list(MODELS))
@pytest.mark.parametrize('detail,expected', [
    ({'code': 500, 'status': 'INTERNAL', 'message': 'private-key-and-school-data'}, 'MODEL_UNAVAILABLE'),
    ({'code': 'private-key-and-school-data', 'message': 'private-key-and-school-data'}, 'MODEL_STREAM_ERROR'),
])
def test_provider_sse_error_after_partial_output_preserves_diagnostics_without_paid_replay(
        tmp_path, monkeypatch, caplog, provider, detail, expected):
    import assess_agent.models as module
    settings, request = setup_task(tmp_path)
    request.model_id = provider
    api = transport_for(provider)
    streams, requests = [], []
    writer = None

    class ErrorStream(api.SyncByteStream):
        closed = False

        def __iter__(self):
            for event in response_events(provider, writer.analysis_model):
                yield event
                if b'READY' in event:
                    break
            body = {'type': 'error', 'error': detail}
            yield (('event: error\n' if provider == 'claude' else '') + 'data: ' + json.dumps(body) + '\n\n').encode()

        def close(self):
            self.closed = True

    def respond(req):
        requests.append(req)
        stream = ErrorStream()
        streams.append(stream)
        return api.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)

    factory = build_model
    with api.Client(transport=api.MockTransport(respond)) as transport:
        def build(*args, **kwargs):
            model, metadata = factory(*args, **kwargs)
            attach_client(model, provider, transport)
            return model, metadata
        monkeypatch.setattr(module, 'build_model', build)
        writer = SectionWriter(settings, request, EvidenceStore(parse_job(request, settings)),
                               provider_config=configuration(provider))
        writer.cache = RetryCache(settings.jobs_root() / str(request.task_id) / 'attempt-1', 'offline-test')
        with pytest.raises(AgentError) as failure:
            writer.measured_invoke(writer.analysis_model, [HumanMessage(content='test')], request.chapters[0], 'analysis')
    assert failure.value.code == expected and streams[0].closed and len(requests) == 1
    trace = writer.call_trace[-1]
    assert trace['errorDetails']['source'] == 'stream_event' and trace['errorCode'] == expected
    assert trace['usageStatus'] == 'unknown' and writer.budget.calls == 1
    assert not list((writer.cache.directory / 'checkpoints').glob('response-*.json'))
    assert 'private-key' not in caplog.text + json.dumps(trace) and 'school-data' not in caplog.text + json.dumps(trace)
