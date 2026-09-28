"""Small, bounded Claude requests. No conversation history or calendar data is sent."""

import asyncio
from functools import lru_cache
from time import monotonic

import httpx
import structlog

from app.ai.prompts import SYSTEM_PROMPT
from app.ai.schemas import ParsedIntent
from app.core.config import settings
from app.core.http import UpstreamError, request_json

log = structlog.get_logger()


class InterpreterUnavailable(RuntimeError):
    pass


@lru_cache
def intent_schema() -> dict:
    schema = ParsedIntent.model_json_schema()

    # Walk schema nodes, preserving field names such as the event's "title".
    def walk(node):
        if isinstance(node, dict):
            if "properties" in node:
                # Legacy scalar stays readable locally, but is omitted from the wire
                # schema to avoid duplicating nullable fields in creation batches.
                node["properties"].pop("reminder_minutes", None)
            for key in (
                "title",
                "default",
                "minimum",
                "maximum",
                "minLength",
                "maxLength",
                "maxItems",
            ):
                node.pop(key, None)
            if "intent" in node.get("properties", {}):
                # Optional fields in both the envelope and batch items create an
                # expensive grammar. Null/empty defaults keep the schema predictable.
                node["required"] = list(node["properties"])
            for key, value in node.items():
                if key in {"properties", "$defs"}:
                    for child in value.values():
                        walk(child)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return schema


async def interpret_with_claude(message: str) -> ParsedIntent:
    started = monotonic()
    try:
        async with asyncio.timeout(40):
            response = await request_json(
                "POST",
                "https://api.anthropic.com/v1/messages",
                max_bytes=32000,
                timeout=httpx.Timeout(35, connect=5),
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": settings.anthropic_model,
                    "max_tokens": settings.llm_max_output_tokens,
                    "thinking": {"type": "disabled"},
                    "system": SYSTEM_PROMPT
                    + " Preencha todos os campos do schema: null para dados ausentes (event_time usa string vazia), [] para listas vazias, "
                    "false para date_uncertain quando o dia estiver claro e other para categoria não aplicável. "
                    "Para lembretes sem alteração, use reminder_mode=preserve e reminder_offsets_minutes=[]. "
                    "Nunca invente informações para preencher campos. Em create_events, os dados de cada "
                    "compromisso ficam em events, com campos de evento do nível principal nulos. "
                    "Seja conciso; não repita a mensagem na descrição.",
                    "messages": [{"role": "user", "content": message}],
                    "output_config": {"format": {"type": "json_schema", "schema": intent_schema()}},
                },
            )
    except (httpx.HTTPError, UpstreamError, TimeoutError) as exc:
        log.warning(
            "claude_unavailable",
            error_type=type(exc).__name__,
            status_code=getattr(exc, "status_code", None),
            elapsed_seconds=round(monotonic() - started, 2),
        )
        # Do not let a transient/ambiguous paid request trigger five worker retries.
        raise InterpreterUnavailable("Claude is unavailable") from exc
    if response.get("stop_reason") != "end_turn":
        raise ValueError("Incomplete or refused interpretation")
    content = response.get("content")
    if not isinstance(content, list) or len(content) != 1 or content[0].get("type") != "text":
        raise ValueError("Invalid interpretation envelope")
    result = ParsedIntent.model_validate_json(content[0]["text"])
    usage = response.get("usage", {})
    log.info(
        "claude_usage",
        input_units=usage.get("input_tokens"),
        output_units=usage.get("output_tokens"),
    )
    return result
