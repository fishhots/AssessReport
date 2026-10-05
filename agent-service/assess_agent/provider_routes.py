"""Connection presets; credentials and model names remain user configured."""
from .errors import AgentError

RELAY_PROVIDERS = frozenset(('gpt', 'claude', 'gemini'))
RELAY_BASE_URLS = {
    'gpt': 'https://api.zhizengzeng.com/v1',
    'claude': 'https://api.zhizengzeng.com/anthropic',
    # The relay documents an OpenAI-compatible text endpoint for Gemini.
    'gemini': 'https://api.zhizengzeng.com/v1',
}
OFFICIAL_BASE_URLS = {
    'gpt': 'https://api.openai.com/v1',
    'claude': 'https://api.anthropic.com',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/openai',
}


def default_channel(provider):
    return 'zhizengzeng' if provider in RELAY_PROVIDERS else 'official'


def validate_channel(provider, channel):
    if channel not in ('official', 'zhizengzeng') or (channel == 'zhizengzeng' and provider not in RELAY_PROVIDERS):
        raise AgentError('MODEL_CONFIG', '当前模型不支持所选调用方式', 422)


def default_base_url(provider, channel):
    return (RELAY_BASE_URLS if channel == 'zhizengzeng' else OFFICIAL_BASE_URLS).get(provider, '')
