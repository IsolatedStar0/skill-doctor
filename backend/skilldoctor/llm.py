"""LLM client for the Skill-Adaptor pipeline.

Reimplemented to mirror the OpenAI-compatible client used by
`zjunlp/SkillAdaptor` (see ``skill-adaptor/core/llm_client.py`` &
``core/provider_config.py``).

Key differences vs. the previous implementation:

* No dependency on the ``openai`` SDK. All calls go through
  ``urllib.request`` so the client works in minimal environments.
* Configuration is unified around three env vars (with DeepSeek-friendly
  fallbacks): ``CHAT_API_KEY`` / ``CHAT_BASE_URL`` / ``CHAT_MODEL``,
  and the pre-existing ``DEEPSEEK_API_KEY`` / ``DEEPSEEK_BASE_URL`` /
  ``DEEPSEEK_MODEL``.
* Provides both a class-style client (:class:`SkillDoctorLLMClient`)
  and the legacy :func:`build_deepseek_client` factory that returns a
  ``Callable[[str], str]`` matching the ``LLMClient`` protocol expected
  by ``adaptor.Localizer / Linker / Generator / Reviser / Qualifier``.

If credentials are missing, the factory returns ``None`` so callers
cleanly fall back to the rule-based deterministic path.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 60.0

# Env var priority (mirrors SkillAdaptor's ``core.api_env``). We intentionally
# do NOT fall back to ``OPENAI_*`` env vars because those may point at an
# unrelated proxy in the host environment; users who want to use them can pass
# ``base_url`` / ``api_key`` explicitly.
_CHAT_KEY_ENVS = ("CHAT_API_KEY", "DEEPSEEK_API_KEY")
_CHAT_URL_ENVS = ("CHAT_BASE_URL", "DEEPSEEK_BASE_URL", "DEEPSEEK_API_BASE_URL")
_CHAT_MODEL_ENVS = ("CHAT_MODEL", "DEEPSEEK_MODEL")


def _first_env(*keys: str) -> str:
    for key in keys:
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return ""


def _load_dotenv_once() -> None:
    """Best-effort ``.env`` loading (python-dotenv when available)."""
    project_root = Path(__file__).resolve().parents[2]
    env_path = project_root / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_path, override=False)
        return
    except Exception:
        pass
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
    except Exception as exc:  # pragma: no cover
        logger.debug("Failed to parse .env at %s: %s", env_path, exc)


class SkillDoctorLLMClient:
    """OpenAI-compatible chat client.

    Ported from ``SkillAdaptorLLMClient`` in ``zjunlp/SkillAdaptor``.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key or _first_env(*_CHAT_KEY_ENVS)
        self.base_url = (base_url or _first_env(*_CHAT_URL_ENVS) or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or _first_env(*_CHAT_MODEL_ENVS) or DEFAULT_MODEL
        self.timeout = timeout
        if not self.api_key:
            raise ValueError(
                "API key must be provided via argument or one of the env vars: "
                + ", ".join(_CHAT_KEY_ENVS)
            )
        # This provider's /chat/completions lives at the root, so we do not
        # force a /v1 suffix (SkillAdaptor's ``_profile_deepseek`` appends /v1
        # for the official DeepSeek endpoint; this deployment differs).
        self._model_lower = self.model.lower()
        self.uses_reasoning_content = any(x in self._model_lower for x in ("kimi", "glm"))

    def call(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1500,
        temperature: float = 0.2,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            logger.warning("LLM HTTP %s: %s", exc.code, body[:300])
            return ""
        except Exception as exc:
            logger.warning("LLM call failed: %s", exc)
            return ""

        msg = data.get("choices", [{}])[0].get("message", {}) or {}
        content = msg.get("content") or ""
        if not content and "reasoning_content" in msg and self.uses_reasoning_content:
            content = self._extract_from_reasoning(msg.get("reasoning_content") or "")
        return content.strip()

    def call_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1500,
        temperature: float = 0.2,
    ) -> dict:
        if "json" not in prompt.lower():
            prompt += "\n\nReturn your response as valid JSON."
        content = self.call(prompt, system=system, max_tokens=max_tokens, temperature=temperature)
        return self._extract_json(content)

    @staticmethod
    def _extract_from_reasoning(reasoning: str) -> str:
        if not reasoning:
            return ""
        m = re.search(r"```(?:json)?\s*\n?(.*?)```", reasoning, re.DOTALL)
        if m:
            candidate = m.group(1).strip()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
        # Fallback: last non-empty sentence.
        parts = [s.strip() for s in reasoning.split(".") if s.strip()]
        return parts[-1] if parts else reasoning

    @staticmethod
    def _extract_json(content: str) -> dict:
        m = re.search(r"```(?:json)?\s*\n?(.*?)```", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass
        start = content.find("{")
        if start >= 0:
            depth = 0
            for i, ch in enumerate(content[start:]):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(content[start : start + i + 1])
                        except json.JSONDecodeError:
                            break
        start, end = content.find("["), content.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"No valid JSON found in response: {content[:200]}...")


def build_deepseek_client(
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[Callable[[str], str]]:
    """Return a prompt-in/response-out callable, or ``None`` if unavailable.

    Preserves the previous public signature so ``service.py`` and all
    adaptor stages keep working unchanged.
    """
    _load_dotenv_once()
    try:
        client = SkillDoctorLLMClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
        )
    except ValueError as exc:
        logger.info(
            "LLM disabled (%s); Skill-Adaptor stages will use rule-based fallback.",
            exc,
        )
        return None

    system_prompt = (
        "You are an assistant embedded in the Skill-Doctor "
        "attribution / repair pipeline. Follow the user's response-format "
        "instructions precisely."
    )

    def call(prompt: str) -> str:
        return client.call(prompt, system=system_prompt, max_tokens=1500, temperature=0.2)

    logger.info(
        "LLM client initialised (model=%s, base_url=%s).",
        client.model,
        client.base_url,
    )
    return call
