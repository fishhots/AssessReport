import json
from importlib import import_module
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from assess_agent.app import create_app
from assess_agent.errors import AgentError
from assess_agent.model_config import ModelConfigInput, ModelConfigStore
from assess_agent.models import build_model
from assess_agent.settings import PROVIDERS, Settings


def payload(**changes):
    return {"model": "user-chosen-model", "baseUrl": "https://example.invalid/v1",
            "apiKey": "test-only-secret-value", "extraBody": {}, "revision": 0, **changes}


@pytest.mark.parametrize("provider", PROVIDERS)
def test_save_masked_read_restart_and_runtime_factory(tmp_path, provider, monkeypatch):
    settings = Settings(data_root=tmp_path)
    store = ModelConfigStore(tmp_path)
    assert store.public(provider)["hasApiKey"] is False
    result = store.save(provider, ModelConfigInput.model_validate(payload()))
    assert result["configured"] and result["hasApiKey"] and result["revision"] == 1
    assert "test-only-secret-value" not in json.dumps(result) and "apiKey" not in result
    assert b"test-only-secret-value" not in store.database.read_bytes()
    # A new service/store reads persisted values; environment variables no longer override the UI.
    monkeypatch.setenv(provider.upper() + "_API_KEY", "old-env-key")
    monkeypatch.setenv(provider.upper() + "_MODEL", "old-env-model")
    model, metadata = build_model(settings, provider)
    assert metadata['provider'] == provider and metadata['model'] == 'user-chosen-model'
    assert metadata['reasoningMode'] == 'auto' and not metadata['analysisEnabled']
    assert ModelConfigStore(tmp_path).runtime(provider)["key"] == "test-only-secret-value"
    if provider == "claude":
        assert str(model.anthropic_api_url).rstrip("/") == "https://example.invalid/v1"
        assert model.anthropic_api_key.get_secret_value() == "test-only-secret-value"
    else:
        assert str(model.openai_api_base).rstrip("/") == "https://example.invalid/v1"
        assert model.openai_api_key.get_secret_value() == "test-only-secret-value"


def test_blank_key_preserves_replacement_and_concurrency(tmp_path):
    store = ModelConfigStore(tmp_path)
    store.save("qwen", ModelConfigInput.model_validate(payload()))
    result = store.save("qwen", ModelConfigInput.model_validate(payload(apiKey="   ", model="changed", revision=1)))
    assert result["revision"] == 2
    assert store.runtime("qwen")["key"] == "test-only-secret-value"
    with pytest.raises(AgentError) as failure:
        store.save("qwen", ModelConfigInput.model_validate(payload(apiKey="overwritten", revision=1)))
    assert failure.value.code == "MODEL_CONFIG_CONFLICT"
    assert store.runtime("qwen")["model"] == "changed"
    store.save("qwen", ModelConfigInput.model_validate(payload(apiKey="new-test-secret", revision=2)))
    assert store.runtime("qwen")["key"] == "new-test-secret"
    assert not store.public("deepseek")["configured"]


def test_http_config_contract_auth_errors_and_no_credential_echo(tmp_path):
    settings = Settings(data_root=tmp_path, service_token="test-internal-token")
    headers = {"X-Agent-Token": "test-internal-token"}
    with TestClient(create_app(settings)) as client:
        assert client.get("/models/qwen/config").status_code == 401
        assert client.put("/models/qwen/config", json=payload()).status_code == 401
        assert client.get("/models/unknown/config", headers=headers).status_code == 404
        result = client.put("/models/qwen/config", json=payload(), headers=headers)
        assert result.status_code == 200
        assert "test-only-secret-value" not in result.text
        assert "test-only-secret-value" not in client.get("/models/qwen/config", headers=headers).text
        assert client.get("/models", headers=headers).json()[0]["configured"]
        for changes in (
            {"baseUrl": "https://user:password@example.invalid/v1"},
            {"baseUrl": "https://example.invalid/v1?api_key=test-only-secret-value"},
            {"baseUrl": "file:///tmp/model"}, {"baseUrl": "https://example.invalid:invalid/v1"},
            {"model": " "}, {"apiKey": "test-only-secret-value\nheader"},
            {"extraBody": []}, {"extraBody": {"headers": {"Authorization": "test-only-secret-value"}}},
            {"extraBody": {"field": "x" * 17000}}, {"revision": -1},
        ):
            response = client.put("/models/qwen/config", json=payload(**{"revision": 1, **changes}), headers=headers)
            assert response.status_code == 422
            assert "test-only-secret-value" not in response.text
        invalid = client.put("/models/qwen/config", content='{"apiKey":"test-only-secret-value",', headers={**headers, "Content-Type": "application/json"})
        assert invalid.status_code == 422 and "test-only-secret-value" not in invalid.text
        response = client.put("/models/qwen/config", json=payload(), headers=headers)
        assert response.status_code == 409
        initial = payload(apiKey=None)
        response = client.put("/models/deepseek/config", json=initial, headers=headers)
        assert response.status_code == 422 and not client.get("/models/deepseek/config", headers=headers).json()["configured"]


def test_missing_encryption_file_does_not_silently_replace_key(tmp_path):
    store = ModelConfigStore(tmp_path)
    store.save("qwen", ModelConfigInput.model_validate(payload()))
    original = store.key_path.read_bytes()
    store.key_path.unlink()
    with pytest.raises(AgentError) as failure:
        store.runtime("qwen")
    assert failure.value.code == "MODEL_KEY_ERROR" and not store.key_path.exists()
    store.key_path.write_bytes(original)
    assert store.runtime("qwen")["key"] == "test-only-secret-value"


def test_connection_test_reads_latest_saved_configuration_without_network(tmp_path, monkeypatch):
    app_module = import_module("assess_agent.app")
    observed = []

    def offline_connection_agent(*, model, tools, **kwargs):
        observed.append({"model": model.model_name, "url": model.openai_api_base,
                         "key": model.openai_api_key.get_secret_value()})

        class OfflineConnectionAgent:
            def invoke(self, *args, **kwargs):
                tools[0].invoke({"value": "READY"})
                return {"structured_response": SimpleNamespace(answer="READY")}
        return OfflineConnectionAgent()

    monkeypatch.setattr(app_module, "create_agent", offline_connection_agent)
    with TestClient(create_app(Settings(data_root=tmp_path))) as client:
        client.put("/models/qwen/config", json=payload())
        result = client.post("/models/qwen/test")
        assert result.status_code == 200 and "test-only-secret-value" not in result.text
        client.put("/models/qwen/config", json=payload(model="updated-model", baseUrl="http://127.0.0.1:9999/v1", apiKey="updated-test-key", revision=1))
        assert client.post("/models/qwen/test").status_code == 200
    assert observed[0]["model"] == "user-chosen-model"
    assert observed[1] == {"model": "updated-model", "url": "http://127.0.0.1:9999/v1", "key": "updated-test-key"}
