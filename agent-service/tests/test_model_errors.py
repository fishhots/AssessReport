from importlib import import_module
import json
import logging

from fastapi.testclient import TestClient
import httpx
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError
import pytest

from assess_agent.app import create_app
from assess_agent.errors import QUOTA_CODES, safe_model_error
from assess_agent.settings import Settings


def provider_error(status, detail):
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    return APIStatusError("raw-provider-message-with-test-secret-and-school-data",
                          response=httpx.Response(status, request=request), body=detail)


@pytest.mark.parametrize("detail", [
    *({"code": code} for code in QUOTA_CODES),
    {"type": "insufficient_quota"},
    {"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted"}},
])
def test_quota_is_not_retryable_rate_limit_and_logs_no_raw_data(detail, caplog):
    with caplog.at_level(logging.WARNING):
        failure = safe_model_error(provider_error(429, detail))
    assert failure.code == "MODEL_QUOTA"
    assert "稍后重试" not in failure.message
    assert "test-secret" not in caplog.text + failure.message
    assert "school-data" not in caplog.text + failure.message


@pytest.mark.parametrize("status,detail,expected", [
    (429, {"code": "rate_limit_exceeded"}, "MODEL_RATE_LIMIT"),
    (429, {"code": {"unexpected": "malformed"}}, "MODEL_RATE_LIMIT"),
    (400, {"code": "invalid_request_error"}, "MODEL_BAD_REQUEST"),
    (404, {"code": "model_not_found"}, "MODEL_NOT_FOUND"),
    (401, {}, "MODEL_AUTH"),
    (403, {}, "MODEL_AUTH"),
    (503, {"code": "server_is_overloaded"}, "MODEL_UNAVAILABLE"),
])
def test_provider_http_errors_are_classified_without_returning_response(status, detail, expected):
    detail["message"] = "private-key-and-school-data"
    failure = safe_model_error(provider_error(status, detail))
    assert failure.code == expected
    assert "private-key" not in failure.message


def test_network_and_timeout_are_distinct():
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    assert safe_model_error(APIConnectionError(request=request)).code == "MODEL_CONNECTION"
    assert safe_model_error(APITimeoutError(request=request)).code == "MODEL_TIMEOUT"
    assert safe_model_error(RuntimeError("private-key-and-school-data")).code == "MODEL_ERROR"


@pytest.mark.parametrize("message", [
    "tool_choice is not supported in thinking mode",
    "Thinking enabled: tool_choice=required is unsupported",
    "Missing reasoning_content in the assistant message",
])
@pytest.mark.parametrize("nested", [False, True])
def test_thinking_configuration_conflict_has_actionable_safe_hint(message, nested, caplog):
    detail = {"message": message + " private-key-and-school-data"}
    with caplog.at_level(logging.WARNING):
        failure = safe_model_error(provider_error(400, {"error": detail} if nested else detail))
    assert failure.code == "MODEL_THINKING_CONFIG"
    assert '思考参数' in failure.message and '关闭额外深度分析' in failure.message
    assert "private-key" not in failure.message + caplog.text
    assert "school-data" not in failure.message + caplog.text


@pytest.mark.parametrize("message", [None, {"unexpected": "value"},
    "invalid reasoning_content format", "thinking budget must be an integer"])
def test_other_bad_parameters_keep_generic_safe_hint(message):
    failure = safe_model_error(provider_error(400, {"message": message}))
    assert failure.code == "MODEL_BAD_REQUEST"


def test_structured_output_loop_is_not_reported_as_network_failure():
    from langgraph.errors import GraphRecursionError
    assert safe_model_error(GraphRecursionError("raw-school-data")).code == "MODEL_STRUCTURE"


@pytest.mark.parametrize('detail,expected,status', [
    ({'code': 500, 'status': 'INTERNAL'}, 'MODEL_UNAVAILABLE', 500),
    ({'error': {'code': 13, 'status': 'INTERNAL'}}, 'MODEL_UNAVAILABLE', 500),
    (json.dumps({'error': {'code': 503, 'status': 'UNAVAILABLE'}}), 'MODEL_UNAVAILABLE', 503),
    ({'status': 'DEADLINE_EXCEEDED'}, 'MODEL_TIMEOUT', 504),
    ({'code': 'request_timeout'}, 'MODEL_TIMEOUT', None),
    ({'code': 'insufficient_quota'}, 'MODEL_QUOTA', 429),
    ({'code': 'rate_limit_exceeded'}, 'MODEL_RATE_LIMIT', 429),
    ({'status': 'RESOURCE_EXHAUSTED'}, 'MODEL_RATE_LIMIT', 429),
    ({'code': 'model_not_found'}, 'MODEL_NOT_FOUND', 404),
    ({'code': 'invalid_api_key'}, 'MODEL_AUTH', 401),
    ({'code': 403, 'status': 'PERMISSION_DENIED'}, 'MODEL_AUTH', 403),
    ({'code': 'context_length_exceeded'}, 'MODEL_CONTEXT_LIMIT', None),
    ({'code': 413}, 'MODEL_CONTEXT_LIMIT', 413),
    ({'code': 'streaming_not_supported'}, 'MODEL_STREAM_UNSUPPORTED', None),
    ({'code': 400, 'message': 'stream_options is unsupported'}, 'MODEL_STREAM_UNSUPPORTED', 400),
    ({'code': 400, 'message': 'tool_choice unsupported with thinking'}, 'MODEL_THINKING_CONFIG', 400),
    ({'code': 422}, 'MODEL_BAD_REQUEST', 422),
    ({'code': 'private-key-and-school-data', 'status': 'private-key-and-school-data'}, 'MODEL_STREAM_ERROR', None),
    ('private-key-and-school-data', 'MODEL_STREAM_ERROR', None),
    (None, 'MODEL_STREAM_ERROR', None),
])
def test_sdk_stream_event_errors_without_http_failure_are_actionable_and_safe(detail, expected, status, caplog):
    request = httpx.Request('POST', 'https://example.invalid/chat/completions')
    error = APIError('private-key-and-school-data', request=request, body=detail)
    with caplog.at_level(logging.WARNING):
        failure = safe_model_error(error)
    assert failure.code == expected and failure.diagnostics['source'] == 'stream_event'
    assert failure.diagnostics.get('upstreamStatus') == status
    assert 'httpStatus' not in failure.diagnostics
    text = caplog.text + failure.message + json.dumps(failure.diagnostics)
    assert 'private-key' not in text and 'school-data' not in text
    assert 'source=stream_event' in caplog.text


def test_http_auth_status_takes_priority_over_body_context_markers():
    failure = safe_model_error(provider_error(401, {'code': 'context_length_exceeded'}))
    assert failure.code == 'MODEL_AUTH'
    assert failure.diagnostics['httpStatus'] == 401 and failure.diagnostics['source'] == 'request'


def test_native_claude_stream_error_with_http_200_is_not_a_successful_request():
    import anthropic
    from relay_transport import transport_for
    api = transport_for('claude')
    error = anthropic.APIStatusError('private-key-and-school-data',
        response=api.Response(200, request=api.Request('POST', 'https://example.invalid/messages')),
        body={'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'private-key-and-school-data'}})
    failure = safe_model_error(error)
    assert failure.code == 'MODEL_UNAVAILABLE'
    assert failure.diagnostics['httpStatus'] == 200
    assert failure.diagnostics['upstreamStatus'] == 529


def test_connection_api_preserves_safe_error_category(tmp_path, monkeypatch, caplog):
    app_module = import_module("assess_agent.app")

    def reject(*args):
        raise provider_error(404, {"code": "model_not_found", "message": "private-key-and-school-data"})

    monkeypatch.setattr(app_module, "build_model", reject)
    with caplog.at_level(logging.WARNING), TestClient(create_app(Settings(data_root=tmp_path))) as client:
        response = client.post("/models/deepseek/test")
    assert response.status_code == 422
    assert response.json()["code"] == "MODEL_NOT_FOUND"
    assert "stage=configuration" in caplog.text
    assert "private-key" not in response.text + caplog.text


def test_deepseek_connection_protocol_and_extra_parameters(tmp_path, monkeypatch):
    """Exercise real SDK serialization and LangChain tool loop with an offline provider."""
    from assess_agent.model_config import ModelConfigInput, ModelConfigStore
    from assess_agent.models import build_model
    app_module = import_module("assess_agent.app")
    settings = Settings(data_root=tmp_path)
    ModelConfigStore(tmp_path).save("deepseek", ModelConfigInput.model_validate({
        "model": "deepseek-flash", "baseUrl": "https://example.invalid",
        "apiKey": "test-only-secret", "extraBody": {"thinking": {"type": "disabled"}}, "revision": 0,
        "reasoningMode": "off",
    }))
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/chat/completions"
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["tool_choice"] == "required"
        assert "max_tokens" in body and "max_completion_tokens" not in body
        assert "parallel_tool_calls" not in body
        index = len(requests)
        arguments = {"value": "READY"} if index == 1 else {"answer": "READY"}
        name = "connection_echo" if index == 1 else "ConnectionResult"
        if index == 2:
            assert body["messages"][-1]["role"] == "tool"
        return httpx.Response(200, json={
            "id": f"test-{index}", "object": "chat.completion", "created": 0, "model": "deepseek-flash",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                "tool_calls": [{"id": f"call-{index}", "type": "function", "function": {
                    "name": name, "arguments": json.dumps(arguments),
                }}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client, metadata = build_model(settings, "deepseek")
        # Inject only the HTTP transport, keeping the actual production model and agent.
        import openai
        client.client = openai.OpenAI(api_key="test-only-secret", base_url="https://example.invalid",
                                      http_client=transport).chat.completions
        monkeypatch.setattr(app_module, "build_model", lambda *args: (client, metadata))
        with TestClient(create_app(settings)) as app_client:
            result = app_client.post("/models/deepseek/test")
    assert result.status_code == 200, result.text
    assert result.json()["capabilities"] == ["toolCalling", "structuredOutput"]
    assert len(requests) == 2
