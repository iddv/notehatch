import calendar
import datetime as dt
import re

_KEY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_UTC = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?Z")
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def parse_key(text):
    """Return a date for an exact, real YYYY-MM-DD key, else None."""
    if not isinstance(text, str) or not _KEY.fullmatch(text):
        return None
    try:
        return dt.date(int(text[:4]), int(text[5:7]), int(text[8:]))
    except ValueError:
        return None


def ordinal(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{({1: 'st', 2: 'nd', 3: 'rd'}).get(n % 10, 'th')}"


def title(d):
    return f"{_WEEKDAYS[d.weekday()]}, {_MONTHS[d.month - 1]} {ordinal(d.day)}, {d.year}"


def utc_to_ns(text):
    """Epoch nanoseconds for an ISO-8601 UTC string, or None if unparsable."""
    m = _UTC.fullmatch(text or "")
    if not m:
        return None
    try:
        y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
        secs = calendar.timegm(dt.datetime(y, mo, d, h, mi, s).timetuple())
    except ValueError:
        return None
    frac = (m.group(7) or "").ljust(9, "0")
    return secs * 10**9 + int(frac)


def noon_local_ns(d):
    return int(dt.datetime(d.year, d.month, d.day, 12).timestamp()) * 10**9


def local_display(text):
    ns = utc_to_ns(text) if text else None
    if ns is None:
        return "unknown"
    return dt.datetime.fromtimestamp(ns // 10**9).strftime("%Y-%m-%d %H:%M:%S")


def date_arg(text):
    """argparse type: validated YYYY-MM-DD string."""
    import argparse
    if parse_key(text) is None:
        raise argparse.ArgumentTypeError(
            f"invalid date {text!r}: expected a real calendar date as YYYY-MM-DD")
    return text
