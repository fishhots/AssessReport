from pathlib import Path
import json
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPORT_CONFIG = json.loads((SERVICE_ROOT / "config/report.json").read_text(encoding="utf-8"))
CHAPTERS = [item["title"] for item in REPORT_CONFIG["chapters"]]
DIAGNOSIS_TITLES = [item["title"] for item in REPORT_CONFIG["diagnosis"]]
PROVIDERS = ("qwen", "deepseek", "glm", "gpt", "claude", "gemini")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASSESS_", env_file=SERVICE_ROOT / ".env", extra="ignore", validate_assignment=True)
    upload_root: Path = SERVICE_ROOT.parent / "backend/data/uploads"
    data_root: Path = SERVICE_ROOT / "data"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    service_token: SecretStr = SecretStr("")
    model_timeout: int = Field(default=90, ge=5, le=600)
    relay_model_timeout: int = Field(default=600, ge=90, le=1200)
    relay_test_timeout: int = Field(default=60, ge=5, le=90)
    job_timeout: int = Field(default=3600, ge=30, le=14400)
    max_model_calls: int = Field(default=120, ge=1, le=300)
    analysis_output_tokens: int = Field(default=16000, ge=4096, le=32768)
    max_output_tokens: int = Field(default=12000, ge=512, le=16000)
    update_word_fields: bool = True
    max_source_chars: int = Field(default=8_000_000, ge=1000)
    max_table_rows: int = Field(default=100000, ge=100)

    def provider_config(self, provider: str) -> dict:
        from .model_config import ModelConfigStore
        return ModelConfigStore(self.data_root).runtime(provider)

    def jobs_root(self) -> Path:
        return self.data_root / "jobs"
