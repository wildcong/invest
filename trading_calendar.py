"""KRX sessions, including holidays and delayed closing sessions.

Fail closed if the installed calendar cannot cover a requested year. There is
intentionally no weekday-only fallback: that would invent trading sessions.
"""
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd

KST = timezone(timedelta(hours=9))
PUBLICATION_DELAY = timedelta(minutes=15)
REVIEWED_THROUGH_YEAR = 2026
# The upstream holiday calendar extrapolates beyond reviewed years and its
# CSAT offsets stop in 2020. Unreviewed years use a conservative 16:30 close
# every session, with a visible review-due notice; collection continues.
# Education Ministry dates (calendar year, not academic year):
# https://www.moe.go.kr/boardCnts/viewRenew.do?boardID=294&boardSeq=100526
# https://www.moe.go.kr/boardCnts/viewRenew.do?boardID=294&boardSeq=96215
DELAYED_CLOSE_DATES = {"2024-11-14", "2025-11-13", "2026-11-19"}


@lru_cache(maxsize=4)
def calendar_for_year(year: int):
    return xcals.get_calendar(
        "XKRX", start=f"{year - 1}-01-01", end=f"{year}-12-31"
    )


def completed_session(now: datetime | None = None) -> str:
    current = now or datetime.now(KST)
    current = current.replace(tzinfo=KST) if current.tzinfo is None else current.astimezone(KST)
    calendar = calendar_for_year(current.year)
    session = calendar.date_to_session(pd.Timestamp(current.date()), direction="previous")
    close = calendar.session_close(session)
    if session.year > REVIEWED_THROUGH_YEAR or session.strftime("%Y-%m-%d") in DELAYED_CLOSE_DATES:
        # Conservative until the exchange's annual notice: never collect the
        # known CSAT session at the ordinary 15:30 close. Avoid double offsets
        # if a newer upstream version includes the delayed close.
        close = max(close, pd.Timestamp(f"{session:%Y-%m-%d} 16:30", tz=KST))
    if pd.Timestamp(current) < close + PUBLICATION_DELAY:
        session = calendar.previous_session(session)
    return session.strftime("%Y%m%d")


def is_session_date(value: str) -> bool:
    try:
        date = datetime.strptime(value, "%Y%m%d")
        return calendar_for_year(date.year).is_session(pd.Timestamp(date.date()))
    except (ValueError, TypeError):
        return False
