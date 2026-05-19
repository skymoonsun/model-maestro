"""Tests for Antigravity quota parsing."""

from app.google_quota import _parse_quota_response


def test_parse_quota_response_models_and_forwarding() -> None:
    raw = {
        "models": {
            "gemini-2.5-flash": {
                "displayName": "Gemini 2.5 Flash",
                "quotaInfo": {
                    "remainingFraction": 0.42,
                    "resetTime": "2026-05-20T12:00:00Z",
                },
                "supportsThinking": True,
            },
            "internal-chat": {
                "quotaInfo": {"remainingFraction": 1.0},
            },
            "claude-sonnet-4-5": {
                "quotaInfo": {"remainingFraction": 0.05, "resetTime": ""},
            },
        },
        "deprecatedModelIds": {
            "old-model": {"newModelId": "new-model"},
        },
    }

    quota = _parse_quota_response(raw, subscription_tier="PRO")

    assert quota["subscription_tier"] == "PRO"
    assert quota["is_forbidden"] is False
    assert len(quota["models"]) == 2
    names = {m["name"] for m in quota["models"]}
    assert names == {"gemini-2.5-flash", "claude-sonnet-4-5"}

    flash = next(m for m in quota["models"] if m["name"] == "gemini-2.5-flash")
    assert flash["percentage"] == 42
    assert flash["reset_time"] == "2026-05-20T12:00:00Z"
    assert flash["display_name"] == "Gemini 2.5 Flash"
    assert flash["supports_thinking"] is True

    assert quota["model_forwarding_rules"] == {"old-model": "new-model"}


def test_parse_quota_response_wrapped_in_response_key() -> None:
    raw = {
        "response": {
            "models": {
                "gpt-oss-120b": {
                    "quotaInfo": {"remainingFraction": 1.0},
                }
            }
        }
    }
    quota = _parse_quota_response(raw, None)
    assert len(quota["models"]) == 1
    assert quota["models"][0]["percentage"] == 100
