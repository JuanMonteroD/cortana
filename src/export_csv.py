from __future__ import annotations

import csv
import io
from typing import Sequence

def build_export_csv(tasks_rows, notes_rows) -> io.BytesIO:
    """
    Devuelve un archivo CSV en memoria (BytesIO) con 2 secciones: TAREAS y NOTAS.
    """
    out = io.StringIO()
    w = csv.writer(out)

    w.writerow(["SECCION", "fecha_objetivo", "texto", "estado", "creado_en", "hecho_en"])
    for r in tasks_rows:
        w.writerow(["TAREA", r["fecha_objetivo"], r["texto"], r["estado"], r["creado_en"], r["hecho_en"]])

    w.writerow([])
    w.writerow(["SECCION", "fecha", "texto", "creado_en"])
    for r in notes_rows:
        w.writerow(["NOTA", r["fecha"], r["texto"], r["creado_en"]])

    raw = out.getvalue().encode("utf-8-sig")
    bio = io.BytesIO(raw)
    bio.name = "asistente_export.csv"
    bio.seek(0)
    return bio
