from celery import Celery

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging()

celery = Celery(
    "whats_agenda",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.inbound", "app.workers.reminders"],
)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone=settings.default_timezone,
    enable_utc=True,
    beat_schedule={
        "retention-cleanup": {"task": "app.workers.reminders.cleanup", "schedule": 86400.0},
        "dispatch-due-reminders": {"task": "app.workers.reminders.dispatch_due", "schedule": 30.0},
        "recover-pending-messages": {
            "task": "app.workers.inbound.dispatch_pending",
            "schedule": 30.0,
        },
        "refresh-recurring-reminders": {
            "task": "app.workers.reminders.refresh_recurring",
            "schedule": 86400.0,
        },
    },
    task_acks_late=True,
    task_ignore_result=True,
    task_time_limit=120,
    broker_connection_timeout=3,
    task_publish_retry=False,
    worker_hijack_root_logger=False,
    worker_prefetch_multiplier=1,
)
