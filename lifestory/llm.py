"""The model provider layer (spec v2 §11: "must not permanently couple the
Life Archive to one model provider").

Everything that talks to a language model goes through here. The rest of the
package names a *task* -- extraction, direction, drafting -- and never a vendor.

Currently targets DeepSeek through its OpenAI-compatible endpoint.

Three things about this provider shape the code below:

1. **No strict schema.** `response_format={"type": "json_schema", strict: true}`
   returns 400 -- "This response_format type is unavailable now". Only
   `json_object` is offered, which guarantees syntactically valid JSON and
   nothing about its shape. So the schema goes into the prompt and every
   response is validated against the Pydantic model here, client side.

2. **Reasoning tokens count against `max_tokens`.** `deepseek-flash` emits a
   `reasoning_content` field before any visible output. A cap that looked
   generous for the answer can be consumed entirely by reasoning, and the call
   then returns HTTP 200 with *empty content*. A truncated response is raised,
   never returned as an empty extraction.

3. **Validation failure is now a real failure mode.** With Anthropic's
   `messages.parse` the schema was enforced server side and a malformed
   response could not reach us. Here it can, so it is retried once and then
   raised -- never silently swallowed, because a dropped window means a lost
   piece of somebody's life story.
"""

from __future__ import annotations

import json
import os
from typing import TypeVar

from pydantic import BaseModel, ValidationError

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"

_DOTENV_LOADED = False

# Reasoning tokens are billed and capped together with the answer, so these are
# deliberately well clear of what the output alone needs.
# 16,000 was too tight: extracting one 12-minute Mandarin session ran out
# mid-response, because Chinese output and reasoning both spend tokens freely.
# The provider accepts 64,000; only tokens actually used are billed.
EXTRACTION_TOKENS = 32000
DRAFTING_TOKENS = 32000

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    """The model could not be reached, or would not answer usefully."""


class TruncatedError(ProviderError):
    """The response hit the token ceiling. Partial output is never used."""


class SchemaError(ProviderError):
    """The model returned JSON that does not fit the requested shape."""


def _load_dotenv() -> None:
    """Read KEY=VALUE lines from `.env` in the project folder, once.

    The README and this module's own error message both told operators to put
    the key in `.env`, and nothing read it: anyone who followed the
    instructions got "no API key". Deliberately tiny rather than a new
    dependency. Real environment variables win, so a shell export overrides.

    Decodes defensively because this runs on Windows, where Notepad writes a
    UTF-8 BOM and Windows PowerShell 5.1's `>` writes UTF-16.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    from .archive import project_root  # local import: archive does not import us

    path = project_root() / ".env"
    if not path.is_file():
        return

    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig", errors="replace")

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _api_key() -> str | None:
    _load_dotenv()
    return os.environ.get("LIFESTORY_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")


def _base_url() -> str:
    _load_dotenv()
    return os.environ.get("LIFESTORY_API_BASE", DEFAULT_BASE_URL)


def _model_name() -> str:
    _load_dotenv()
    return os.environ.get("LIFESTORY_MODEL", DEFAULT_MODEL)


def credentials_present() -> bool:
    return bool(_api_key())


def _client():
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise ProviderError("the `openai` package is required: pip install openai") from exc

    key = _api_key()
    if not key:
        raise ProviderError(
            "no API key. Put LIFESTORY_API_KEY=... in a .env file in the project "
            "folder, or set it in the environment -- never in the source."
        )
    return OpenAI(api_key=key, base_url=_base_url())


def _schema_instruction(model: type[BaseModel]) -> str:
    """The shape contract, stated in the prompt.

    This provider will not enforce a schema, so it has to be asked for. The
    schema is generated from the Pydantic model so it cannot drift from what
    we then validate against.
    """
    schema = json.dumps(model.model_json_schema(), indent=2)
    return (
        "Reply with a single JSON object and nothing else. No prose before or "
        "after it, no markdown fence.\n\n"
        "It must conform to this JSON Schema:\n\n"
        f"{schema}\n\n"
        "Every required field must be present. Use null for an optional value "
        "you cannot support from the source material -- never invent one to "
        "fill the field."
    )


def complete_json(
    *,
    system: str,
    prompt: str,
    output_model: type[T],
    max_tokens: int = EXTRACTION_TOKENS,
    temperature: float = 0.2,
    retries: int = 1,
) -> T:
    """Ask for a JSON object of the given shape and return it validated.

    Raises rather than returning anything partial or unvalidated. Callers
    checkpoint their work, so a raise costs one window, not the whole session.
    """
    client = _client()
    instruction = _schema_instruction(output_model)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{prompt}\n\n{instruction}"},
    ]

    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=_model_name(),
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # network, auth, rate limit
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc

        choice = response.choices[0]
        content = (choice.message.content or "").strip()

        if choice.finish_reason == "length" or not content:
            # Reasoning consumed the budget, or the answer was cut mid-object.
            raise TruncatedError(
                f"response truncated at {max_tokens} tokens "
                f"(finish_reason={choice.finish_reason}, "
                f"{len(content)} chars of content). Raise max_tokens or shrink "
                f"the window."
            )

        try:
            return output_model.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                # Show it what it got wrong rather than re-rolling blind.
                messages.append({"role": "assistant", "content": content[:2000]})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "That did not match the schema:\n\n"
                            f"{str(exc)[:600]}\n\n"
                            "Reply again with the corrected JSON object only."
                        ),
                    }
                )
                continue

    raise SchemaError(
        f"model did not return the requested shape after {retries + 1} attempts: "
        f"{str(last_error)[:300]}"
    )


def describe() -> str:
    """One line naming the provider, for operator output and the audit trail."""
    return f"{_model_name()} @ {_base_url()}"
