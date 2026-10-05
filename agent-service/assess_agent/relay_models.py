"""Keep native Claude relay streams bounded, closed, and correctly metered."""
from contextvars import ContextVar

from langchain_anthropic import ChatAnthropic


_stream_state = ContextVar('relay_anthropic_stream_state', default=None)
_INPUT_USAGE_FIELDS = ('input_tokens', 'cache_read_input_tokens',
                       'cache_creation_input_tokens', 'cache_creation')


class RelayChatAnthropic(ChatAnthropic):
    def _create(self, payload):
        response = super()._create(payload)
        state = _stream_state.get()
        if state is not None and payload.get('stream'):
            state['responses'].append(response)
        return response

    async def _acreate(self, payload):
        response = await super()._acreate(payload)
        state = _stream_state.get()
        if state is not None and payload.get('stream'):
            state['responses'].append(response)
        return response

    def _stream(self, *args, **kwargs):
        state = {'responses': [], 'usage': {}}
        token = _stream_state.set(state)
        try:
            yield from super()._stream(*args, **kwargs)
        finally:
            try:
                for response in state['responses']:
                    response.close()
            finally:
                _stream_state.reset(token)

    async def _astream(self, *args, **kwargs):
        state = {'responses': [], 'usage': {}}
        token = _stream_state.set(state)
        try:
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk
        finally:
            try:
                for response in state['responses']:
                    await response.close()
            finally:
                _stream_state.reset(token)

    def _make_message_chunk_from_anthropic_event(self, event, **kwargs):
        state = _stream_state.get()
        if state is not None:
            if event.type == 'message_start':
                # Anthropic may send input usage only at the start, while
                # LangChain emits its usage chunk at message_delta.
                state['usage'] = event.message.usage.model_dump()
            elif event.type == 'message_delta':
                missing = {field: state['usage'][field] for field in _INPUT_USAGE_FIELDS
                           if state['usage'].get(field) is not None
                           and getattr(event.usage, field, None) is None}
                if missing:
                    event = event.model_copy(update={
                        'usage': event.usage.model_copy(update=missing)})
        return super()._make_message_chunk_from_anthropic_event(event, **kwargs)
