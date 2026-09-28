"""Calendar adapters. All URLs are fixed; identifiers are quoted as path segments."""

from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.http import request_json
from app.core.time import utc

OP_PROPERTY = "String {297092b2-a7da-4b51-9e7f-476f0178e7b4} Name WhatsAgendaOperation"


class ReconciliationRequired(RuntimeError):
    pass


def clean_title(value):
    if not isinstance(value, str):
        raise ValueError("Invalid external title")
    return "".join(char for char in value[:255] if char.isprintable()) or "Evento"


def external_id(payload):
    value = payload.get("id")
    if not isinstance(value, str) or not 0 < len(value) <= 255:
        raise ValueError("Invalid external event ID")
    return value


class FakeCalendar:
    """The local database is the fake calendar. No process-global user data."""

    async def create_event(
        self,
        title,
        start_at,
        end_at,
        description=None,
        recurrence_rule=None,
        *,
        operation_id,
        recover_only=False,
    ):
        return operation_id

    async def update_event(self, external_id, title, start_at, end_at, recurrence_rule=None):
        return None

    async def delete_event(self, external_id):
        return None

    async def rename_event(self, external_id, title):
        return None

    async def list_events(self, start_at, end_at):
        return []


class GoogleCalendar:
    def __init__(self, access_token, calendar_id="primary", timezone="America/Sao_Paulo"):
        self.headers = {"Authorization": f"Bearer {access_token}"}
        self.base = (
            f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events"
        )
        self.timezone = timezone

    def _body(self, title, start_at, end_at, description=None, recurrence_rule=None):
        body = {
            "summary": title,
            "start": {"dateTime": start_at.isoformat(), "timeZone": self.timezone},
            "end": {
                "dateTime": (end_at or start_at + timedelta(minutes=30)).isoformat(),
                "timeZone": self.timezone,
            },
        }
        if description is not None:
            body["description"] = description
        if recurrence_rule:
            body["recurrence"] = [f"RRULE:{recurrence_rule}"]
        return body

    async def rename_event(self, event_id, title):
        await request_json(
            "PATCH",
            f"{self.base}/{quote(event_id, safe='')}",
            headers=self.headers,
            json={"summary": title},
        )

    async def create_event(
        self,
        title,
        start_at,
        end_at,
        description=None,
        recurrence_rule=None,
        *,
        operation_id,
        recover_only=False,
    ):
        body = self._body(title, start_at, end_at, description, recurrence_rule)
        body.update(id=operation_id, extendedProperties={"private": {"operation": operation_id}})
        payload = await request_json(
            "POST", self.base, headers=self.headers, json=body, allowed_status=(409,)
        )
        if payload.get("_status") == 409:
            payload = await request_json(
                "GET", f"{self.base}/{quote(operation_id, safe='')}", headers=self.headers
            )
            if (
                payload.get("extendedProperties", {}).get("private", {}).get("operation")
                != operation_id
            ):
                raise ReconciliationRequired("Existing event does not match operation")
        return external_id(payload)

    async def update_event(self, event_id, title, start_at, end_at, recurrence_rule=None):
        await request_json(
            "PATCH",
            f"{self.base}/{quote(event_id, safe='')}",
            headers=self.headers,
            json=self._body(title, start_at, end_at, recurrence_rule=recurrence_rule),
        )

    async def delete_event(self, event_id):
        await request_json(
            "DELETE",
            f"{self.base}/{quote(event_id, safe='')}",
            headers=self.headers,
            allowed_status=(404, 410),
        )

    async def list_events(self, start_at, end_at):
        params = {
            "timeMin": start_at.isoformat(),
            "timeMax": end_at.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 250,
        }
        result = []
        for _ in range(10):
            payload = await request_json("GET", self.base, headers=self.headers, params=params)
            items = payload.get("items", [])
            if not isinstance(items, list):
                raise ValueError("Invalid Google event list")
            for item in items:
                if item.get("status") == "cancelled" or item.get("transparency") == "transparent":
                    continue
                start, end = item.get("start", {}), item.get("end", {})

                def parse(value):
                    if value.get("dateTime"):
                        parsed = datetime.fromisoformat(value["dateTime"])
                        if parsed.tzinfo is None:
                            raise ValueError("Missing event timezone")
                        return parsed
                    return datetime.fromisoformat(value["date"]).replace(
                        tzinfo=ZoneInfo(self.timezone)
                    )

                result.append(
                    (parse(start), parse(end), clean_title(item.get("summary", "Evento")))
                )
            token = payload.get("nextPageToken")
            if not token:
                return result
            if not isinstance(token, str) or len(token) > 4096:
                raise ValueError("Invalid page token")
            params["pageToken"] = token
        raise ValueError("Calendar exceeds bounded query; no availability can be guaranteed")


class MicrosoftCalendar:
    def __init__(self, access_token, timezone="America/Sao_Paulo"):
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Prefer": 'outlook.timezone="UTC", IdType="ImmutableId"',
        }
        self.base = "https://graph.microsoft.com/v1.0/me/events"
        self.timezone = timezone

    def _body(self, title, start_at, end_at, recurrence_rule=None):
        zone = ZoneInfo(self.timezone)
        start, finish = (
            start_at.astimezone(zone),
            (end_at or start_at + timedelta(minutes=30)).astimezone(zone),
        )
        body = {
            "subject": title,
            "start": {
                "dateTime": start.replace(tzinfo=None).isoformat(),
                "timeZone": self.timezone,
            },
            "end": {"dateTime": finish.replace(tzinfo=None).isoformat(), "timeZone": self.timezone},
        }
        if recurrence_rule:
            if recurrence_rule == "FREQ=DAILY":
                pattern = {"type": "daily", "interval": 1}
            else:
                day_names = dict(
                    MO="monday",
                    TU="tuesday",
                    WE="wednesday",
                    TH="thursday",
                    FR="friday",
                    SA="saturday",
                    SU="sunday",
                )
                days = [day_names[day] for day in recurrence_rule.split("BYDAY=")[1].split(",")]
                pattern = {"type": "weekly", "interval": 1, "daysOfWeek": days}
            body["recurrence"] = {
                "pattern": pattern,
                "range": {
                    "type": "noEnd",
                    "startDate": start.date().isoformat(),
                    "recurrenceTimeZone": self.timezone,
                },
            }
        return body

    async def rename_event(self, event_id, title):
        await request_json(
            "PATCH",
            f"{self.base}/{quote(event_id, safe='')}",
            headers=self.headers,
            json={"subject": title},
        )

    async def create_event(
        self,
        title,
        start_at,
        end_at,
        description=None,
        recurrence_rule=None,
        *,
        operation_id,
        recover_only=False,
    ):
        # Exact, durable marker supplements Graph's transactionId retry protection.
        found = await request_json(
            "GET",
            self.base,
            headers=self.headers,
            params={
                "$filter": f"singleValueExtendedProperties/Any(ep: ep/id eq '{OP_PROPERTY}' and ep/value eq '{operation_id}')",
                "$select": "id",
                "$top": 2,
            },
        )
        items = found.get("value")
        if not isinstance(items, list) or len(items) > 1:
            raise ReconciliationRequired("Invalid operation reconciliation")
        if items:
            return external_id(items[0])
        if recover_only:
            raise ReconciliationRequired("Ambiguous prior create; manual reconciliation required")
        body = self._body(title, start_at, end_at, recurrence_rule)
        body.update(
            transactionId=operation_id,
            singleValueExtendedProperties=[{"id": OP_PROPERTY, "value": operation_id}],
        )
        if description:
            body["body"] = {"contentType": "text", "content": description}
        return external_id(await request_json("POST", self.base, headers=self.headers, json=body))

    async def update_event(self, event_id, title, start_at, end_at, recurrence_rule=None):
        await request_json(
            "PATCH",
            f"{self.base}/{quote(event_id, safe='')}",
            headers=self.headers,
            json=self._body(title, start_at, end_at, recurrence_rule),
        )

    async def delete_event(self, event_id):
        await request_json(
            "DELETE",
            f"{self.base}/{quote(event_id, safe='')}",
            headers=self.headers,
            allowed_status=(404,),
        )

    async def list_events(self, start_at, end_at):
        url = "https://graph.microsoft.com/v1.0/me/calendarView"
        params = {
            "startDateTime": start_at.isoformat(),
            "endDateTime": end_at.isoformat(),
            "$top": 250,
        }
        result = []
        for _ in range(10):
            payload = await request_json("GET", url, headers=self.headers, params=params)
            items = payload.get("value")
            if not isinstance(items, list):
                raise ValueError("Invalid Graph event list")
            for item in items:
                if item.get("isCancelled") or item.get("showAs") == "free":
                    continue

                def parse(value):
                    if value.get("timeZone") != "UTC":
                        raise ValueError("Unexpected Graph timezone")
                    return utc(datetime.fromisoformat(value["dateTime"]))

                result.append(
                    (
                        parse(item["start"]),
                        parse(item["end"]),
                        clean_title(item.get("subject", "Evento")),
                    )
                )
            next_url = payload.get("@odata.nextLink")
            if not next_url:
                return result
            # Never follow an upstream-provided URL with bearer credentials. Extract only the cursor.
            from urllib.parse import parse_qs, urlsplit

            parsed = urlsplit(next_url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "graph.microsoft.com"
                or parsed.path != "/v1.0/me/calendarView"
            ):
                raise ValueError("Invalid Graph pagination origin")
            cursor = parse_qs(parsed.query).get("$skiptoken", [])
            if len(cursor) != 1 or len(cursor[0]) > 8192:
                raise ValueError("Invalid Graph page cursor")
            params["$skiptoken"] = cursor[0]
        raise ValueError("Calendar exceeds bounded query; no availability can be guaranteed")


fake_calendar = FakeCalendar()


def get_calendar_provider(provider="fake", access_token=None, calendar_id="primary", timezone=None):
    if provider == "fake":
        if not settings.fake_calendar:
            raise ValueError("Fake calendar is disabled")
        return fake_calendar
    if settings.fake_calendar or not access_token:
        raise ValueError("Calendar integration mode mismatch")
    if provider == "google":
        return GoogleCalendar(access_token, calendar_id, timezone or settings.default_timezone)
    if provider == "microsoft":
        return MicrosoftCalendar(access_token, timezone or settings.default_timezone)
    raise ValueError("Unsupported provider")


async def provider_for_connection(
    connection, session, *, user_id, timezone=None, commit_tokens=True
):
    from app.api.oauth import TokenResponse, oauth_config
    from app.core.security import decrypt_token, encrypt_token

    if connection.user_id != user_id or not connection.active:
        raise ValueError("Calendar ownership or status mismatch")
    now = datetime.now(UTC)
    expiry = connection.token_expires_at
    if expiry is None or utc(expiry) <= now + timedelta(minutes=2):
        if not connection.encrypted_refresh_token:
            raise ValueError("Calendar authorization expired")
        _, url, scope = oauth_config(connection.provider)
        body = {
            "client_id": getattr(settings, f"{connection.provider}_client_id"),
            "client_secret": getattr(settings, f"{connection.provider}_client_secret"),
            "refresh_token": decrypt_token(connection.encrypted_refresh_token),
            "grant_type": "refresh_token",
        }
        if connection.provider == "microsoft":
            body["scope"] = scope
        tokens = TokenResponse.model_validate(await request_json("POST", url, data=body))
        connection.encrypted_access_token = encrypt_token(tokens.access_token)
        if tokens.refresh_token:
            connection.encrypted_refresh_token = encrypt_token(tokens.refresh_token)
        connection.token_expires_at = now + timedelta(seconds=tokens.expires_in)
        if commit_tokens:
            await session.commit()
        else:
            await session.flush()
    return get_calendar_provider(
        connection.provider,
        decrypt_token(connection.encrypted_access_token),
        connection.calendar_id,
        timezone,
    )
