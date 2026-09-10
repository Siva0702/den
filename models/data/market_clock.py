"""US cash-session context from an exchange calendar, never fixed UTC hours."""
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo


@lru_cache(maxsize=1)
def _calendar():
    import exchange_calendars
    return exchange_calendars.get_calendar('XNYS')


def market_clock_features(now_utc=None):
    u = now_utc or datetime.now(timezone.utc)
    if u.tzinfo is None:
        raise ValueError('Market clock requires a timezone-aware timestamp')
    u = u.astimezone(timezone.utc)
    local = u.astimezone(ZoneInfo('America/New_York'))
    result = {'is_weekend': local.weekday() >= 5, 'is_rth': None,
              'hours_to_us_open': None, 'is_us_holiday': None,
              'utc_weekday': u.weekday(), 'utc_hour': u.hour,
              'calendar_available': False}
    try:
        import pandas as pd
        cal = _calendar()
        day = local.date().isoformat()
        session = cal.date_to_session(day, direction='next')
        opened, closed = cal.session_open(session), cal.session_close(session)
        now = pd.Timestamp(u)
        regular = opened <= now < closed
        next_open = opened
        if now >= opened:
            next_open = cal.session_open(cal.next_session(session))
        result.update(is_rth=bool(regular), is_us_holiday=local.weekday()<5 and not cal.is_session(day),
                      hours_to_us_open=round((next_open-now).total_seconds()/3600, 2),
                      calendar_available=True)
    except (ImportError, ValueError, KeyError):
        pass  # Unknown is not an ordinary weekday or a clear calendar.
    return result
