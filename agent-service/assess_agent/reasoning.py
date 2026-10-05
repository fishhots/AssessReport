"""Provider-specific reasoning parameters. Names, endpoints and keys stay user-configured.

Analysis requests carry no tools or prior assistant turns. This avoids both forced-tool
restrictions and the need to replay private reasoning/signature fields between turns.
"""
from copy import deepcopy
import re


def supports_reasoning(provider, model):
    name = model.lower()
    qwen_alias = name.startswith(('qwen-plus','qwen-flash','qwen-turbo'))
    snapshot = re.search(r'20\d{2}-\d{2}-\d{2}', name)
    if snapshot and snapshot.group() < '2025-04-28':
        qwen_alias = False
    return {
        'deepseek': name.startswith('deepseek'),
        'qwen': (name.startswith(('qwen3', 'qwq')) or qwen_alias) and '-instruct' not in name,
        'glm': bool(re.match(r'glm-(?:4\.[5-9]|[5-9])', name)),
        'gpt': bool(re.match(r'(?:o[134](?:-|$)|gpt-[5-9])', name)),
        'claude': bool(re.search(r'claude-(?:3-7|(?:sonnet|opus|haiku)-[4-9]|[4-9]|fable|mythos)', name)),
        'gemini': bool(re.match(r'gemini-(?:2\.5|[3-9])', name)),
    }.get(provider, False)


def parameters(config, provider, stage):
    extra = deepcopy(config['extra_body'])
    mode = config.get('reasoningMode', 'auto')
    supported = supports_reasoning(provider, config['model'])
    enabled = mode != 'off' and (mode == 'on' or supported)
    thinking = stage.startswith('analysis') and enabled
    effort = config.get('reasoningEffort', 'medium')
    budget = config.get('thinkingBudget', 4096)
    options = {}
    # Long chapter JSON needs incremental transport even with thinking disabled.
    if provider == 'qwen':
        options.update(streaming=True, stream_usage=True)
    name = config['model'].lower()
    qwen_only = provider == 'qwen' and ('thinking' in name or name.startswith('qwq') or name in
                                     ('qwen3.7-max-preview','qwen3.7-max-2026-05-17','qwen3.8-2.4t-a95b'))
    if supported or mode == 'on':
        if provider == 'deepseek':
            extra['thinking'] = {'type': 'enabled' if thinking or 'reasoner' in name else 'disabled'}
            extra['reasoning_effort'] = ('low' if effort == 'low' else 'high') if thinking else ('low' if 'reasoner' in name else 'none')
        elif provider == 'qwen':
            if not qwen_only:
                extra['enable_thinking'] = thinking
            else:
                extra.pop('enable_thinking', None)
            if thinking or qwen_only:
                extra['thinking_budget'] = budget if thinking else min(budget, 2048)
                options['streaming'] = True
                options['stream_usage'] = True
        elif provider == 'glm':
            always_thinking = bool(re.match(r'glm-(?:5\.[3-9]|[6-9])', name))
            extra['thinking'] = {'type': 'enabled' if thinking or always_thinking else 'disabled'}
            if re.match(r'glm-(?:5\.[2-9]|[6-9])', name):
                extra['reasoning_effort'] = ('low' if effort == 'low' else 'high') if thinking else 'low'
        elif provider == 'gpt':
            extra.pop('reasoning_effort', None)
            extra.pop('reasoning', None)
            if config.get('channel') == 'zhizengzeng':
                options['reasoning_effort'] = effort if thinking else 'low'
            else:
                options['reasoning'] = {'effort': effort if thinking else 'low'}
        elif provider == 'gemini':
            # Native thinking_config and reasoning_effort are mutually exclusive.
            if isinstance(extra.get('extra_body'), dict):
                google = extra['extra_body'].get('google', {})
                if isinstance(google, dict):
                    google.pop('thinking_config', None)
            extra.pop('reasoning_effort', None)
            options['reasoning_effort'] = effort if thinking else 'low'
        elif provider == 'claude':
            extra.pop('thinking', None)
            extra.pop('output_config', None)
            adaptive = bool(re.search(r'(?:opus|sonnet)-(?:4-[6-9]|[5-9])|fable|mythos', name))
            if thinking:
                options['thinking'] = {'type': 'adaptive'} if adaptive else {'type': 'enabled', 'budget_tokens': budget}
                if adaptive:
                    options['output_config'] = {'effort': effort}
            else:
                if adaptive:
                    options['thinking'] = {'type': 'adaptive'}
                    options['output_config'] = {'effort': 'low'}
                else:
                    options['thinking'] = {'type': 'disabled'}
    if thinking or (provider in ('gpt','claude') and options):
        for key in ('temperature', 'top_p', 'top_logprobs', 'logprobs'):
            extra.pop(key, None)
    return extra, options, {'analysisEnabled': enabled,
                            'toolFree': supported and (provider == 'gemini' or 'reasoner' in name or qwen_only or
                                                     (provider == 'glm' and bool(re.match(r'glm-(?:5\.[3-9]|[6-9])', name)))),
                            'reasoningMode': mode,
                            'reasoningEffort': effort, 'stage': stage}


def answer_text(message):
    if isinstance(message.content, str):
        return message.content
    return '\n'.join(block.get('text', '') for block in message.content
                     if isinstance(block, dict) and block.get('type') in ('text', 'output_text'))
