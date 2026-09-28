from datetime import datetime
from typing import Protocol


class CalendarProvider(Protocol):
    async def create_event(
        self,
        title: str,
        start_at: datetime,
        end_at: datetime | None,
        description: str | None = None,
        recurrence_rule: str | None = None,
        *,
        operation_id: str,
        recover_only: bool = False,
    ) -> str: ...
    async def update_event(
        self,
        external_id: str,
        title: str,
        start_at: datetime,
        end_at: datetime | None,
        recurrence_rule: str | None = None,
    ) -> None: ...
    async def delete_event(self, external_id: str) -> None: ...
    async def rename_event(self, external_id: str, title: str) -> None: ...
    async def list_events(
        self, start_at: datetime, end_at: datetime
    ) -> list[tuple[datetime, datetime, str]]: ...
