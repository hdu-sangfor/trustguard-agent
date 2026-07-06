import os

from tests.orchestrator_test_env import prepare_orchestrator_app_import

prepare_orchestrator_app_import()

from app.clients.llm_client import _load_provider_config


def _clear_llm_env(monkeypatch) -> None:
    keys = [
        "LLM_PROVIDER",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL_ID",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL_ID",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL_ID",
        "GEMINI_API_KEY",
        "GEMINI_BASE_URL",
        "GEMINI_MODEL_ID",
        "LOCAL_API_KEY",
        "LOCAL_BASE_URL",
        "LOCAL_MODEL_ID",
        "LLM_SECRETS_FILE",
    ]
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    # 不设置 LLM_SECRETS_FILE 时不应读取本地外部密钥文件，避免宿主机真实密钥污染断言。
    monkeypatch.delenv("LLM_SECRETS_FILE", raising=False)
    monkeypatch.setenv("LLM_SECRETS_OVERRIDE", "true")


def test_llm_provider_explicit_openai_compat(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://openai.local/v1")
    monkeypatch.setenv("OPENAI_MODEL_ID", "gpt-test")

    cfg = _load_provider_config()
    assert cfg.provider.value == "openai_compat"
    assert cfg.provider_source == "openai_compat"
    assert cfg.base_url == "http://openai.local/v1"
    assert cfg.model_id == "gpt-test"
    assert cfg.api_key == "sk-openai"


def test_llm_provider_explicit_anthropic(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anth")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anth.local")
    monkeypatch.setenv("ANTHROPIC_MODEL_ID", "claude-test")

    cfg = _load_provider_config()
    assert cfg.provider.value == "anthropic"
    assert cfg.provider_source == "anthropic"
    assert cfg.base_url == "https://anth.local"
    assert cfg.model_id == "claude-test"
    assert cfg.api_key == "sk-anth"


def test_llm_provider_auto_fallback_order(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://openai.local/v1")
    monkeypatch.setenv("OPENAI_MODEL_ID", "gpt-test")

    cfg = _load_provider_config()
    # auto 顺序里 anthropic 优先，但未配置时应落到 openai_compat
    assert cfg.provider.value == "openai_compat"
    assert cfg.provider_source == "openai_compat"
    assert cfg.api_key == "sk-openai"


def test_llm_secrets_env_overrides_existing_llm_env(monkeypatch, tmp_path):
    _clear_llm_env(monkeypatch)
    # 先模拟进程内残留旧值
    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("OPENAI_API_KEY", "stale-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://stale-openai.local/v1")
    monkeypatch.setenv("OPENAI_MODEL_ID", "stale-model")

    secrets_file = tmp_path / "llm.secrets.env"
    secrets_file.write_text(
        "\n".join(
            [
                "LLM_PROVIDER=anthropic",
                "ANTHROPIC_API_KEY=test-anthropic",
                "ANTHROPIC_BASE_URL=https://anthropic.local",
                "ANTHROPIC_MODEL_ID=claude-test",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_SECRETS_FILE", str(secrets_file))
    monkeypatch.setenv("LLM_SECRETS_OVERRIDE", "true")

    cfg = _load_provider_config()
    assert cfg.provider.value == "anthropic"
    assert cfg.provider_source == "anthropic"
    assert cfg.base_url == "https://anthropic.local"
    assert cfg.model_id == "claude-test"
    assert cfg.api_key == "test-anthropic"


def test_llm_secrets_not_loaded_without_explicit_file(monkeypatch, tmp_path):
    _clear_llm_env(monkeypatch)
    # 即使 cwd 上级存在历史 llm.secrets.env，也不再默认读取；用户配置统一走 .env。
    repo_root = tmp_path
    orch_dir = repo_root / "orchestrator"
    orch_dir.mkdir(parents=True, exist_ok=True)
    secrets_file = repo_root / "llm.secrets.env"
    secrets_file.write_text(
        "\n".join(
            [
                "LLM_PROVIDER=anthropic",
                "ANTHROPIC_API_KEY=test-parent-anthropic",
                "ANTHROPIC_BASE_URL=https://parent-anth.local",
                "ANTHROPIC_MODEL_ID=claude-parent",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(orch_dir)
    monkeypatch.delenv("LLM_SECRETS_FILE", raising=False)
    monkeypatch.setenv("LLM_SECRETS_OVERRIDE", "true")

    cfg = _load_provider_config()
    assert cfg.provider.value == "openai_compat"
    assert cfg.provider_source == "openai_compat"
    assert cfg.api_key == ""
