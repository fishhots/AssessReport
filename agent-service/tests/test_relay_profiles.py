import json
import sqlite3
from contextlib import closing

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from assess_agent.app import create_app
from assess_agent.errors import AgentError
from assess_agent.model_config import ModelConfigInput, ModelConfigStore
from assess_agent.models import build_model
from assess_agent.provider_routes import RELAY_BASE_URLS, OFFICIAL_BASE_URLS
from assess_agent.reasoning import answer_text
from assess_agent.settings import Settings
from relay_transport import attach_client, response_events, transport_for

def config(provider, channel, revision=0, key='relay-test-secret', **changes):
    return ModelConfigInput(model={'gpt':'gpt-5.4','claude':'claude-sonnet-4-6','gemini':'gemini-3.1-pro-preview'}[provider],
        baseUrl=(RELAY_BASE_URLS if channel=='zhizengzeng' else OFFICIAL_BASE_URLS)[provider],
        channel=channel, apiKey=key, revision=revision, **changes)


@pytest.mark.parametrize('provider',list(RELAY_BASE_URLS))
def test_connection_profiles_preserve_both_keys_and_switch_only_on_save(tmp_path,provider):
    store=ModelConfigStore(tmp_path)
    initial=store.public(provider)
    assert initial['channel']=='zhizengzeng' and initial['baseUrl']==RELAY_BASE_URLS[provider]
    assert not initial['configured'] and not initial['hasApiKey']
    store.save(provider,config(provider,'official',key='official-test-secret'))
    preview=store.public(provider,'zhizengzeng')
    assert not preview['hasApiKey'] and preview['revision']==1
    assert store.runtime(provider)['channel']=='official'
    with pytest.raises(AgentError) as missing:
        store.save(provider,config(provider,'zhizengzeng',revision=1,key=None))
    assert missing.value.code=='MODEL_CONFIG'
    assert store.runtime(provider)['key']=='official-test-secret'
    relay=store.save(provider,config(provider,'zhizengzeng',revision=1))
    assert relay['channel']==relay['activeChannel']=='zhizengzeng'
    frozen=store.runtime(provider)
    store=ModelConfigStore(tmp_path)
    official=store.public(provider,'official')
    assert official['hasApiKey'] and official['revision']==2
    assert official['activeChannel']=='zhizengzeng'
    assert not any(key in json.dumps(official) for key in ('official-test-secret','relay-test-secret'))
    store.save(provider,config(provider,'official',revision=2,key=None))
    assert store.runtime(provider)['key']=='official-test-secret'
    assert frozen['channel']=='zhizengzeng' and frozen['key']=='relay-test-secret'
    with pytest.raises(AgentError) as stale:
        store.save(provider,config(provider,'zhizengzeng',revision=2,key='stale-key'))
    assert stale.value.code=='MODEL_CONFIG_CONFLICT'
    store.save(provider,config(provider,'zhizengzeng',revision=3,key=None))
    assert store.runtime(provider)['key']=='relay-test-secret'
    assert b'relay-test-secret' not in store.database.read_bytes()
    assert b'official-test-secret' not in store.database.read_bytes()


def test_legacy_encrypted_configuration_migrates_without_replacing_values(tmp_path):
    key=Fernet.generate_key(); (tmp_path/'model-config.key').write_bytes(key)
    ciphertext=Fernet(key).encrypt(b'legacy-test-key').decode()
    with closing(sqlite3.connect(tmp_path/'model-config.sqlite3')) as db,db:
        db.execute('CREATE TABLE models(provider TEXT PRIMARY KEY,model TEXT,base_url TEXT,key_ciphertext TEXT,extra_body TEXT,revision INTEGER,updated_at TEXT)')
        db.execute('INSERT INTO models VALUES(?,?,?,?,?,?,?)',('gpt','legacy-custom-model','https://legacy.invalid/v1',ciphertext,'{}',7,'2026-10-01'))
    store=ModelConfigStore(tmp_path)
    saved=store.public('gpt')
    assert saved['channel']=='official' and saved['revision']==7
    assert saved['model']=='legacy-custom-model' and saved['baseUrl']=='https://legacy.invalid/v1'
    assert store.runtime('gpt')['key']=='legacy-test-key'
    assert store.row('gpt','official')['key_ciphertext']==ciphertext
    store.save('gpt',config('gpt','zhizengzeng',revision=7))
    assert ModelConfigStore(tmp_path).public('gpt','official')['model']=='legacy-custom-model'


def test_profile_http_preview_and_invalid_channels_are_safe(tmp_path):
    with TestClient(create_app(Settings(data_root=tmp_path))) as client:
        preview=client.get('/models/gpt/config?channel=official')
        assert preview.status_code==200 and preview.json()['baseUrl']==OFFICIAL_BASE_URLS['gpt']
        data=config('gpt','zhizengzeng').model_dump(mode='json',by_alias=True)
        data['apiKey']='http-relay-test-key'
        saved=client.put('/models/gpt/config',json=data)
        assert saved.status_code==200 and saved.json()['channel']=='zhizengzeng'
        assert 'http-relay-test-key' not in saved.text
        assert client.get('/models/gpt/config?channel=official').json()['hasApiKey'] is False
        assert client.get('/models/gpt/config').json()['channel']=='zhizengzeng'
        assert client.get('/models/qwen/config?channel=zhizengzeng').status_code==422
        assert client.get('/models/gpt/config?channel=invalid').status_code==422
        data.update(channel='invalid',revision=1)
        bad=client.put('/models/gpt/config',json=data)
        assert bad.status_code==422 and 'http-relay-test-key' not in bad.text


@pytest.mark.parametrize('provider',['gpt','claude','gemini'])
@pytest.mark.parametrize('stage',['analysis','structured'])
def test_relay_real_sdk_request_paths_headers_and_reasoning_without_network(tmp_path,provider,stage):
    ModelConfigStore(tmp_path).save(provider,config(provider,'zhizengzeng'))
    model,metadata=build_model(Settings(data_root=tmp_path),provider,stage=stage)
    transport_api=transport_for(provider)
    requests=[]
    def respond(request):
        requests.append(request)
        return transport_api.Response(200,headers={'content-type':'text/event-stream'},
            content=b''.join(response_events(provider,model)))
    with transport_api.Client(transport=transport_api.MockTransport(respond)) as transport:
        attach_client(model,provider,transport)
        assert answer_text(model.invoke([HumanMessage(content='连接测试')]))=='READY'
    assert len(requests)==1 and metadata['channel']=='zhizengzeng'
    request=requests[0];body=json.loads(request.content)
    assert body['stream'] is True
    assert request.extensions['timeout']['read']==600
    assert request.url.host=='api.zhizengzeng.com'
    if provider=='claude':
        assert request.url.path=='/anthropic/v1/messages'
        assert request.headers['x-api-key']=='relay-test-secret'
        assert body['thinking']=={'type':'adaptive'}
        assert body['output_config']['effort']==('medium' if stage=='analysis' else 'low')
    else:
        assert request.url.path=='/v1/chat/completions'
        assert request.headers['authorization']=='Bearer relay-test-secret'
        assert body['reasoning_effort']==('medium' if stage=='analysis' else 'low')
        assert all(field not in body for field in ('reasoning','store','include','input'))
        token_field='max_completion_tokens' if provider=='gpt' else 'max_tokens'
        assert body[token_field] > 0
        assert ('max_tokens' if provider=='gpt' else 'max_completion_tokens') not in body
    assert not body.get('tools') and not body.get('tool_choice')


def test_official_gpt_keeps_responses_and_relay_does_not_mutate_frozen_config(tmp_path):
    store=ModelConfigStore(tmp_path)
    store.save('gpt',config('gpt','official'))
    official=store.runtime('gpt');official['extra_body']=json.loads(official['extra_body'])
    store.save('gpt',config('gpt','zhizengzeng',revision=1))
    model,metadata=build_model(Settings(data_root=tmp_path),'gpt',stage='analysis',provider_config=official)
    body=model._get_request_payload([HumanMessage(content='test')])
    assert metadata['channel']=='official' and model.use_responses_api is True
    assert body['reasoning']=={'effort':'medium'} and body['store'] is False
    assert body['include']==['reasoning.encrypted_content']
    assert 'reasoning_effort' not in body and 'reasoning' not in official['extra_body']
