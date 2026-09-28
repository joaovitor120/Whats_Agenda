import logging
import re

import structlog


class SafeFormatter(logging.Formatter):
    def formatException(self, ei):
        return f"[exception:{ei[0].__name__}]"

    def format(self, record):
        from app.core.config import settings

        value = super().format(record)
        for name, secret in settings.model_dump().items():
            if (
                isinstance(secret, str)
                and len(secret) >= 8
                and any(
                    word in name for word in ("key", "secret", "token", "database_url", "redis_url")
                )
            ):
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[REDACTED]", value)
        return re.sub(r"\b[1-9][0-9]{9,14}\b", "[PHONE]", value)


def redact_event(logger, method, event):
    for key in list(event):
        if any(
            part in key.lower()
            for part in (
                "token",
                "secret",
                "password",
                "payload",
                "phone",
                "transcription",
                "authorization",
                "qr",
            )
        ):
            event[key] = "[REDACTED]"
    return event


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter("%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            redact_event,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
