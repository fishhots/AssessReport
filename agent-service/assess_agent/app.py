from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from hashlib import sha256
import hmac
import logging
import shutil
import traceback
from pathlib import Path
from threading import Lock
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import tool
from langsmith import tracing_context

from .errors import AgentError, safe_model_error
from .models import build_model, provider_status, validate_provider
from .model_config import ModelConfigInput, ModelConfigStore
from .parsers import source_path
from .schemas import JobRequest
from .settings import Settings
from .store import JobStore
from .workflow import run_workflow
from .export_resume import export_snapshot_directory

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, model=None) -> FastAPI:
    settings = settings or Settings()
    jobs = JobStore(settings.data_root)
    model_configs = ModelConfigStore(settings.data_root)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="report-agent")
    futures, future_lock = {}, Lock()

    @asynccontextmanager
    async def lifespan(app):
        jobs.recover_interrupted()
        yield
        executor.shutdown(wait=True)

    app = FastAPI(title="教学质量报告智能体服务", version="0.1.0", lifespan=lifespan)
    app.state.jobs, app.state.settings = jobs, settings

    def authorize(x_agent_token: str = Header(default="")):
        expected = settings.service_token.get_secret_value()
        if expected and not hmac.compare_digest(x_agent_token, expected):
            raise AgentError("UNAUTHORIZED", "智能体服务认证失败", 401)

    @app.exception_handler(AgentError)
    async def handle_error(request: Request, error: AgentError):
        return JSONResponse(status_code=error.status_code, content={"code": error.code, "message": error.message})

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, error: RequestValidationError):
        is_config = request.url.path.startswith("/models/")
        return JSONResponse(status_code=422, content={"code": "MODEL_CONFIG" if is_config else "INVALID_REQUEST",
            "message": "请检查模型名称、密钥、接口地址和额外参数" if is_config else "任务参数不符合约定，请检查学校、年份、章节和资料 ID"})

    def preflight(request: JobRequest):
        if model is None:
            validate_provider(settings, request.model_id)
        for material in request.materials:
            source_path(settings, material)

    def execute(row):
        task_id, attempt = row["id"], row["attempt"]
        try:
            jobs.ensure_active(task_id, attempt)
            request = JobRequest.model_validate_json(row["request"])
            with tracing_context(enabled=False):
                result = run_workflow(request, settings, jobs, attempt, model=model)
            jobs.finish(task_id, attempt, result)
        except AgentError as error:
            if error.code == 'MODEL_TIMEOUT':
                error = AgentError(error.code, error.message + '。任务进度已保存，可继续生成')
            jobs.fail(task_id, attempt, error)
            if error.code == "JOB_CANCELLED":
                logger.info("Task %s attempt %s cancelled", task_id, attempt)
            else:
                logger.warning("Task %s attempt %s failed: %s", task_id, attempt, error.code)
        except Exception as error:
            jobs.fail(task_id, attempt, AgentError("WORKFLOW_ERROR", "任务执行失败，请检查资料或重试"))
            frames = ';'.join(f'{Path(f.filename).name}:{f.lineno}:{f.name}'
                              for f in traceback.extract_tb(error.__traceback__)[-5:])
            # Record diagnostic locations, never raw exception messages or data.
            logger.error("Task %s attempt %s failed: WORKFLOW_ERROR type=%s frames=%s",
                         task_id, attempt, type(error).__name__, frames)

    def enqueue(row):
        key = (row["id"], row["attempt"])
        future = executor.submit(execute, row)
        with future_lock:
            futures[key] = future
        def completed(_):
            with future_lock:
                futures.pop(key, None)
        future.add_done_callback(completed)

    @app.get("/health")
    def health():
        return {"status": "UP", "framework": "langchain", "version": "0.1.0"}

    @app.get("/models", dependencies=[Depends(authorize)])
    def models():
        return provider_status(settings)

    @app.get("/models/{provider}/config", dependencies=[Depends(authorize)])
    def model_config(provider: str, channel: str | None = None):
        return model_configs.public(provider, channel)

    @app.put("/models/{provider}/config", dependencies=[Depends(authorize)])
    def save_model_config(provider: str, config: ModelConfigInput):
        return model_configs.save(provider, config)

    @app.post("/models/{provider}/test", dependencies=[Depends(authorize)])
    def test_model(provider: str):
        stage = "configuration"
        try:
            client, metadata = build_model(settings.model_copy(update={"max_output_tokens": 512, "model_timeout": min(settings.model_timeout, 30)}), provider, 0, 'test')
            if metadata.get('analysisEnabled') or metadata.get('toolFree'):
                from .reasoning import answer_text
                from langchain_core.messages import HumanMessage
                import json
                capabilities = ['structuredOutput']
                reasoning_tokens = 0
                reasoning_observed = False
                with tracing_context(enabled=False):
                    if metadata.get('analysisEnabled'):
                        stage = 'analysis'
                        analysis, _ = build_model(settings.model_copy(update={'analysis_output_tokens': 4096,
                            'model_timeout': min(settings.model_timeout, 30)}), provider, max_retries=0, stage='analysis-test')
                        result = analysis.invoke([HumanMessage(content='连接测试，只回答 READY。')])
                        if 'READY' not in answer_text(result):
                            raise AgentError('MODEL_TEST', '模型深度分析连接测试未通过，请重试')
                        reasoning_tokens = (result.usage_metadata or {}).get('output_token_details', {}).get('reasoning', 0)
                        reasoning_observed = bool(result.response_metadata.get('reasoningObserved') or reasoning_tokens)
                        capabilities.append('reasoning')
                    stage = 'structured_output'
                    result = client.invoke([HumanMessage(content='连接测试。只返回 JSON 对象：{"answer":"READY"}，不要输出其他内容。')])
                    text = answer_text(result).strip()
                    if text.startswith('```'):
                        text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
                    try:
                        parsed = json.loads(text)
                    except ValueError as exc:
                        raise AgentError('MODEL_STRUCTURE', '模型未返回有效的 JSON 结构，请重试') from exc
                    if not isinstance(parsed, dict) or parsed.get('answer') != 'READY':
                        raise AgentError('MODEL_TEST', '模型未通过结构化输出测试')
                return {'status': 'OK', 'capabilities': capabilities, 'reasoningTokens': reasoning_tokens,
                        'reasoningObserved': reasoning_observed, **metadata}
            called = []

            @tool
            def connection_echo(value: str) -> dict:
                """用于接口测试，原样返回固定测试文本。"""
                called.append(value)
                return {"value": value}

            class ConnectionResult(BaseModel):
                answer: str

            agent = create_agent(model=client, tools=[connection_echo],
                response_format=ToolStrategy(ConnectionResult, handle_errors=False),
                system_prompt="这是工具调用和结构化输出测试。必须先调用 connection_echo，value 为 READY，再返回 answer 为 READY 的结构化结果。")
            stage = "tool_calling"
            with tracing_context(enabled=False):
                response = agent.invoke({"messages": [{"role": "user", "content": "开始固定测试"}]}, config={"recursion_limit": 8})
            stage = "structured_output"
            structured = response.get("structured_response")
            if "READY" not in called or not structured or structured.answer != "READY":
                raise AgentError("MODEL_TEST", "模型未通过工具调用与结构化输出测试")
            return {"status": "OK", "capabilities": ["toolCalling", "structuredOutput"], **metadata}
        except AgentError as error:
            logger.warning("Model connection test failed: stage=%s category=%s", stage, error.code)
            raise
        except Exception as error:
            failure = safe_model_error(error)
            logger.warning("Model connection test failed: stage=%s category=%s", stage, failure.code)
            raise failure from error

    @app.post("/jobs", status_code=202, dependencies=[Depends(authorize)])
    def submit(request: JobRequest):
        preflight(request)
        row, created = jobs.create(request)
        if created:
            enqueue(row)
        return jobs.view(row)

    @app.get("/jobs", dependencies=[Depends(authorize)])
    def list_jobs():
        return jobs.list()

    @app.get("/jobs/{task_id}", dependencies=[Depends(authorize)])
    def job(task_id: UUID):
        return jobs.view(jobs.get(str(task_id)))

    @app.post("/jobs/{task_id}/retry", status_code=202, dependencies=[Depends(authorize)])
    def retry(task_id: UUID):
        existing = jobs.get(str(task_id))
        request = JobRequest.model_validate_json(existing['request'])
        export_only = export_snapshot_directory(request, settings.jobs_root(), existing['attempt'] + 1) is not None
        if not export_only:
            preflight(request)
        row = jobs.retry(str(task_id), export_only=export_only)
        enqueue(row)
        return jobs.view(row)

    @app.post("/jobs/{task_id}/cancel", dependencies=[Depends(authorize)])
    def cancel(task_id: UUID):
        row = jobs.cancel(str(task_id))
        with future_lock:
            future = futures.get((str(task_id), row["attempt"]))
        if future:
            future.cancel()
        return jobs.view(row)

    @app.delete("/jobs/{task_id}", status_code=204, dependencies=[Depends(authorize)])
    def delete_job(task_id: UUID):
        root = settings.jobs_root().resolve()
        directory = (root / str(task_id)).resolve()
        if directory.parent != root or directory.name != str(task_id):
            raise AgentError("JOB_DELETE", "报告缓存路径无效", 409)
        jobs.delete(str(task_id))
        if directory.exists():
            try:
                shutil.rmtree(directory)
            except OSError:
                logger.warning("Report cache cleanup deferred: task=%s", task_id)
        return Response(status_code=204)

    @app.get("/jobs/{task_id}/artifacts/{kind}", dependencies=[Depends(authorize)])
    def artifact(task_id: UUID, kind: str):
        if kind not in ("report", "diagnosis"):
            raise AgentError("ARTIFACT_KIND", "输出类型无效", 404)
        row = jobs.get(str(task_id))
        if row["status"] != "SUCCEEDED":
            raise AgentError("ARTIFACT_PENDING", "输出尚未完成", 409)
        path = settings.jobs_root() / str(task_id) / f"attempt-{row['attempt']}" / f"{kind}.docx"
        if not path.is_file():
            raise AgentError("ARTIFACT_MISSING", "输出文件不存在，请重新执行", 404)
        result = jobs.view(row)["result"]
        expected = result["artifacts"][kind]["sha256"]
        if sha256(path.read_bytes()).hexdigest() != expected:
            raise AgentError("ARTIFACT_CHANGED", "输出文件已改变，请重新执行", 409)
        return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=f"{kind}.docx")

    return app


app = create_app()
