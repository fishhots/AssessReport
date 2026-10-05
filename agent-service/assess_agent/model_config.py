"""Editable provider configuration. API responses never contain decrypted credentials."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Literal
import sqlite3
from threading import RLock
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .errors import AgentError
from .settings import PROVIDERS
from .provider_routes import default_channel, default_base_url, validate_channel

_key_lock = RLock()


class ModelConfigInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    model: str = Field(min_length=1, max_length=512)
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=2048)
    api_key: SecretStr | None = Field(default=None, alias="apiKey", max_length=4096)
    extra_body: dict = Field(default_factory=dict, alias="extraBody")
    revision: int = Field(ge=0)
    channel: Literal['official', 'zhizengzeng'] = 'official'
    reasoning_mode: Literal['auto', 'on', 'off'] = Field(default='auto', alias='reasoningMode')
    reasoning_effort: Literal['low', 'medium', 'high'] = Field(default='medium', alias='reasoningEffort')
    thinking_budget: int = Field(default=4096, ge=1024, le=16000, alias='thinkingBudget')

    @field_validator("model", "base_url")
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("名称或地址格式无效")
        return value

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        try:
            url = urlsplit(value)
            if (url.scheme not in ("http", "https") or not url.hostname or url.username
                    or url.password or url.query or url.fragment or any(char.isspace() for char in value)):
                raise ValueError()
            _ = url.port
        except ValueError:
            raise ValueError("请填写有效的接口基础地址，不包含密钥或查询参数") from None
        return value.rstrip("/")

    @field_validator("api_key")
    @classmethod
    def clean_key(cls, value):
        if value is None:
            return None
        key = value.get_secret_value().strip()
        if not key:
            return None
        if any(ord(char) < 32 or char.isspace() for char in key):
            raise ValueError("密钥不能包含空白或控制字符")
        return SecretStr(key)

    @field_validator("extra_body")
    @classmethod
    def valid_extra(cls, value):
        def check(item):
            if isinstance(item, dict):
                for name, content in item.items():
                    normalized = name.lower().replace("-", "").replace("_", "")
                    if normalized in ("apikey", "authorization", "accesstoken", "secretkey", "password"):
                        raise ValueError("密钥请填写在 API Key 字段中")
                    check(content)
            elif isinstance(item, list):
                for content in item:
                    check(content)
        try:
            check(value)
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (RecursionError, TypeError, OverflowError):
            raise ValueError("额外参数格式无效") from None
        if len(encoded.encode("utf-8")) > 16384:
            raise ValueError("额外参数不能超过 16 KB")
        return value


class ModelConfigStore:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.database = root / "model-config.sqlite3"
        self.key_path = root / "model-config.key"
        with closing(self.connect()) as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("""CREATE TABLE IF NOT EXISTS models (
                provider TEXT PRIMARY KEY, model TEXT NOT NULL, base_url TEXT NOT NULL,
                key_ciphertext TEXT NOT NULL, extra_body TEXT NOT NULL,
                revision INTEGER NOT NULL, updated_at TEXT NOT NULL)""")
            if 'reasoning' not in {row[1] for row in connection.execute('PRAGMA table_info(models)')}:
                connection.execute("ALTER TABLE models ADD COLUMN reasoning TEXT NOT NULL DEFAULT '{}'")
            if 'channel' not in {row[1] for row in connection.execute('PRAGMA table_info(models)')}:
                connection.execute("ALTER TABLE models ADD COLUMN channel TEXT NOT NULL DEFAULT 'official'")
            connection.execute("""CREATE TABLE IF NOT EXISTS model_profiles (
                provider TEXT NOT NULL, channel TEXT NOT NULL, model TEXT NOT NULL, base_url TEXT NOT NULL,
                key_ciphertext TEXT NOT NULL, extra_body TEXT NOT NULL, reasoning TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(provider,channel))""")
            # Preserve every pre-existing configuration as its official profile.
            # The encrypted credential is copied as-is; no keys are decrypted.
            connection.execute("""INSERT OR IGNORE INTO model_profiles
                (provider,channel,model,base_url,key_ciphertext,extra_body,reasoning,updated_at)
                SELECT provider,channel,model,base_url,key_ciphertext,extra_body,reasoning,updated_at FROM models""")
            connection.commit()

    def connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def validate_provider(provider):
        if provider not in PROVIDERS:
            raise AgentError("MODEL_PROVIDER", "不支持的模型系列", 404)

    def cipher(self):
        with _key_lock:
            if not self.key_path.exists():
                with closing(self.connect()) as connection:
                    if connection.execute("SELECT 1 FROM models UNION ALL SELECT 1 FROM model_profiles LIMIT 1").fetchone():
                        raise AgentError("MODEL_KEY_ERROR", "模型密钥文件缺失，请恢复配置备份", 503)
                try:
                    with open(os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                        output.write(Fernet.generate_key())
                except FileExistsError:
                    pass
            try:
                return Fernet(self.key_path.read_bytes())
            except (OSError, ValueError):
                raise AgentError("MODEL_KEY_ERROR", "模型密钥文件读取失败", 503) from None

    def row(self, provider, channel=None):
        self.validate_provider(provider)
        with closing(self.connect()) as connection:
            if channel is not None:
                validate_channel(provider, channel)
                return connection.execute("SELECT * FROM model_profiles WHERE provider=? AND channel=?", (provider,channel)).fetchone()
            return connection.execute("SELECT * FROM models WHERE provider=?", (provider,)).fetchone()

    def public(self, provider, channel=None):
        active = self.row(provider)
        active_channel = active['channel'] if active else default_channel(provider)
        channel = channel or active_channel
        validate_channel(provider, channel)
        row = self.row(provider, channel)
        return {
            "id": provider, "model": row["model"] if row else "",
            "channel": channel, "activeChannel": active_channel,
            "baseUrl": row["base_url"] if row else default_base_url(provider,channel),
            "hasApiKey": bool(row and row["key_ciphertext"]),
            "keyMasked": "••••••••" if row else "",
            "extraBody": json.loads(row["extra_body"]) if row else {},
            # A provider-wide revision protects switching against stale dialogs.
            "configured": bool(row), "revision": active["revision"] if active else 0,
            "updatedAt": row["updated_at"] if row else None,
            **self.reasoning(row),
        }

    @staticmethod
    def reasoning(row):
        saved = json.loads(row['reasoning']) if row else {}
        return {'reasoningMode': saved.get('mode', 'auto'), 'reasoningEffort': saved.get('effort', 'medium'),
                'thinkingBudget': saved.get('budget', 4096)}

    def runtime(self, provider):
        row = self.row(provider)
        key = ""
        if row:
            try:
                key = self.cipher().decrypt(row["key_ciphertext"].encode()).decode()
            except InvalidToken:
                raise AgentError("MODEL_KEY_ERROR", "模型密钥读取失败，请检查配置备份", 503) from None
        return {"id": provider, "key": key, "model": row["model"] if row else "",
                "channel": row['channel'] if row else default_channel(provider),
                "base_url": row["base_url"] if row else "",
                "extra_body": row["extra_body"] if row else "{}", **self.reasoning(row)}

    def save(self, provider, config: ModelConfigInput):
        self.validate_provider(provider)
        validate_channel(provider,config.channel)
        # Initialize the cipher before opening the write transaction.
        cipher = self.cipher()
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM models WHERE provider=?", (provider,)).fetchone()
            revision = row["revision"] if row else 0
            if config.revision != revision:
                raise AgentError("MODEL_CONFIG_CONFLICT", "配置已更新，请重新打开后修改", 409)
            profile = connection.execute("SELECT * FROM model_profiles WHERE provider=? AND channel=?", (provider,config.channel)).fetchone()
            key = config.api_key.get_secret_value() if config.api_key else ""
            if not key and not profile:
                raise AgentError("MODEL_CONFIG", "首次配置此调用方式请填写对应的 API Key", 422)
            ciphertext = cipher.encrypt(key.encode()).decode() if key else profile["key_ciphertext"]
            updated_at=datetime.now(timezone.utc).isoformat()
            extra=json.dumps(config.extra_body, ensure_ascii=False, allow_nan=False)
            reasoning=json.dumps({'mode': config.reasoning_mode, 'effort': config.reasoning_effort, 'budget': config.thinking_budget})
            connection.execute("""INSERT INTO model_profiles (provider,channel,model,base_url,key_ciphertext,extra_body,reasoning,updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(provider,channel) DO UPDATE SET
                model=excluded.model, base_url=excluded.base_url, key_ciphertext=excluded.key_ciphertext,
                extra_body=excluded.extra_body, reasoning=excluded.reasoning, updated_at=excluded.updated_at""",
                (provider,config.channel,config.model,config.base_url,ciphertext,extra,reasoning,updated_at))
            connection.execute("""INSERT INTO models (provider,model,base_url,key_ciphertext,extra_body,revision,updated_at,reasoning,channel) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET model=excluded.model, base_url=excluded.base_url,
                key_ciphertext=excluded.key_ciphertext, extra_body=excluded.extra_body,
                revision=excluded.revision, updated_at=excluded.updated_at, reasoning=excluded.reasoning, channel=excluded.channel""",
                (provider, config.model, config.base_url, ciphertext,
                 extra, revision + 1, updated_at, reasoning, config.channel))
        return self.public(provider)
