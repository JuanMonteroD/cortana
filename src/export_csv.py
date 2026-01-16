from __future__ import annotations

import csv
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Tuple

import db as dbmod


def export_user_data_to_csv(con, user_id: int) -> Tuple[str, str]:
    """
    Retorna (filepath, filename)
    """
    # Sacar tareas y notas
    cur = con.cursor()
    cur.execute("SELECT * FROM tasks WHERE user_id=? ORDER BY id DESC", (user_id,))
    tasks = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM notes WHERE user_id=? ORDER BY note_datetime DESC", (user_id,))
    notes = [dict(r) for r in cur.fetchall()]

    tmp = NamedTemporaryFile(delete=False, suffix=".csv", mode="w", newline="", encoding="utf-8")
    writer = csv.writer(tmp)

    writer.writerow(["TYPE", "DATE", "TEXT", "STATUS", "CREATED_AT", "DONE_AT", "NOTE_DATETIME"])
    for t in tasks:
        writer.writerow(["TASK", t["target_date"], t["text"], t["status"], t["created_at"], t.get("done_at"), ""])
    for n in notes:
        writer.writerow(["NOTE", n["note_datetime"][:10], n["text"], "", n["created_at"], "", n["note_datetime"]])

    tmp.close()
    filename = "export_asistente.csv"
    return tmp.name, filename
