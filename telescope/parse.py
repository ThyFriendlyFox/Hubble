"""Shared value-coercion helpers for turning raw fetched API data into clean
scoreable numbers.

Hubble, Kepler and Simons each grew their own copy of "safely coerce this to
a float, or None" (hubble.py's caught NaN, kepler.py's/simons.py's didn't) --
the same convention that put shared HTTP concerns in telescope/http.py and
time-series analytics in telescope/series.py applies here too.
"""


def to_float(v):
    """float(v), or None if v is missing, the wrong type, or NaN.

    NaN matters, not just theoretical: float("nan") is a valid float, so a
    raw API value of "NaN" (some sources use it as a null sentinel) would
    otherwise silently pass through as a real-looking number instead of the
    absence of one.
    """
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None
