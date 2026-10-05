import json
import logging


logger = logging.getLogger(__name__)

QUOTA_CODES = frozenset({
    "insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded", "organization_usage_limit_exceeded",
})
PROVIDER_STATUS_CODES = {
    'INVALID_ARGUMENT': 400, 'UNAUTHENTICATED': 401, 'PERMISSION_DENIED': 403,
    'NOT_FOUND': 404, 'RESOURCE_EXHAUSTED': 429, 'INTERNAL': 500,
    'UNAVAILABLE': 503, 'DEADLINE_EXCEEDED': 504,
}
PROVIDER_ERROR_TYPES = {
    'invalid_request_error': 400, 'authentication_error': 401, 'permission_error': 403,
    'not_found_error': 404, 'rate_limit_error': 429, 'api_error': 500, 'overloaded_error': 529,
}
KNOWN_CODES = QUOTA_CODES | frozenset(PROVIDER_STATUS_CODES) | {
    'rate_limit_exceeded', 'slow_down', 'server_is_overloaded', 'server_error',
    'context_length_exceeded', 'model_not_found', 'invalid_api_key',
    'invalid_request_error', 'request_timeout', 'timeout', 'streaming_not_supported',
}


def _error_detail(error):
    """Unwrap SDK stream bodies, including JSON strings, without logging text."""
    body = getattr(error, 'body', None)
    for _ in range(4):
        if isinstance(body, str):
            if len(body) > 16384:
                break
            try:
                body = json.loads(body)
            except ValueError:
                return {'message': body}
        elif isinstance(body, dict) and 'error' in body:
            body = body['error']
        else:
            break
    return body if isinstance(body, dict) else {}


class AgentError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def safe_model_error(error: Exception) -> AgentError:
    # Classify SDK errors without exposing their raw message, request, or response.
    status = getattr(error, "status_code", None)
    detail = _error_detail(error)
    code = detail.get("code") if isinstance(detail.get("code"), str) else None
    error_type = detail.get("type")
    names = {kind.__name__.lower() for kind in type(error).__mro__}
    # A stream can start with HTTP 200 and later carry a provider error. SDK
    # APIError then has no status_code, unlike errors before the stream starts.
    streaming_error = 'apierror' in names and (not isinstance(status, int) or status == 200)
    upstream_status = None
    provider_status = detail.get('status')
    provider_status = provider_status if isinstance(provider_status, str) and provider_status in PROVIDER_STATUS_CODES else None
    if streaming_error:
        candidate = detail.get('status_code', detail.get('code'))
        if isinstance(candidate, str) and candidate.isdecimal():
            candidate = int(candidate)
        if type(candidate) is int and candidate in (400, 401, 403, 404, 408, 413, 422, 429, 500, 502, 503, 504, 529):
            upstream_status = candidate
        elif provider_status or code in PROVIDER_STATUS_CODES:
            upstream_status = PROVIDER_STATUS_CODES[provider_status or code]
        elif code in QUOTA_CODES or error_type == 'insufficient_quota' or code in ('rate_limit_exceeded', 'slow_down'):
            upstream_status = 429
        elif code == 'model_not_found':
            upstream_status = 404
        elif code == 'invalid_api_key':
            upstream_status = 401
        elif code == 'invalid_request_error':
            upstream_status = 400
        elif code in ('server_error', 'server_is_overloaded'):
            upstream_status = 503
        elif isinstance(error_type, str) and error_type in PROVIDER_ERROR_TYPES:
            upstream_status = PROVIDER_ERROR_TYPES[error_type]
        status = upstream_status
    message = detail.get('message')
    if not isinstance(message, str):
        message = getattr(error, 'message', '') if streaming_error else ''
    message = message[:4096].lower() if isinstance(message, str) else ''
    context_error = code == 'context_length_exceeded' or any(marker in message for marker in
        ('context length exceeded', 'context_length_exceeded', 'maximum context length', 'input token limit'))
    stream_unsupported = code == 'streaming_not_supported' or (
        'stream' in message and any(marker in message for marker in ('not supported', 'unsupported', '不支持')))
    if status in (401, 403):
        result = AgentError("MODEL_AUTH", "模型认证失败，请在模型设置中检查密钥和账号权限")
    elif status == 429 and (code in QUOTA_CODES or error_type == "insufficient_quota"):
        result = AgentError("MODEL_QUOTA", "模型 API 余额不足或使用额度已用尽，请检查模型平台的余额和额度")
    elif status == 429:
        result = AgentError("MODEL_RATE_LIMIT", "模型调用已限流，请稍后重试")
    elif status == 404:
        result = AgentError("MODEL_NOT_FOUND", "模型或接口不存在，请检查模型名称和接口基础地址")
    elif status in (408, 504) or streaming_error and code in ('timeout', 'request_timeout'):
        result = AgentError("MODEL_TIMEOUT", "模型接口在生成过程中超时，已停止本次调用；模型已处理的内容可能计费")
    elif status == 413 or context_error and (status in (None, 400, 422)):
        result = AgentError("MODEL_CONTEXT_LIMIT", "模型输入或请求超过接口上限，请减少单次请求内容或选择支持更长上下文的模型")
    elif stream_unsupported and status in (None, 400, 422):
        result = AgentError("MODEL_STREAM_UNSUPPORTED", "模型接口不支持当前流式参数，请核对中转接口的流式兼容性")
    elif status in (400, 422):
        # Inspect only diagnostic markers; never copy provider text into the UI or logs.
        thinking_tool_conflict = "tool_choice" in message and "thinking" in message
        thinking_history_missing = "reasoning_content" in message and any(
            marker in message for marker in ("missing", "required", "must", "缺少", "必需"))
        if thinking_tool_conflict or thinking_history_missing:
            result = AgentError("MODEL_THINKING_CONFIG",
                '模型接口的思考模式或工具协议不兼容，请检查接口支持的思考参数，'
                '或在模型设置中关闭额外深度分析后保存并测试')
        else:
            result = AgentError("MODEL_BAD_REQUEST", "模型接口拒绝了请求参数，请检查模型名称、接口地址和额外参数")
    elif isinstance(status, int) and status >= 500:
        result = AgentError("MODEL_UNAVAILABLE", "模型平台服务暂时不可用，请稍后重试")
    elif any("timeout" in name for name in names):
        result = AgentError("MODEL_TIMEOUT", "模型响应超时，已停止本次调用；模型已处理的内容可能计费")
    elif names & {"apiconnectionerror", "connecterror", "networkerror", "proxyerror"}:
        result = AgentError("MODEL_CONNECTION", "无法连接模型接口，请检查网络、代理和接口地址")
    elif names & {"structuredoutputvalidationerror", "multiplestructuredoutputserror", "outputparserexception", "graphrecursionerror"}:
        result = AgentError("MODEL_STRUCTURE", "模型未返回符合要求的结构化结果，请重试或更换模型")
    elif streaming_error:
        result = AgentError("MODEL_STREAM_ERROR", "模型接口在流式生成过程中返回错误，已停止本次调用；请查看模型平台对应调用详情后继续生成")
    else:
        result = AgentError("MODEL_ERROR", "模型调用失败，请重试；若持续失败，请查看智能体服务日志")
    # Log only an allowlisted category; provider messages can include school data or keys.
    result.diagnostics = {'source': 'stream_event' if streaming_error else 'request',
                          'category': result.code}
    if isinstance(status, int):
        result.diagnostics['upstreamStatus' if streaming_error else 'httpStatus'] = status
    if code in KNOWN_CODES:
        result.diagnostics['providerCode'] = code
    if provider_status:
        result.diagnostics['providerStatus'] = provider_status
    if isinstance(error_type, str) and error_type in PROVIDER_ERROR_TYPES:
        result.diagnostics['providerType'] = error_type
    if streaming_error and getattr(error, 'status_code', None) == 200:
        result.diagnostics['httpStatus'] = 200
    logger.warning("Model call failed: category=%s source=%s status=%s exception=%s provider_code=%s provider_status=%s",
                   result.code, result.diagnostics['source'], status if isinstance(status, int) else "unknown",
                   type(error).__name__, code if code in KNOWN_CODES else "unknown", provider_status or 'unknown')
    return result
