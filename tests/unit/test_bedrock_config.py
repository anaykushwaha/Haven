from __future__ import annotations

import pytest

from aws.bedrock.config import BedrockConfig, load_bedrock_config
from haven.config import HavenConfig, get_config, normalize_backend


def test_defaults_when_environment_is_empty() -> None:
    config = load_bedrock_config({})
    assert config.region == "us-west-2"
    assert config.model_id == "global.anthropic.claude-sonnet-4-6"
    assert config.temperature == 0.2
    assert config.max_tokens == 2048


def test_environment_overrides() -> None:
    config = load_bedrock_config(
        {
            "AWS_REGION": "us-east-1",
            "HAVEN_BEDROCK_MODEL_ID": "some.model",
            "HAVEN_BEDROCK_TEMPERATURE": "0.7",
            "HAVEN_BEDROCK_MAX_TOKENS": "512",
        }
    )
    assert config == BedrockConfig("us-east-1", "some.model", 0.7, 512)


def test_region_fallback_to_aws_default_region() -> None:
    assert load_bedrock_config({"AWS_DEFAULT_REGION": "eu-west-1"}).region == "eu-west-1"


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({"AWS_REGION": "   "}, None),  # blank -> default, not an error
        ({"HAVEN_BEDROCK_TEMPERATURE": "hot"}, "valid float"),
        ({"HAVEN_BEDROCK_MAX_TOKENS": "many"}, "valid int"),
        ({"HAVEN_BEDROCK_TEMPERATURE": "1.5"}, "temperature"),
        ({"HAVEN_BEDROCK_TEMPERATURE": "-0.1"}, "temperature"),
        ({"HAVEN_BEDROCK_MAX_TOKENS": "0"}, "max_tokens"),
    ],
)
def test_invalid_values(env: dict[str, str], match: str | None) -> None:
    if match is None:
        assert load_bedrock_config(env).region == "us-west-2"
        return
    with pytest.raises(ValueError, match=match):
        load_bedrock_config(env)


def test_direct_construction_validates() -> None:
    with pytest.raises(ValueError, match="model id"):
        BedrockConfig(model_id=" ")
    with pytest.raises(ValueError, match="region"):
        BedrockConfig(region="")


def test_bedrock_config_has_no_credential_fields() -> None:
    fields = set(BedrockConfig.__dataclass_fields__)
    assert not {f for f in fields if "key" in f.lower() or "secret" in f.lower()}


# --- Haven planner backend configuration -------------------------------- #


def test_haven_default_backend_is_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAVEN_PLANNER_BACKEND", raising=False)
    assert get_config().planner_backend == "mock"


def test_haven_backend_bedrock_selected_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAVEN_PLANNER_BACKEND", " Bedrock ")
    assert get_config().planner_backend == "bedrock"


def test_haven_backend_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAVEN_PLANNER_BACKEND", "openai")
    with pytest.raises(ValueError, match="unsupported planner backend"):
        get_config()


def test_normalize_backend() -> None:
    assert normalize_backend("MOCK") == "mock"
    with pytest.raises(ValueError):
        normalize_backend("")
    with pytest.raises(ValueError):
        HavenConfig(planner_backend="nope")


def test_bedrock_client_module_imports_and_builds_model() -> None:
    # Offline: constructing the Strands model does not contact AWS.
    from aws.bedrock.client import create_bedrock_model

    model = create_bedrock_model(BedrockConfig(region="us-west-2", model_id="m", temperature=0.1))
    settings = model.get_config()
    assert settings["model_id"] == "m"
    assert settings["temperature"] == 0.1
    assert settings["max_tokens"] == 2048
