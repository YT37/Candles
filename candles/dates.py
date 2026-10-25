import calendar
import json
from datetime import date, timedelta
from pathlib import Path

CAMPUS = json.loads((Path(__file__).parent / "campus.json").read_text())


def occurrence(month: int, day: int, year: int) -> date:
    if month == 2 and day == 29 and not calendar.isleap(year):
        return date(year, 2, 28)
    return date(year, month, day)


def next_birthday(month: int, day: int, today: date) -> date:
    this_year = occurrence(month, day, today.year)
    return this_year if this_year >= today else occurrence(month, day, today.year + 1)


def campus_break(day: date) -> str | None:
    for period in CAMPUS["breaks"]:
        start = date(day.year, *map(int, period["start"].split("-")))
        end = date(day.year, *map(int, period["end"].split("-")))
        inside = start <= day <= end if start <= end else day >= start or day <= end
        if inside:
            return period["name"]
    return None


def lead_days(base: int, birthday: date) -> int:
    return base + CAMPUS.get("extra_lead_days_for_breaks", 7) if campus_break(birthday) else base


def weekday(d: date, today: date) -> str:
    days = (d - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"{d:%A}" if 0 < days < 7 else f"{d:%a %b %-d}"


def order_by(birthday: date, ship_days: int | None) -> date | None:
    return birthday - timedelta(days=ship_days + 1) if ship_days is not None else None
