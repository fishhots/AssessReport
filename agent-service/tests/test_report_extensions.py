import json
from uuid import uuid4

from docx import Document
from langchain_core.messages import AIMessage
import pytest
from pydantic import ValidationError

from assess_agent.evidence import EvidenceStore
from assess_agent.model_config import ModelConfigInput, ModelConfigStore
from assess_agent.models import SectionWriter, build_model
from assess_agent.parsers import parse_job
from assess_agent.reasoning import parameters
from assess_agent.schemas import CustomTopic
from assess_agent.store import JobStore
from assess_agent.workflow import run_workflow
from test_agent import setup_task
from fake_model import ToolCallingTestModel


def test_combined_report_preserves_topic_and_fixed_personalized_dimensions(tmp_path):
    settings, request = setup_task(tmp_path)
    request.custom_topic = CustomTopic(title='未来5年信息类人才培养方向', requirements='分析AI能力、课程路径与实践评价。')
    jobs = JobStore(settings.data_root)
    jobs.create(request)
    result = run_workflow(request, settings, jobs, 1, model=ToolCallingTestModel())
    directory = settings.jobs_root() / str(request.task_id) / 'attempt-1'
    doc = Document(directory / 'report.docx')
    headings = [p.text for p in doc.paragraphs if p.style.name == 'Heading 1']
    assert headings[:3] == [*request.chapters, *request.extension_titles()]
    assert set(result['artifacts']) == {'report'}
    assert not (directory / 'diagnosis.docx').exists()
    assert json.loads(jobs.get(str(request.task_id))['request'])['customTopic']['requirements'] == request.custom_topic.requirements
    assert len(doc.sections) == 3
    assert abs(doc.sections[-1].left_margin.cm - 3.0004) < .01
    assert abs(doc.sections[-1].right_margin.cm - 2.7005) < .01
    assert '{{SCHOOL}}' not in doc.element.xml and '{{YEAR}}' not in doc.element.xml


@pytest.mark.parametrize('value', [{'title':' ', 'requirements':'test'}, {'title':'topic', 'requirements':' '},
                                  {'title':'line\nbreak', 'requirements':'test'}])
def test_custom_topic_requires_real_title_and_requirements(value):
    with pytest.raises(ValidationError):
        CustomTopic.model_validate(value)


@pytest.mark.parametrize('provider,model,expected', [
    ('deepseek','deepseek-flash', {'thinking':{'type':'enabled'}, 'reasoning_effort':'high'}),
    ('qwen','qwen3.6-plus', {'enable_thinking':True, 'thinking_budget':4096}),
    ('glm','glm-4.7', {'thinking':{'type':'enabled'}}),
    ('gemini','gemini-3.1-pro', {}),
    ('gpt','gpt-5.4', {}),
    ('claude','claude-sonnet-4-6', {}),
])
def test_reasoning_parameters_are_provider_specific_and_do_not_mutate_saved_config(provider, model, expected):
    original = {'model':model, 'extra_body':{'temperature': .6, 'top_p': .9}, 'reasoningMode':'auto'}
    extra, options, metadata = parameters(original, provider, 'analysis')
    assert metadata['analysisEnabled']
    assert 'temperature' not in extra and 'top_p' not in extra
    assert original['extra_body'] == {'temperature':.6, 'top_p':.9}
    for key,value in expected.items():
        assert extra[key] == value
    if provider == 'gpt': assert options['reasoning'] == {'effort':'medium'}
    if provider == 'gemini': assert options['reasoning_effort'] == 'medium'
    if provider == 'claude': assert options['thinking'] == {'type':'adaptive'}
    if provider == 'qwen': assert options['streaming']


def test_reasoning_configuration_survives_restart_and_blank_key_save(tmp_path):
    store = ModelConfigStore(tmp_path)
    saved = store.save('deepseek', ModelConfigInput(model='deepseek-flash', baseUrl='https://example.invalid',
        apiKey='test-only-secret', revision=0, reasoningMode='on', reasoningEffort='high', thinkingBudget=8192))
    updated = ModelConfigStore(tmp_path).save('deepseek', ModelConfigInput.model_validate({
        **{k: saved[k] for k in ('model','baseUrl','revision','reasoningMode','reasoningEffort','thinkingBudget')},
        'extraBody': {'custom': 'preserved'}}))
    assert updated['reasoningMode']=='on' and updated['thinkingBudget']==8192 and updated['hasApiKey']
    assert 'test-only-secret' not in json.dumps(updated)
    model, metadata = build_model(type('SettingsProxy', (), {'provider_config': lambda s,p: store.runtime(p),
        'model_timeout':30, 'analysis_output_tokens':16000, 'max_output_tokens':6000})(), 'deepseek', stage='analysis')
    assert model.extra_body['thinking'] == {'type':'enabled'} and metadata['analysisEnabled']


def test_thinking_pipeline_uses_fresh_requests_and_validates_composed_json(tmp_path):
    settings, request = setup_task(tmp_path)
    evidence = EvidenceStore(parse_job(request, settings))
    key = evidence.search('就业')[0]['id']
    class Recorder:
        def __init__(self, reply): self.reply, self.requests = reply, []
        def invoke(self, messages):
            self.requests.append(messages)
            assert all(message.type in ('human','system') for message in messages)
            return AIMessage(content=self.reply, usage_metadata={'input_tokens':10,'output_tokens':20,'total_tokens':30})
    analyzer = Recorder('该数据就业人数80人。引用 ' + key + '；不推断毕业去向落实率。')
    composer = Recorder(json.dumps({'title':request.chapters[0], 'blocks':[{
        'kind':'finding','text':'就业人数80人。','source_ids':[key]}]}))
    writer = SectionWriter(settings, request, evidence, model=composer)
    writer.analysis_model = analyzer
    draft = writer.write(request.chapters[0], '就业', diagnosis=True, question='分析本校AI课程支撑。')
    assert draft.blocks[0].source_ids == [key]
    assert '分析本校AI课程支撑' in analyzer.requests[0][-1].content
    assert writer.budget.calls == 2 and writer.budget.input_tokens == 20
    assert [t['phase'] for t in writer.call_trace] == ['analysis','compose']


def test_seven_column_configuration_migrates_without_changing_key_or_revision(tmp_path):
    import sqlite3
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    (tmp_path/'model-config.key').write_bytes(key)
    encrypted = Fernet(key).encrypt(b'test-only-secret').decode()
    with sqlite3.connect(tmp_path/'model-config.sqlite3') as connection:
        connection.execute('CREATE TABLE models (provider TEXT PRIMARY KEY, model TEXT, base_url TEXT, key_ciphertext TEXT, extra_body TEXT, revision INTEGER, updated_at TEXT)')
        connection.execute('INSERT INTO models VALUES (?,?,?,?,?,?,?)',
            ('deepseek','deepseek-flash','https://example.invalid',encrypted,'{"custom":"keep"}',15,'2026-10-01T00:00:00Z'))
    store = ModelConfigStore(tmp_path)
    assert store.public('deepseek')['reasoningMode']=='auto'
    assert store.public('deepseek')['revision']==15
    assert store.runtime('deepseek')['key']=='test-only-secret'
    with store.connect() as connection:
        assert connection.execute('SELECT key_ciphertext FROM models').fetchone()[0]==encrypted
    assert (tmp_path/'model-config.key').read_bytes()==key


def test_native_metric_headings_and_table_cell_formats(tmp_path):
    from assess_agent.exporter import new_document, template_heading, add_data_table
    from assess_agent.settings import REPORT_CONFIG, SERVICE_ROOT
    from assess_agent.validation import subsection_paths
    from docx.oxml.ns import qn
    _, request = setup_task(tmp_path)
    document = new_document(request, '毕业生培养质量评价报告')
    assert document.styles['Normal'].font.bold is False
    assert document.styles['Heading 1'].font.size.pt == 22
    assert document.styles['Heading 2'].font.bold is True
    profile = json.loads((SERVICE_ROOT/'config/template-profile.json').read_text(encoding='utf-8'))
    assert sum(len(c['topics']) for c in REPORT_CONFIG['chapters'])==109
    for chapter in REPORT_CONFIG['chapters']:
        for path in subsection_paths(chapter['title']):
            pattern = profile['headingPatterns'][chapter['title']+'|'+path]
            p = template_heading(document,path.split(' / ')[-1],chapter['title'],path)
            assert p.style.name==f"Heading {pattern['level']}"
            assert p.paragraph_format.keep_with_next is True
    table = add_data_table(document,['名称','结果'],[['示例指标','80']])
    assert table.rows[0].cells[0]._tc.tcPr.find(qn('w:shd')).get(qn('w:fill'))=='006699'
    assert table.rows[0].cells[0].paragraphs[0].runs[0].font.size.pt==10
    assert table.rows[0]._tr.trPr.find(qn('w:tblHeader')) is not None


def test_chapters_restart_native_subsection_numbers_and_retain_even_page_fields(tmp_path):
    from assess_agent.exporter import new_document, template_heading, body_section
    from docx.oxml.ns import qn
    _, request = setup_task(tmp_path)
    document = new_document(request, '毕业生培养质量评价报告')
    state = {}
    headings = [template_heading(document,'本章小结',title,'本章小结',numbering_state=state)
                for title in ('第一章 社会需求适应','第二章 就业质量')]
    ids = [p._p.pPr.numPr.numId.val for p in headings]
    assert ids[0] != ids[1]
    for num_id in ids:
        num = next(n for n in document.part.numbering_part.element.findall(qn('w:num')) if int(n.get(qn('w:numId')))==num_id)
        override = next(n for n in num.findall(qn('w:lvlOverride')) if n.get(qn('w:ilvl'))=='1')
        assert override.find(qn('w:startOverride')).get(qn('w:val'))=='1'
    body_section(document,'报告',request)
    section = document.sections[-1]
    assert request.school in section.even_page_header.paragraphs[0].text
    for footer in (section.footer,section.even_page_footer):
        assert 'PAGE' in ''.join(footer._element.xpath('.//w:instrText/text()'))
    assert not any('徽标' in n.get('alt','') for n in document.element.iter('{urn:schemas-microsoft-com:vml}shape'))


@pytest.mark.parametrize('provider,model', [('deepseek','deepseek-flash'),('glm','glm-5.3'),('gpt','gpt-5.4'),
    ('claude','claude-sonnet-4-6'),('gemini','gemini-3.1-pro'),('qwen','qwen-plus-latest'),('qwen','qwen3-next-80b-a3b-thinking')])
def test_disabled_analysis_preserves_compatible_provider_defaults(provider,model):
    extra, options, metadata = parameters({'model':model,'extra_body':{},'reasoningMode':'off'},provider,'tools')
    assert not metadata['analysisEnabled']
    if provider=='deepseek': assert extra['thinking']['type']=='disabled'
    if provider=='glm': assert extra['thinking']['type']=='enabled' and extra['reasoning_effort']=='low' and metadata['toolFree']
    if provider=='qwen' and 'thinking' in model:
        assert 'enable_thinking' not in extra and options['streaming'] and metadata['toolFree']


def test_cancel_during_analysis_prevents_compose_call(tmp_path):
    from assess_agent.errors import AgentError
    settings, request = setup_task(tmp_path)
    evidence = EvidenceStore(parse_job(request, settings))
    cancelled = False
    class Analyzer:
        def invoke(self, messages):
            nonlocal cancelled
            cancelled = True
            return AIMessage(content='分析结束')
    class Composer:
        def invoke(self, messages):
            pytest.fail('A cancelled task must not begin composition')
    writer = SectionWriter(settings, request, evidence, model=Composer())
    writer.analysis_model = Analyzer()
    def check():
        if cancelled: raise AgentError('JOB_CANCELLED','任务已终止')
    writer.budget.cancel_check = check
    with pytest.raises(AgentError, match='任务已终止'):
        writer.write(request.chapters[0],'就业')
    assert writer.budget.calls==1


def test_thinking_connection_serializes_actual_sdk_without_forced_tools(tmp_path,monkeypatch):
    import httpx, openai
    from importlib import import_module
    from fastapi.testclient import TestClient
    from assess_agent.app import create_app
    from assess_agent.settings import Settings
    app_module = import_module('assess_agent.app')
    settings = Settings(data_root=tmp_path)
    ModelConfigStore(tmp_path).save('deepseek',ModelConfigInput(model='deepseek-flash',
        baseUrl='https://example.invalid',apiKey='test-only-secret',extraBody={'thinking':{'type':'disabled'}},revision=0))
    requests = []
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        analysis = len(requests)==1
        assert body['thinking']=={'type':'enabled' if analysis else 'disabled'}
        assert 'tools' not in body and 'tool_choice' not in body
        assert all(m['role']=='user' for m in body['messages'])
        return httpx.Response(200,json={'id':'offline','object':'chat.completion','created':0,'model':'deepseek-flash',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'READY' if analysis else '{"answer":"READY"}',
            'reasoning_content':'private-reasoning-do-not-log' if analysis else None}}],
            'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30,'completion_tokens_details':{'reasoning_tokens':15 if analysis else 0}}})
    factory = build_model
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        def build(*args,**kwargs):
            model, metadata = factory(*args,**kwargs)
            model.client = openai.OpenAI(api_key='test-only-secret',base_url='https://example.invalid',http_client=transport).chat.completions
            return model, metadata
        monkeypatch.setattr(app_module,'build_model',build)
        with TestClient(create_app(settings)) as client:
            response = client.post('/models/deepseek/test')
    assert response.status_code==200, response.text
    assert response.json()['reasoningTokens']==15 and response.json()['reasoningObserved']
    assert 'private-reasoning' not in response.text and len(requests)==2


@pytest.mark.parametrize('provider,model', [('gpt','gpt-5.4'),('claude','claude-sonnet-4-6'),('claude','claude-haiku-4-5'),('gemini','gemini-3.1-pro'),('glm','glm-5.3')])
def test_provider_native_request_payload(provider,model,tmp_path):
    from assess_agent.settings import Settings
    from langchain_core.messages import HumanMessage
    settings = Settings(data_root=tmp_path)
    ModelConfigStore(tmp_path).save(provider,ModelConfigInput(model=model,baseUrl='https://example.invalid',
        apiKey='test-only-secret',revision=0))
    client,_ = build_model(settings,provider,stage='analysis')
    if provider=='claude':
        body = client._get_request_payload([HumanMessage(content='test')])
        assert body['thinking']['type']==('enabled' if 'haiku' in model else 'adaptive')
        if 'haiku' in model: assert body['thinking']['budget_tokens']<body['max_tokens']
    else:
        body = client._get_request_payload([HumanMessage(content='test')])
        if provider=='gpt': assert body['reasoning']=={'effort':'medium'} and not body['store']
        if provider=='gemini': assert body['reasoning_effort']=='medium'
        if provider=='glm': assert body['extra_body']['thinking']=={'type':'enabled'} and body['extra_body']['reasoning_effort']=='high'
    assert not body.get('tools') and not body.get('tool_choice')
