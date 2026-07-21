"""time_window_parser.py — risolutore deterministico di `time_window` (§7.9).

Riusabile da qualsiasi executor che accetti un'unica stringa per indicare un
intervallo temporale (read_events, find_files, find_messages, ...). Zero LLM,
solo parsing regex + datetime.

Forme accettate
---------------

Canoniche (§2.1):
  `last-Nd`, `next-Nd`, `today`

Periodi estesi:
  `yesterday`, `tomorrow`, `last-week`, `next-week`,
  `this-week`, `last-month`, `next-month`, `this-month`,
  `this-year`, `last-year`, `next-year`.

Singolo giorno:
  - ISO `YYYY-MM-DD` (intera giornata 00:00 -> 23:59:59)
  - Italiano `DD/MM/YY` o `DD/MM/YYYY` (anno a 2 cifre -> 2000..2099)

Intervalli:
  - ISO range `YYYY-MM-DD/YYYY-MM-DD`
  - Italiano «dal DD/MM al DD/MM», «dal DD/MM/YY al DD/MM/YYYY»,
    o trattino `12/3-15/3` (anno opzionale, si eredita dalla data piu'
    completa o, in mancanza, dall'anno corrente di `now`).

Convenzioni
-----------
- Output: tuple `(start_iso, end_iso)` con timezone Europe/Rome, formato
  `YYYY-MM-DDTHH:MM:SS+HH:MM` (offset esplicito, mai `Z`).
- L'intervallo per i singoli giorni e per i periodi multi-giorno usa
  `end` = `23:59:59` del giorno finale, per parita' lessicografica con le
  date complete.
- `this-week` parte dal lunedi'; `this-month`/`this-year` dal primo giorno.
- `now` e' iniettabile per test deterministici.
- ValueError viene sollevato per qualsiasi spec non riconosciuta o numero
  invalido (es. `last-0d`, `next--3d`, `2026-13-01`, `dal 32/2 al 1/3`).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")

# ---------------------------------------------------------------------------
# Regex (compilate una volta sola)
# ---------------------------------------------------------------------------

# Accept multiple separator styles for robustness: "next-30d" canonical,
# "next_30_days" / "next30days" / "next 30 d" all normalized.
_RE_LAST_ND = re.compile(r"^last[-_ ]?(\d+)[-_ ]?d(?:ays?)?$")
_RE_NEXT_ND = re.compile(r"^next[-_ ]?(\d+)[-_ ]?d(?:ays?)?$")
_RE_LAST_NH = re.compile(r"^last[-_ ]?(\d+)[-_ ]?h(?:ours?)?$")
_RE_NEXT_NH = re.compile(r"^next[-_ ]?(\d+)[-_ ]?h(?:ours?)?$")
_RE_LAST_NW = re.compile(r"^last[-_ ]?(\d+)[-_ ]?w(?:eeks?)?$")
_RE_NEXT_NW = re.compile(r"^next[-_ ]?(\d+)[-_ ]?w(?:eeks?)?$")
_RE_LAST_NM = re.compile(r"^last[-_ ]?(\d+)[-_ ]?m(?:in(?:utes?)?)?$")
_RE_NEXT_NM = re.compile(r"^next[-_ ]?(\d+)[-_ ]?m(?:in(?:utes?)?)?$")
_RE_LAST_NY = re.compile(r"^last[-_ ]?(\d+)[-_ ]?y(?:ears?)?$")
_RE_NEXT_NY = re.compile(r"^next[-_ ]?(\d+)[-_ ]?y(?:ears?)?$")
_RE_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_RE_ISO_YEAR = re.compile(r"^(\d{4})$")
_RE_ISO_YEAR_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_RE_ISO_RANGE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})/(\d{4})-(\d{2})-(\d{2})$"
)
_RE_IT_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")
_RE_IT_RANGE_DAL_AL = re.compile(
    r"^dal\s+(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?"
    r"\s+al\s+(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?$",
    re.IGNORECASE,
)
_RE_IT_RANGE_DASH = re.compile(
    r"^(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?"
    r"-(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?$"
)


def _aware(dt_naive_or_date, t_default=None):
    if isinstance(dt_naive_or_date, datetime):
        if dt_naive_or_date.tzinfo is None:
            return dt_naive_or_date.replace(tzinfo=ROME)
        return dt_naive_or_date.astimezone(ROME)
    if isinstance(dt_naive_or_date, date):
        return datetime.combine(
            dt_naive_or_date, t_default or time(0, 0, 0), tzinfo=ROME
        )
    raise TypeError(f"unexpected type {type(dt_naive_or_date).__name__}")


def _fmt(dt):
    """ISO 8601 con offset esplicito (`+HH:MM`), mai `Z`."""
    s = dt.astimezone(ROME).strftime("%Y-%m-%dT%H:%M:%S%z")
    return s[:-2] + ":" + s[-2:]


def _full_day(d):
    return _aware(d, time(0, 0, 0)), _aware(d, time(23, 59, 59))


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError as e:
        raise ValueError(
            f"invalid date {year:04d}-{month:02d}-{day:02d}: {e}"
        ) from None


def _expand_short_year(yy):
    if not 0 <= yy <= 99:
        raise ValueError(f"invalid 2-digit year: {yy}")
    return 2000 + yy


def _parse_it_year(token, fallback_year):
    if token is None or token == "":
        return fallback_year
    if len(token) == 2:
        return _expand_short_year(int(token))
    return int(token)


def _start_of_week(d):
    return d - timedelta(days=d.weekday())


def _start_of_month(d):
    return d.replace(day=1)


def _add_months(d, months):
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, 1)


def _resolve_canonical(spec, now):
    today = now.date()

    if spec == "today":
        return _full_day(today)
    if spec == "yesterday":
        return _full_day(today - timedelta(days=1))
    if spec == "tomorrow":
        return _full_day(today + timedelta(days=1))

    m = _RE_LAST_ND.match(spec)
    if m:
        n = int(m.group(1))
        if n <= 0:
            raise ValueError(f"last-Nd requires N>=1, got {spec!r}")
        s = now - timedelta(days=n)
        return _aware(s), _aware(now)
    m = _RE_NEXT_ND.match(spec)
    if m:
        n = int(m.group(1))
        if n <= 0:
            raise ValueError(f"next-Nd requires N>=1, got {spec!r}")
        e = now + timedelta(days=n)
        return _aware(now), _aware(e)
    # Hours
    m = _RE_LAST_NH.match(spec) or _RE_NEXT_NH.match(spec)
    if m:
        n = int(m.group(1))
        if n <= 0:
            raise ValueError(f"last/next-Nh requires N>=1, got {spec!r}")
        delta = timedelta(hours=n)
        if spec.startswith("last"):
            return _aware(now - delta), _aware(now)
        return _aware(now), _aware(now + delta)
    # Weeks (N*7 giorni). «ultime 2 settimane» → "last-2w".
    m = _RE_LAST_NW.match(spec) or _RE_NEXT_NW.match(spec)
    if m:
        n = int(m.group(1))
        if n <= 0:
            raise ValueError(f"last/next-Nw requires N>=1, got {spec!r}")
        delta = timedelta(weeks=n)
        if spec.startswith("last"):
            return _aware(now - delta), _aware(now)
        return _aware(now), _aware(now + delta)
    # Months (approx 30 giorni). Pattern §2.1: l'utente dice "prossimi 3
    # mesi" → "next-3m"; il delta e' calcolato in giorni (3*30=90).
    m = _RE_LAST_NM.match(spec) or _RE_NEXT_NM.match(spec)
    if m:
        n = int(m.group(1))
        if n <= 0:
            raise ValueError(f"last/next-Nm requires N>=1, got {spec!r}")
        delta = timedelta(days=n * 30)
        if spec.startswith("last"):
            return _aware(now - delta), _aware(now)
        return _aware(now), _aware(now + delta)
    # Years (approx 365 giorni)
    m = _RE_LAST_NY.match(spec) or _RE_NEXT_NY.match(spec)
    if m:
        n = int(m.group(1))
        if n <= 0:
            raise ValueError(f"last/next-Ny requires N>=1, got {spec!r}")
        delta = timedelta(days=n * 365)
        if spec.startswith("last"):
            return _aware(now - delta), _aware(now)
        return _aware(now), _aware(now + delta)

    if spec == "this-week":
        mon = _start_of_week(today)
        sun = mon + timedelta(days=6)
        return _aware(mon, time(0, 0, 0)), _aware(sun, time(23, 59, 59))
    if spec == "last-week":
        mon_this = _start_of_week(today)
        mon_prev = mon_this - timedelta(days=7)
        sun_prev = mon_prev + timedelta(days=6)
        return (
            _aware(mon_prev, time(0, 0, 0)),
            _aware(sun_prev, time(23, 59, 59)),
        )
    if spec == "next-week":
        mon_next = _start_of_week(today) + timedelta(days=7)
        sun_next = mon_next + timedelta(days=6)
        return (
            _aware(mon_next, time(0, 0, 0)),
            _aware(sun_next, time(23, 59, 59)),
        )

    if spec == "this-month":
        first = _start_of_month(today)
        next_first = _add_months(first, 1)
        last = next_first - timedelta(days=1)
        return _aware(first, time(0, 0, 0)), _aware(last, time(23, 59, 59))
    if spec == "last-month":
        first_prev = _add_months(_start_of_month(today), -1)
        first_this = _start_of_month(today)
        last_prev = first_this - timedelta(days=1)
        return (
            _aware(first_prev, time(0, 0, 0)),
            _aware(last_prev, time(23, 59, 59)),
        )
    if spec == "next-month":
        first_next = _add_months(_start_of_month(today), 1)
        first_after = _add_months(first_next, 1)
        last_next = first_after - timedelta(days=1)
        return (
            _aware(first_next, time(0, 0, 0)),
            _aware(last_next, time(23, 59, 59)),
        )

    if spec == "this-year":
        first = date(today.year, 1, 1)
        last = date(today.year, 12, 31)
        return _aware(first, time(0, 0, 0)), _aware(last, time(23, 59, 59))
    if spec == "last-year":
        y = today.year - 1
        return (
            _aware(date(y, 1, 1), time(0, 0, 0)),
            _aware(date(y, 12, 31), time(23, 59, 59)),
        )
    if spec == "next-year":
        y = today.year + 1
        return (
            _aware(date(y, 1, 1), time(0, 0, 0)),
            _aware(date(y, 12, 31), time(23, 59, 59)),
        )

    return None


def _resolve_iso(spec):
    m = _RE_ISO_RANGE.match(spec)
    if m:
        y1, mo1, d1, y2, mo2, d2 = map(int, m.groups())
        a = _safe_date(y1, mo1, d1)
        b = _safe_date(y2, mo2, d2)
        if b < a:
            a, b = b, a
        return _aware(a, time(0, 0, 0)), _aware(b, time(23, 59, 59))
    m = _RE_ISO_DAY.match(spec)
    if m:
        y, mo, d = map(int, m.groups())
        return _full_day(_safe_date(y, mo, d))
    # Anno solo `YYYY` → tutto l'anno (1 gennaio 00:00 → 31 dicembre 23:59:59).
    m = _RE_ISO_YEAR.match(spec)
    if m:
        y = int(m.group(1))
        a = _safe_date(y, 1, 1)
        b = _safe_date(y, 12, 31)
        return _aware(a, time(0, 0, 0)), _aware(b, time(23, 59, 59))
    # Anno-mese `YYYY-MM` → tutto il mese.
    m = _RE_ISO_YEAR_MONTH.match(spec)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        a = _safe_date(y, mo, 1)
        # Ultimo giorno del mese: vai al primo del mese successivo - 1 giorno.
        if mo == 12:
            b_next = _safe_date(y + 1, 1, 1)
        else:
            b_next = _safe_date(y, mo + 1, 1)
        from datetime import timedelta
        b = b_next - timedelta(days=1)
        return _aware(a, time(0, 0, 0)), _aware(b, time(23, 59, 59))
    return None


def _resolve_italian(spec, now):
    m = _RE_IT_DAY.match(spec)
    if m:
        dd, mm, yy = m.groups()
        y = _parse_it_year(yy, now.year)
        return _full_day(_safe_date(y, int(mm), int(dd)))

    m = _RE_IT_RANGE_DAL_AL.match(spec)
    if m:
        d1, mo1, y1, d2, mo2, y2 = m.groups()
        return _build_it_range(d1, mo1, y1, d2, mo2, y2, now)

    m = _RE_IT_RANGE_DASH.match(spec)
    if m:
        d1, mo1, y1, d2, mo2, y2 = m.groups()
        return _build_it_range(d1, mo1, y1, d2, mo2, y2, now)

    return None


def _build_it_range(d1, mo1, y1, d2, mo2, y2, now):
    if y1 is None and y2 is None:
        ya = yb = now.year
    elif y1 is None:
        yb = _parse_it_year(y2, now.year)
        ya = yb
    elif y2 is None:
        ya = _parse_it_year(y1, now.year)
        yb = ya
    else:
        ya = _parse_it_year(y1, now.year)
        yb = _parse_it_year(y2, now.year)
    a = _safe_date(ya, int(mo1), int(d1))
    b = _safe_date(yb, int(mo2), int(d2))
    if b < a:
        a, b = b, a
    return _aware(a, time(0, 0, 0)), _aware(b, time(23, 59, 59))


def _normalize_llm_spec(s: str) -> str:
    """Normalizza le varianti che l'LLM INVENTA verso le forme canoniche
    (deterministico §7.9). L'LLM emette spesso `now_plus_7d`/`in 3 days`/
    `prossimi 7 giorni` invece di `next-7d` → qui le canonicalizziamo cosi'
    il resolver le accetta (fix q11 read_events 4/6/2026). Generale: vale per
    ogni executor che usa parse_time_window. Non-match → invariato."""
    t = s.strip().lower()
    m = (re.match(r"^now[\s_+]*plus[\s_]*(\d+)[\s_]*d(?:ays?)?$", t)
         or re.match(r"^now\s*\+\s*(\d+)\s*d(?:ays?)?$", t)
         or re.match(r"^(?:in|fra|tra)[\s_]+(\d+)[\s_]+(?:days?|giorni)$", t)
         or re.match(r"^prossim[ie][\s_]+(\d+)[\s_]+giorni$", t))
    if m:
        return f"next-{m.group(1)}d"
    m = (re.match(r"^now[\s_]*minus[\s_]*(\d+)[\s_]*d(?:ays?)?$", t)
         or re.match(r"^now\s*-\s*(\d+)\s*d(?:ays?)?$", t)
         or re.match(r"^(\d+)[\s_]+(?:days?[\s_]+ago|giorni[\s_]+fa)$", t)
         or re.match(r"^ultim[ie][\s_]+(\d+)[\s_]+giorni$", t))
    if m:
        return f"last-{m.group(1)}d"
    return s


def parse_time_window(spec, now=None):
    """Risolve `spec` in `(start_iso, end_iso)` aware su Europe/Rome.

    Solleva ValueError se `spec` non e' riconosciuta o se contiene un
    parametro non valido (giorno 32, mese 13, N<=0, eccetera).
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("time_window must be a non-empty string")
    s = _normalize_llm_spec(spec.strip())

    if now is None:
        now = datetime.now(tz=ROME)
    else:
        now = _aware(now)

    out = _resolve_canonical(s, now)
    if out is None:
        out = _resolve_iso(s)
    if out is None:
        out = _resolve_italian(s, now)
    if out is None:
        raise ValueError(f"unknown time_window: {spec!r}")

    start, end = out
    return _fmt(start), _fmt(end)
