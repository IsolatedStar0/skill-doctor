"""DeepSeek LLM client for the Skill-Adaptor pipeline.

DeepSeek is OpenAI-compatible, so we use the `openai` SDK with a custom
``base_url``. Exposes :func:`build_deepseek_client`, which returns a
``Callable[[str], str]`` matching the ``LLMClient`` protocol expected by
``adaptor.Localizer / Linker / Generator / Reviser / Qualifier``.

If credentials or the ``openai`` package are unavailable, the factory
returns ``None`` so the caller cleanly falls back to the rule-based
deterministic path used by offline tests.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 60.0


def _load_dotenv_once() -> None:
    """Best-effort ``.env`` loading. Uses ``python-dotenv`` when available.

    Falls back to a tiny manual parser so this module keeps working even
    without the optional dependency installed.
    """
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


def build_deepseek_client(
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[Callable[[str], str]]:
    """Return a prompt-in/response-out callable, or ``None`` if unavailable."""
    _load_dotenv_once()
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        logger.info(
            "DEEPSEEK_API_KEY not set; Skill-Adaptor stages will use "
            "rule-based fallback."
        )
        return None
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:
        logger.warning(
            "openai SDK not installed (%s); DeepSeek LLM disabled.", exc
        )
        return None

    model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
    base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def call(prompt: str) -> str:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an assistant embedded in the Skill-Doctor "
                            "attribution / repair pipeline. Follow the "
                            "user's response-format instructions precisely."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1500,
            )
            content = resp.choices[0].message.content or ""
            return content.strip()
        except Exception as exc:  # pragma: no cover - network path
            logger.warning("DeepSeek call failed: %s", exc)
            return ""

    logger.info("DeepSeek LLM client initialised (model=%s).", model)
    return call
