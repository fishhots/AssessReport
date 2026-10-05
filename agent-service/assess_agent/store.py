from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import sqlite3

from .errors import AgentError
from .schemas import JobRequest

STEP_NAMES = ["资料上传与任务配置", "资料解析与证据整理", "模板与 Skill 加载", "培养质量报告撰写", "个性化与主题报告", "内容校验与修订", "Word 导出"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initial_steps() -> list[dict]:
    return [{"name": name, "status": "COMPLETED" if index == 0 else "WAITING"}
            for index, name in enumerate(STEP_NAMES)]


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class JobStore:
    def __init__(self, data_root: Path):
        data_root.mkdir(parents=True, exist_ok=True)
        self.path = data_root / "jobs.sqlite3"
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request TEXT NOT NULL, fingerprint TEXT NOT NULL,
                status TEXT NOT NULL, attempt INTEGER NOT NULL, steps TEXT NOT NULL,
                message TEXT NOT NULL, error_code TEXT, result TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        return connection

    def get(self, task_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise AgentError("JOB_NOT_FOUND", "执行任务不存在", 404)
        return dict(row)

    def view(self, row: dict) -> dict:
        return {"taskId": row["id"], "status": row["status"], "attempt": row["attempt"],
                "steps": json.loads(row["steps"]), "message": row["message"], "errorCode": row["error_code"],
                "result": json.loads(row["result"]) if row["result"] else None,
                "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    def list(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 1000").fetchall()
        return [self.view(dict(row)) for row in rows]

    def create(self, request: JobRequest) -> tuple[dict, bool]:
        data = json.dumps(request.model_dump(mode="json", by_alias=True), ensure_ascii=False, sort_keys=True)
        fingerprint = sha256(data.encode()).hexdigest()
        timestamp = now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM jobs WHERE id=?", (str(request.task_id),)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise AgentError("JOB_CONFLICT", "同一任务 ID 的资料或配置不一致", 409)
                return dict(existing), False
            active = connection.execute("SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING','CANCELLING')").fetchone()[0]
            if active >= 8:
                raise AgentError("QUEUE_FULL", "任务队列已满，请稍后再试", 429)
            connection.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (str(request.task_id), data, fingerprint, "QUEUED", 1,
                                json.dumps(initial_steps(), ensure_ascii=False), "任务已排队", None, None,
                                timestamp, timestamp))
        return self.get(str(request.task_id)), True

    def retry(self, task_id: str, export_only=False) -> dict:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute("SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING','CANCELLING')").fetchone()[0]
            if active >= 8:
                raise AgentError("QUEUE_FULL", "任务队列已满，请稍后再试", 429)
            steps = initial_steps()
            if export_only:
                for step in steps[:-1]:
                    step['status'] = 'COMPLETED'
            message = '正文已保存，已排队继续导出 Word' if export_only else '任务已排队，将检查已保存内容并继续生成'
            cursor = connection.execute("""UPDATE jobs SET status='QUEUED', attempt=attempt+1, steps=?,
                message=?, error_code=NULL, result=NULL, updated_at=?
                WHERE id=? AND status IN ('FAILED','INTERRUPTED','CANCELLED')""",
                (json.dumps(steps, ensure_ascii=False), message, now(), task_id))
            if cursor.rowcount != 1:
                raise AgentError("JOB_RETRY", "仅失败、中断或已终止的任务可以重试", 409)
        return self.get(task_id)

    def phase(self, task_id: str, attempt: int, index: int, status: str):
        self.ensure_active(task_id, attempt)
        row = self.get(task_id)
        steps = json.loads(row["steps"])
        steps[index]["status"] = status
        with self.connect() as connection:
            cursor = connection.execute("UPDATE jobs SET status='RUNNING',steps=?,message=?,updated_at=? WHERE id=? AND attempt=? AND status IN ('QUEUED','RUNNING')",
                               (json.dumps(steps, ensure_ascii=False), STEP_NAMES[index], now(), task_id, attempt))
        if cursor.rowcount != 1:
            raise AgentError("JOB_CANCELLED", "任务已终止", 409)

    def finish(self, task_id: str, attempt: int, result: dict):
        with self.connect() as connection:
            message = "完整报告已生成，部分内容未完成，详见文内资料缺口说明" if result.get("contentGaps") else "完整报告已生成"
            if result.get('artifacts', {}).get('report', {}).get('layout', {}).get('status') == 'manualUpdateRequired':
                message += '；目录页码需在 Word 中更新'
            cursor = connection.execute("UPDATE jobs SET status='SUCCEEDED',result=?,message=?,updated_at=? WHERE id=? AND attempt=? AND status IN ('QUEUED','RUNNING')",
                               (json.dumps(result, ensure_ascii=False), message, now(), task_id, attempt))
        if cursor.rowcount != 1:
            self.complete_cancel(task_id, attempt)

    def fail(self, task_id: str, attempt: int, error: AgentError):
        try:
            row = self.get(task_id)
        except AgentError:
            return
        if row["attempt"] != attempt or row["status"] not in ("QUEUED", "RUNNING", "CANCELLING"):
            return
        if row["status"] == "CANCELLING" or error.code == "JOB_CANCELLED":
            self.complete_cancel(task_id, attempt)
            return
        steps = json.loads(row["steps"])
        for step in steps:
            if step["status"] == "RUNNING":
                step["status"] = "FAILED"
        with self.connect() as connection:
            cursor = connection.execute("UPDATE jobs SET status='FAILED',steps=?,error_code=?,message=?,updated_at=? WHERE id=? AND attempt=? AND status IN ('QUEUED','RUNNING')",
                               (json.dumps(steps, ensure_ascii=False), error.code, error.message, now(), task_id, attempt))
        if cursor.rowcount != 1:
            self.complete_cancel(task_id, attempt)

    def ensure_active(self, task_id: str, attempt: int):
        with self.connect() as connection:
            row = connection.execute("SELECT attempt,status FROM jobs WHERE id=?", (task_id,)).fetchone()
        if row is None or row["attempt"] != attempt or row["status"] not in ("QUEUED", "RUNNING"):
            raise AgentError("JOB_CANCELLED", "任务已终止", 409)

    def cancel(self, task_id: str) -> dict:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise AgentError("JOB_NOT_FOUND", "执行任务不存在", 404)
            if row["status"] in ("CANCELLING", "CANCELLED"):
                return dict(row)
            if row["status"] not in ("QUEUED", "RUNNING"):
                raise AgentError("JOB_CANCEL", "当前任务已经结束，无需终止", 409)
            queued = row["status"] == "QUEUED"
            status = "CANCELLED" if queued else "CANCELLING"
            steps = json.loads(row["steps"])
            for step in steps:
                if step["status"] == "RUNNING" or queued and step["status"] == "WAITING":
                    step["status"] = status
            connection.execute("UPDATE jobs SET status=?,steps=?,message=?,error_code=?,updated_at=? WHERE id=?",
                (status, json.dumps(steps, ensure_ascii=False), "任务已终止，可重新执行" if queued else "正在终止，等待当前请求结束",
                 "JOB_CANCELLED" if queued else None, now(), task_id))
        return self.get(task_id)

    def complete_cancel(self, task_id: str, attempt: int):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT steps FROM jobs WHERE id=? AND attempt=? AND status='CANCELLING'", (task_id, attempt)).fetchone()
            if row is None:
                return
            steps = json.loads(row["steps"])
            for step in steps:
                if step["status"] in ("RUNNING", "CANCELLING", "WAITING"):
                    step["status"] = "CANCELLED"
            connection.execute("UPDATE jobs SET status='CANCELLED',steps=?,message='任务已终止，可重新执行',error_code='JOB_CANCELLED',result=NULL,updated_at=? WHERE id=? AND attempt=? AND status='CANCELLING'",
                (json.dumps(steps, ensure_ascii=False), now(), task_id, attempt))

    def delete(self, task_id: str):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM jobs WHERE id=?", (task_id,)).fetchone()
            if row and row["status"] in ("QUEUED", "RUNNING", "CANCELLING"):
                raise AgentError("JOB_ACTIVE", "请先终止任务，再删除报告记录", 409)
            connection.execute("DELETE FROM jobs WHERE id=?", (task_id,))

    def recover_interrupted(self):
        with self.connect() as connection:
            rows = connection.execute("SELECT id,steps,status FROM jobs WHERE status IN ('QUEUED','RUNNING','CANCELLING')").fetchall()
            for row in rows:
                cancelled = row["status"] == "CANCELLING"
                steps = json.loads(row["steps"])
                for step in steps:
                    if step["status"] in ("RUNNING", "CANCELLING") or cancelled and step["status"] == "WAITING":
                        step["status"] = "CANCELLED" if cancelled else "INTERRUPTED"
                connection.execute("UPDATE jobs SET status=?,steps=?,message=?,error_code=?,updated_at=? WHERE id=?",
                    ("CANCELLED" if cancelled else "INTERRUPTED", json.dumps(steps, ensure_ascii=False),
                     "任务已终止，可重新执行" if cancelled else "执行服务已重启，可重新执行",
                     "JOB_CANCELLED" if cancelled else "INTERRUPTED", now(), row["id"]))
