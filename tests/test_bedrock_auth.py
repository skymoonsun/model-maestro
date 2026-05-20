"""Bedrock authentication mode helpers."""

from app.bedrock_proxy import (
    bedrock_credentials_configured,
    bedrock_heuristic_inference_profile_id,
    bedrock_region_geo_prefix,
    is_bedrock_inference_profile_id,
    resolve_bedrock_auth_mode,
    resolve_bedrock_converse_model_id,
)


def test_resolve_bedrock_auth_mode_explicit():
    assert resolve_bedrock_auth_mode("api_key", "secret") == "api_key"
    assert resolve_bedrock_auth_mode("iam", None) == "iam"


def test_resolve_bedrock_auth_mode_infer_from_secret():
    assert resolve_bedrock_auth_mode(None, "sk") == "iam"
    assert resolve_bedrock_auth_mode(None, None) == "api_key"
    assert resolve_bedrock_auth_mode(None, "   ") == "api_key"


def test_bedrock_credentials_configured_iam():
    assert bedrock_credentials_configured(
        api_key="AKIA",
        secret_key="secret",
        region="us-east-1",
        bedrock_auth_mode="iam",
    )
    assert not bedrock_credentials_configured(
        api_key="AKIA",
        secret_key=None,
        region="us-east-1",
        bedrock_auth_mode="iam",
    )


def test_bedrock_credentials_configured_api_key():
    assert bedrock_credentials_configured(
        api_key="bedrock-key-abc",
        secret_key=None,
        region="eu-west-1",
        bedrock_auth_mode="api_key",
    )
    assert not bedrock_credentials_configured(
        api_key=None,
        secret_key=None,
        region="us-east-1",
        bedrock_auth_mode="api_key",
    )


def test_is_bedrock_inference_profile_id():
    assert is_bedrock_inference_profile_id("us.anthropic.claude-opus-4-6-v1")
    assert is_bedrock_inference_profile_id("global.anthropic.claude-sonnet-4-20250514-v1:0")
    assert not is_bedrock_inference_profile_id("anthropic.claude-opus-4-6-v1")


def test_resolve_bedrock_converse_model_id():
    assert resolve_bedrock_converse_model_id(
        "anthropic.claude-opus-4-6-v1",
        "us-east-1",
    ) == "us.anthropic.claude-opus-4-6-v1"
    assert resolve_bedrock_converse_model_id(
        "us.anthropic.claude-opus-4-6-v1",
        "us-east-1",
    ) == "us.anthropic.claude-opus-4-6-v1"
    assert resolve_bedrock_converse_model_id(
        "anthropic.claude-3-haiku-20240307-v1:0",
        "eu-west-1",
    ) == "eu.anthropic.claude-3-haiku-20240307-v1:0"


def test_bedrock_region_geo_prefix():
    assert bedrock_region_geo_prefix("us-east-1") == "us"
    assert bedrock_region_geo_prefix("eu-central-1") == "eu"
    assert bedrock_region_geo_prefix("ap-southeast-1") == "apac"


def test_bedrock_heuristic_inference_profile_id():
    assert bedrock_heuristic_inference_profile_id(
        "anthropic.claude-opus-4-6-v1",
        "us-east-1",
    ) == "us.anthropic.claude-opus-4-6-v1"
