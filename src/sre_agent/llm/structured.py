"""Structured-output invocation with a bounded repair loop.

Order: provider-native json_schema -> function_calling -> parse fallback.
A failed validation gets exactly one repair attempt with the errors echoed
back; a second failure raises StructuredOutputError. Mutating actions are
never executed on invalid output (spec section 13).
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from sre_agent.exceptions import StructuredOutputError


async def invoke_structured[T: BaseModel](
    model: BaseChatModel,
    schema: type[T],
    messages: list[BaseMessage],
    *,
    repair_attempts: int = 1,
) -> T:
    """Invoke the model for a structured ``schema`` result."""
    errors: list[str] = []
    attempt = 0
    msgs = list(messages)
    while True:
        attempt += 1
        structured = None
        last_exc: Exception | None = None
        for method in ("json_schema", "function_calling"):
            try:
                structured = await model.with_structured_output(schema, method=method).ainvoke(msgs)
                break
            except (ValidationError, Exception) as exc:  # provider may not support method
                last_exc = exc
                if _is_method_unsupported(exc):
                    continue
                # validation-style failure -> fall through to repair path
                break
        if structured is not None:
            if isinstance(structured, schema):
                return structured
            try:
                return schema.model_validate(structured)
            except ValidationError as exc:
                last_exc = exc

        if attempt > repair_attempts:
            raise StructuredOutputError(
                f"{schema.__name__} invalid after {attempt} attempt(s): {last_exc}"
            ) from last_exc
        errors.append(str(last_exc))
        msgs = list(messages) + [
            HumanMessage(
                content=(
                    "Your previous response failed validation. Errors: "
                    f"{errors[-1]}\nReturn ONLY a valid object matching the schema."
                )
            )
        ]


def _is_method_unsupported(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        k in text
        for k in (
            "json_schema",
            "response_format",
            "structured",
            "not supported",
            "unsupported",
            "invalid_request",
        )
    )
