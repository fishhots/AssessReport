"""Real SDK fixtures for relay requests; never access a model endpoint."""
import json
import anthropic
from anthropic import _base_client as anthropic_transport
import httpx
import openai


def transport_for(provider):
    return getattr(anthropic_transport, 'httpx2', httpx) if provider == 'claude' else httpx


def attach_client(model, provider, transport):
    if provider == 'claude':
        model._client = anthropic.Anthropic(api_key=model.anthropic_api_key.get_secret_value(),
            base_url=model.anthropic_api_url, http_client=transport,
            timeout=model._client.timeout, max_retries=model.max_retries)
    else:
        model.root_client = openai.OpenAI(api_key=model.openai_api_key.get_secret_value(),
            base_url=model.openai_api_base, http_client=transport,
            timeout=model.request_timeout, max_retries=model.max_retries)
        model.client = model.root_client.chat.completions


def text_delta(provider, text):
    if provider == 'claude':
        body = {'type': 'content_block_delta', 'index': 0,
                'delta': {'type': 'text_delta', 'text': text}}
        return f'event: content_block_delta\ndata: {json.dumps(body)}\n\n'.encode()
    body = {'id': 'chat-test', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'offline-model',
            'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]}
    return f'data: {json.dumps(body)}\n\n'.encode()


def response_events(provider, model):
    if provider == 'claude':
        events = [
            {'type': 'message_start', 'message': {'id': 'msg-test', 'type': 'message', 'role': 'assistant',
                'model': model.model, 'content': [], 'stop_reason': None, 'stop_sequence': None,
                'usage': {'input_tokens': 10, 'output_tokens': 0}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'READY'}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                'usage': {'output_tokens': 2}},
            {'type': 'message_stop'},
        ]
        return [f'event: {e["type"]}\ndata: {json.dumps(e)}\n\n'.encode() for e in events]
    body = {'id': 'chat-test', 'object': 'chat.completion.chunk', 'created': 0, 'model': model.model_name}
    chunks = [
        {**body, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'READY'}, 'finish_reason': None}]},
        {**body, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        {**body, 'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}},
    ]
    return [f'data: {json.dumps(c)}\n\n'.encode() for c in chunks] + [b'data: [DONE]\n\n']
