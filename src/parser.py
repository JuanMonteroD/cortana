from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

PREFIX_RE = re.compile(r"^\s*(hice|pendiente|nota|mañana)\s*:\s*(.+)\s*$", re.IGNORECASE)
BUSCAR_RE = re.compile(r"^\s*buscar\s*:\s*(.+)\s*$", re.IGNORECASE)

@dataclass(frozen=True)
class Parsed:
    kind: str
    payload: str

def parse_message(text: str) -> Optional[Parsed]:
    if not text:
        return None

    clean = text.strip()

    if clean.lower() in {"resumen", "/resumen"}:
        return Parsed(kind="resumen", payload="")
    if clean.lower() in {"pendientes", "pendiente"}:
        return Parsed(kind="pendientes", payload="")
    if clean.lower() in {"hechos hoy", "hechos"}:
        return Parsed(kind="hechos_hoy", payload="")

    m = BUSCAR_RE.match(clean)
    if m:
        return Parsed(kind="buscar", payload=m.group(1).strip())

    m = PREFIX_RE.match(clean)
    if m:
        k = m.group(1).lower()
        payload = m.group(2).strip()
        # normaliza "mañana"
        if k == "mañana":
            k = "manana"
        return Parsed(kind=k, payload=payload)

    return None


def parse_hhmm(s: str) -> Optional[tuple[int, int]]:
    s = s.strip()
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))
