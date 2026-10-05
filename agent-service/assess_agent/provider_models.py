from langchain_openai.chat_models.base import BaseChatOpenAI


class CompatibleChatModel(BaseChatOpenAI):
    """Keep safe reasoning usage metadata without retaining private reasoning text."""

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        result = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if result and any((choice.get('delta') or {}).get('reasoning_content') for choice in chunk.get('choices', [])):
            result.message.response_metadata['reasoningObserved'] = True
        return result

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        body = response if isinstance(response, dict) else response.model_dump()
        choices = body.get('choices', [])
        usage = body.get('usage') or {}
        details = usage.get('completion_tokens_details') or {}
        for index, generation in enumerate(result.generations):
            choice = choices[index] if index < len(choices) else {}
            observed = bool((choice.get('message') or {}).get('reasoning_content'))
            generation.message.response_metadata['reasoningObserved'] = observed
            if isinstance(details.get('reasoning_tokens'), int) and generation.message.usage_metadata:
                generation.message.usage_metadata.setdefault('output_token_details', {})['reasoning'] = details['reasoning_tokens']
        return result
