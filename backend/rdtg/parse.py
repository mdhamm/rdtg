"""EARS (Easy Approach to Requirements Syntax) parser.

Accepts the standard EARS templates plus the trailing form engineers often write:

    When T [and C...], the software shall R [within N ms].
    While S, the software shall R.
    While S, when T, the software shall R.
    If C, then the software shall R.
    Where F, the software shall R.
    The software shall R [when|while|if ...] [within N ms].
    The software shall R.

Conditions:  SIG is V | SIG is not V | SIG is greater than N | SIG > N | SIG is set | ...
Responses:   set SIG to V | set SIG | clear SIG | report SIG as V | SIG is V
"""
from __future__ import annotations

import re
from typing import Optional

from .model import Catalog, Condition, Pattern, Requirement, Signal, SignalKind, Timing

SIG = r"[A-Za-z_][\w.]*"

_UNITS = {"ms": 1.0, "msec": 1.0, "millisecond": 1.0, "milliseconds": 1.0,
          "s": 1000.0, "sec": 1000.0, "second": 1000.0, "seconds": 1000.0}
_UNIT = r"(ms|msec|milliseconds?|s|sec|seconds?)\b"
_NUM = r"(\d+(?:\.\d+)?)"

# Ordered longest-first so "is greater than or equal to" wins over "is".
_OPS: list[tuple[str, str]] = [
    (r"is\s+greater\s+than\s+or\s+equal\s+to", ">="),
    (r"is\s+less\s+than\s+or\s+equal\s+to", "<="),
    (r"is\s+at\s+least", ">="),
    (r"is\s+at\s+most", "<="),
    (r"is\s+greater\s+than|is\s+above|exceeds", ">"),
    (r"is\s+less\s+than|is\s+below", "<"),
    (r"is\s+not\s+equal\s+to|is\s+not|does\s+not\s+equal|!=", "!="),
    (r"is\s+equal\s+to|equals|is|==|=", "=="),
    (r">=", ">="), (r"<=", "<="), (r">", ">"), (r"<", "<"),
]
_COND_RE = [(re.compile(rf"^(?P<sig>{SIG})\s*(?:{pat})\s*(?P<val>.+)$", re.I), op) for pat, op in _OPS]

_TRUE_WORDS = {"true", "set", "active", "asserted", "enabled", "1", "yes"}
_FALSE_WORDS = {"false", "clear", "cleared", "inactive", "deasserted", "disabled", "0", "no", "reset"}


class ParseError(Exception):
    pass


def _ms(num: str, unit: str) -> float:
    return float(num) * _UNITS[unit.lower()]


def extract_timing(text: str) -> tuple[str, Optional[Timing]]:
    timing = Timing()
    found = False
    m = re.search(rf"\s*,?\s*between\s+{_NUM}\s*(?:{_UNIT})?\s+and\s+{_NUM}\s*{_UNIT}", text, re.I)
    if m:
        unit = m.group(2) or m.group(4)
        timing.min_ms, timing.max_ms = _ms(m.group(1), unit), _ms(m.group(3), m.group(4))
        text, found = text[:m.start()] + text[m.end():], True
    m = re.search(rf"\s*,?\s*within\s+{_NUM}\s*{_UNIT}", text, re.I)
    if m:
        timing.max_ms = _ms(m.group(1), m.group(2))
        text, found = text[:m.start()] + text[m.end():], True
    m = re.search(rf"\s*,?\s*(?:no\s+(?:sooner|earlier)\s+than|after\s+at\s+least)\s+{_NUM}\s*{_UNIT}", text, re.I)
    if m:
        timing.min_ms = _ms(m.group(1), m.group(2))
        text, found = text[:m.start()] + text[m.end():], True
    return text.strip(), (timing if found else None)


def parse_value(raw: str):
    s = raw.strip().strip("'\"")
    low = s.lower()
    if low in ("true",):
        return True
    if low in ("false",):
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def parse_condition(text: str) -> Condition:
    t = text.strip().rstrip(",").strip()
    t = re.sub(r"^(?:the\s+)", "", t, flags=re.I)
    m = re.match(rf"^({SIG})\s+is\s+(set|active|asserted|enabled|cleared|clear|inactive|deasserted|disabled|true|false)$", t, re.I)
    if m:
        return Condition(signal=m.group(1), op="==", value=m.group(2).lower() in _TRUE_WORDS)
    for rx, op in _COND_RE:
        m = rx.match(t)
        if m:
            val = m.group("val").strip()
            val = re.sub(rf"\s*{_UNIT}$", "", val, flags=re.I) if re.match(_NUM, val) else val
            val = re.sub(r"\s*(psi|rpm|deg|v|a|%)$", "", val, flags=re.I)
            return Condition(signal=m.group("sig"), op=op, value=parse_value(val))
    if re.fullmatch(SIG, t):
        return Condition(signal=t, op="==", value=True)
    raise ParseError(f"cannot parse condition: '{text.strip()}'")


def parse_conditions(text: str) -> list[Condition]:
    parts = [p for p in re.split(r"\s*,?\s+and\s+|\s*,\s*", text.strip(), flags=re.I) if p.strip()]
    return [parse_condition(p) for p in parts]


def parse_response_part(text: str, prev_verb: Optional[str]) -> tuple[Condition, Optional[str]]:
    t = text.strip()
    m = re.match(rf"^(set|drive|command)\s+(?:the\s+)?({SIG})\s+to\s+(.+)$", t, re.I)
    if m:
        return Condition(signal=m.group(2), value=parse_value(m.group(3))), "set"
    m = re.match(rf"^(set|assert|raise|enable)\s+(?:the\s+)?({SIG})$", t, re.I)
    if m:
        return Condition(signal=m.group(2), value=True), "set"
    m = re.match(rf"^(clear|reset|deassert|disable|lower)\s+(?:the\s+)?({SIG})$", t, re.I)
    if m:
        return Condition(signal=m.group(2), value=False), "clear"
    m = re.match(rf"^(report|output|transmit|send|indicate)\s+(?:the\s+)?({SIG})\s+(?:as|=|with\s+value\s+of|with\s+value)\s+(.+)$", t, re.I)
    if m:
        return Condition(signal=m.group(2), value=parse_value(m.group(3))), "set"
    m = re.match(rf"^({SIG})\s+to\s+(.+)$", t, re.I)
    if m and prev_verb == "set":
        return Condition(signal=m.group(1), value=parse_value(m.group(2))), "set"
    m = re.match(rf"^({SIG})$", t)
    if m and prev_verb in ("set", "clear"):
        return Condition(signal=m.group(1), value=prev_verb == "set"), prev_verb
    m = re.match(r"^(?:ensure|maintain|keep)\s+(?:that\s+)?(.+)$", t, re.I)
    return parse_condition(m.group(1) if m else t), None


def parse_response(text: str) -> list[Condition]:
    out, verb = [], None
    for part in re.split(r"\s*,?\s+and\s+", text.strip(), flags=re.I):
        cond, verb = parse_response_part(part, verb)
        out.append(cond)
    return out


def parse_requirement(req_id: str, text: str) -> Requirement:
    req = Requirement(id=req_id, text=text)
    try:
        _parse_into(req, text)
        req.parsed = True
    except ParseError as e:
        req.parse_error = str(e)
    return req


def _parse_into(req: Requirement, text: str) -> None:
    body = " ".join(text.split()).rstrip(".").strip()
    body, timing = extract_timing(body)
    req.timing = timing
    parts = re.split(r"\s+shall\s+", body, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        raise ParseError("no 'shall' found — requirement must contain '... shall ...'")
    left, right = parts

    state = trig = cond = feat = None
    pattern = Pattern.ubiquitous
    if m := re.match(r"^while\s+(.+?),\s*when\s+(.+?),\s*(.+)$", left, re.I):
        state, trig, pattern = m.group(1), m.group(2), Pattern.complex
    elif m := re.match(r"^when\s+(.+?),\s*(.+)$", left, re.I):
        trig, pattern = m.group(1), Pattern.event
    elif m := re.match(r"^while\s+(.+?),\s*(.+)$", left, re.I):
        state, pattern = m.group(1), Pattern.state
    elif m := re.match(r"^if\s+(.+?),\s*(?:then\s+)?(.+)$", left, re.I):
        cond, pattern = m.group(1), Pattern.unwanted
    elif m := re.match(r"^where\s+(.+?),\s*(.+)$", left, re.I):
        feat, pattern = m.group(1), Pattern.optional

    # Trailing clause: "The software shall R when T and C"
    if m := re.match(r"^(.+?)\s+(when|while|if|whenever|upon)\s+(.+)$", right, re.I):
        right = m.group(1)
        kw = m.group(2).lower()
        if kw == "while":
            state = f"{state} and {m.group(3)}" if state else m.group(3)
            pattern = Pattern.complex if trig else Pattern.state
        elif kw == "if":
            cond, pattern = m.group(3), Pattern.unwanted
        else:
            trig = m.group(3)
            pattern = Pattern.complex if state else Pattern.event

    req.pattern = pattern
    req.response = parse_response(right)
    conditions: list[Condition] = []
    if state:
        conditions += parse_conditions(state)
    if feat:
        conditions += parse_conditions(feat)
    for src in (trig, cond):
        if src:
            parsed = parse_conditions(src)
            req.trigger = parsed[0]
            conditions += parsed[1:]
    req.conditions = conditions


# --------------------------------------------------------------------------- resolution


def resolve_signal_name(name: str, signals: dict[str, Signal]) -> Optional[str]:
    if name in signals:
        return name
    low = name.lower()
    return next((s for s in signals if s.lower() == low), None)


def resolve_value(value, sig: Signal):
    """Coerce a parsed value into the signal's domain. Raises ValueError if impossible."""
    if sig.kind == SignalKind.discrete:
        if isinstance(value, bool):
            return value
        s = str(value).lower()
        if s in _TRUE_WORDS or s == "on":
            return True
        if s in _FALSE_WORDS or s == "off":
            return False
        raise ValueError(f"'{value}' is not a boolean value for {sig.name}")
    if sig.kind == SignalKind.enum:
        for v in sig.values or []:
            if str(v).lower() == str(value).lower():
                return v
        raise ValueError(f"'{value}' is not one of {sig.values} for {sig.name}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{value}' is not numeric for analog signal {sig.name}")
    return value


def resolve(req: Requirement, catalog: Catalog) -> list[str]:
    """Resolve signal names/values in-place. Returns a list of error messages."""
    signals = catalog.signals()
    errors = []
    for c in req.stimuli() + list(req.response):
        name = resolve_signal_name(c.signal, signals)
        if name is None:
            errors.append(f"unknown signal '{c.signal}'")
            continue
        c.signal = name
        try:
            c.value = resolve_value(c.value, signals[name])
        except ValueError as e:
            errors.append(str(e))
    return errors
