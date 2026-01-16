from __future__ import annotations

from datetime import date
from typing import Optional, Tuple


def parse_hhmm(text: str) -> Optional[Tuple[int, int]]:
    t = text.strip()
    if len(t) != 5 or t[2] != ":":
        return None
    hh, mm = t.split(":")
    if not (hh.isdigit() and mm.isdigit()):
        return None
    h = int(hh)
    m = int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


def parse_yyyy_mm_dd(text: str) -> Optional[date]:
    try:
        return date.fromisoformat(text.strip())
    except Exception:
        return None
