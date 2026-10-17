"""Timezone-aware timestamps shared by policies, storage and presentation."""

from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Use an ISO timestamp including a timezone.")
    return parsed.astimezone(timezone.utc)


def timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Timestamps must include a timezone.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
