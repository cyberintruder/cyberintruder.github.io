"""Claude wrapper.

Every LLM call in this project goes through here so that model choice, thinking,
structured output and graceful degradation are handled in exactly one place.

If no API key is configured, `available()` is False and callers fall back to
their deterministic paths (keyword scoring, answer bank, template email) rather
than crashing — the agent stays useful without a key, just less sharp.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

from . import settings as settings_mod

log = logging.getLogger(__name__)

# Effort tiers. Cheap, high-volume calls run low; judgement calls run high.
EFFORT_FAST = "low"
EFFORT_STANDARD = "medium"
EFFORT_CAREFUL = "high"


class LLMUnavailable(RuntimeError):
    pass


class Claude:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        st = settings_mod.load()
        self._api_key = api_key or st.env.anthropic_api_key
        self.model = model or st.env.model
        self._client: Any = None

    # ------------------------------------------------------------------ setup

    def available(self) -> bool:
        return bool(self._api_key)

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------ calls

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 8000,
        effort: str = EFFORT_STANDARD,
        images: list[Path] | None = None,
    ) -> str:
        """Plain text completion, optionally with images attached to the prompt."""
        content: list[dict[str, Any]] = []
        for img in images or []:
            content.append(_image_block(img))
        content.append({"type": "text", "text": prompt})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
        if system:
            kwargs["system"] = system

        resp = self.client.messages.create(**kwargs)
        if getattr(resp, "stop_reason", None) == "refusal":
            raise LLMUnavailable("model declined this request")
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        system: str | None = None,
        max_tokens: int = 8000,
        effort: str = EFFORT_STANDARD,
        images: list[Path] | None = None,
    ) -> dict[str, Any]:
        """Completion constrained to a JSON schema. Returns the parsed object."""
        content: list[dict[str, Any]] = []
        for img in images or []:
            content.append(_image_block(img))
        content.append({"type": "text", "text": prompt})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        if system:
            kwargs["system"] = system

        resp = self.client.messages.create(**kwargs)
        if getattr(resp, "stop_reason", None) == "refusal":
            raise LLMUnavailable("model declined this request")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text)


def _image_block(path: Path) -> dict[str, Any]:
    path = Path(path)
    media_type = mimetypes.guess_type(path.name)[0] or "image/png"
    if media_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
        media_type = "image/png"
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


_default: Claude | None = None


def get_client() -> Claude:
    global _default
    if _default is None:
        _default = Claude()
    return _default


def reset_client() -> None:
    """Drop the cached client (used after config reloads and in tests)."""
    global _default
    _default = None


# --------------------------------------------------------------- json schemas

JOB_MATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "resume_key": {"type": "string"},
        "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "missing_skills": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "seniority_fit": {"type": "string", "enum": ["under", "match", "over"]},
        "recommend_apply": {"type": "boolean"},
    },
    "required": ["score", "resume_key", "reasons", "missing_skills", "seniority_fit", "recommend_apply"],
    "additionalProperties": False,
}

JOB_POST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_job_post": {"type": "boolean"},
        "title": {"type": "string"},
        "company": {"type": "string"},
        "location": {"type": "string"},
        "workplace_type": {"type": "string", "enum": ["remote", "hybrid", "onsite", "unknown"]},
        "seniority": {"type": "string"},
        "description": {"type": "string"},
        "skills": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "contact_name": {"type": "string"},
        "contact_email": {"type": "string"},
        "apply_url": {"type": "string"},
        "apply_instructions": {"type": "string"},
        "posted_ago": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "is_job_post", "title", "company", "location", "workplace_type", "seniority",
        "description", "skills", "contact_name", "contact_email", "apply_url",
        "apply_instructions", "posted_ago", "confidence",
    ],
    "additionalProperties": False,
}

EMAIL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["subject", "body"],
    "additionalProperties": False,
}

FORM_ANSWERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_id": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
                "required": ["field_id", "value", "confidence", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answers"],
    "additionalProperties": False,
}
