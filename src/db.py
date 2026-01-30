from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple


# ---------------- Core DB ----------------

def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_db(con: sqlite3.Connection) -> None:
    cur = con.cursor()

    # Usuarios
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_user_id INTEGER UNIQUE,
        telegram_chat_id INTEGER,
        name TEXT,
        created_at TEXT NOT NULL
    )
    """)

    # Notas
    cur.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        note_datetime TEXT NOT NULL,
        text TEXT NOT NULL,
        tags TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    # Recordatorios (si ya existe en tu proyecto, lo respeta)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        message TEXT NOT NULL,
        schedule TEXT NOT NULL,
        timezone TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        last_run_at TEXT,
        next_run_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    # ---- TAREAS (robusto) ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        target_date TEXT NOT NULL, -- YYYY-MM-DD
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', -- pending/done/missed
        created_at TEXT NOT NULL,
        done_at TEXT,
        missed_at TEXT,
        updated_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    # Migración liviana: agregar columnas si faltan
    _ensure_column(con, "tasks", "done_at", "TEXT")
    _ensure_column(con, "tasks", "missed_at", "TEXT")
    _ensure_column(con, "tasks", "updated_at", "TEXT")

    # Índices
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_date ON tasks(user_id, target_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status)")

    con.commit()


def _ensure_column(con: sqlite3.Connection, table: str, col: str, coltype: str) -> None:
    cur = con.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = {row["name"] for row in cur.fetchall()}
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        con.commit()


# ---------------- Users ----------------

def upsert_user(con: sqlite3.Connection, telegram_user_id: int, telegram_chat_id: int, name: str) -> int:
    cur = con.cursor()
    cur.execute("SELECT id FROM users WHERE telegram_user_id = ?", (telegram_user_id,))
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE users SET telegram_chat_id=?, name=? WHERE telegram_user_id=?",
            (telegram_chat_id, name, telegram_user_id),
        )
        con.commit()
        return int(row["id"])

    cur.execute(
        "INSERT INTO users(telegram_user_id, telegram_chat_id, name, created_at) VALUES(?,?,?,?)",
        (telegram_user_id, telegram_chat_id, name, _now_iso()),
    )
    con.commit()
    return int(cur.lastrowid)


def get_user_id(con: sqlite3.Connection, telegram_user_id: int) -> Optional[int]:
    cur = con.cursor()
    cur.execute("SELECT id FROM users WHERE telegram_user_id=?", (telegram_user_id,))
    row = cur.fetchone()
    return int(row["id"]) if row else None


def get_user_chat_id(con: sqlite3.Connection, user_id: int) -> Optional[int]:
    cur = con.cursor()
    cur.execute("SELECT telegram_chat_id FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    return int(row["telegram_chat_id"]) if (row and row["telegram_chat_id"] is not None) else None


# ---------------- Notes ----------------

def add_note(con: sqlite3.Connection, user_id: int, note_datetime: str, text: str, tags: str | None = None) -> int:
    cur = con.cursor()
    cur.execute(
        "INSERT INTO notes(user_id, note_datetime, text, tags, created_at) VALUES(?,?,?,?,?)",
        (user_id, note_datetime, text, tags, _now_iso()),
    )
    con.commit()
    return int(cur.lastrowid)


def list_notes_by_date(con: sqlite3.Connection, user_id: int, yyyy_mm_dd: str) -> List[Dict[str, Any]]:
    cur = con.cursor()
    cur.execute(
        "SELECT * FROM notes WHERE user_id=? AND substr(note_datetime,1,10)=? ORDER BY note_datetime ASC",
        (user_id, yyyy_mm_dd),
    )
    return [dict(r) for r in cur.fetchall()]


def search_all(con: sqlite3.Connection, user_id: int, needle: str) -> Dict[str, List[Dict[str, Any]]]:
    like = f"%{needle}%"
    cur = con.cursor()

    cur.execute(
        "SELECT id, user_id, target_date, text, status FROM tasks WHERE user_id=? AND text LIKE ? ORDER BY target_date DESC, id DESC",
        (user_id, like),
    )
    tasks = [dict(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT id, user_id, note_datetime, text FROM notes WHERE user_id=? AND text LIKE ? ORDER BY note_datetime DESC, id DESC",
        (user_id, like),
    )
    notes = [dict(r) for r in cur.fetchall()]

    return {"tasks": tasks, "notes": notes}


# ---------------- Tasks (nuevo robusto) ----------------

def add_task(con: sqlite3.Connection, user_id: int, target_date: str, text: str) -> int:
    cur = con.cursor()
    cur.execute(
        "INSERT INTO tasks(user_id, target_date, text, status, created_at) VALUES(?,?,?,?,?)",
        (user_id, target_date, text, "pending", _now_iso()),
    )
    con.commit()
    return int(cur.lastrowid)


def get_task_by_id(con: sqlite3.Connection, user_id: int, task_id: int) -> Optional[Dict[str, Any]]:
    cur = con.cursor()
    cur.execute("SELECT * FROM tasks WHERE user_id=? AND id=?", (user_id, task_id))
    row = cur.fetchone()
    return dict(row) if row else None


def list_tasks_by_date(con: sqlite3.Connection, user_id: int, target_date: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    cur = con.cursor()
    if status:
        cur.execute(
            "SELECT * FROM tasks WHERE user_id=? AND target_date=? AND status=? ORDER BY id ASC",
            (user_id, target_date, status),
        )
    else:
        cur.execute(
            "SELECT * FROM tasks WHERE user_id=? AND target_date=? ORDER BY id ASC",
            (user_id, target_date),
        )
    return [dict(r) for r in cur.fetchall()]


def list_tasks_between(con: sqlite3.Connection, user_id: int, start_date: str, end_date: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    cur = con.cursor()
    if status:
        cur.execute(
            "SELECT * FROM tasks WHERE user_id=? AND target_date>=? AND target_date<=? AND status=? ORDER BY target_date ASC, id ASC",
            (user_id, start_date, end_date, status),
        )
    else:
        cur.execute(
            "SELECT * FROM tasks WHERE user_id=? AND target_date>=? AND target_date<=? ORDER BY target_date ASC, id ASC",
            (user_id, start_date, end_date),
        )
    return [dict(r) for r in cur.fetchall()]


def list_tasks_global(con: sqlite3.Connection, user_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    cur = con.cursor()
    if status:
        cur.execute(
            "SELECT * FROM tasks WHERE user_id=? AND status=? ORDER BY target_date DESC, id DESC",
            (user_id, status),
        )
    else:
        cur.execute(
            "SELECT * FROM tasks WHERE user_id=? ORDER BY target_date DESC, id DESC",
            (user_id,),
        )
    return [dict(r) for r in cur.fetchall()]


def mark_task_done_by_id(con: sqlite3.Connection, user_id: int, task_id: int) -> bool:
    now = _now_iso()
    cur = con.cursor()
    cur.execute(
        "UPDATE tasks SET status='done', done_at=?, updated_at=? WHERE user_id=? AND id=? AND status!='done'",
        (now, now, user_id, task_id),
    )
    con.commit()
    return cur.rowcount > 0


def mark_task_done_by_text(con: sqlite3.Connection, user_id: int, text: str, target_date: Optional[str] = None) -> Tuple[str, List[int]]:
    """
    Retorna (status, ids)
    status: 'none' | 'one' | 'many'
    """
    cur = con.cursor()
    params = [user_id]
    q = "SELECT id FROM tasks WHERE user_id=? AND status='pending' AND text=?"
    params.append(text)

    if target_date:
        q += " AND target_date=?"
        params.append(target_date)

    q += " ORDER BY id ASC"
    cur.execute(q, tuple(params))
    ids = [int(r["id"]) for r in cur.fetchall()]

    if not ids:
        return ("none", [])
    if len(ids) > 1:
        return ("many", ids)

    # exacto uno
    ok = mark_task_done_by_id(con, user_id, ids[0])
    return ("one", [ids[0]] if ok else [])


def update_task_text(con: sqlite3.Connection, user_id: int, task_id: int, new_text: str) -> bool:
    now = _now_iso()
    cur = con.cursor()
    cur.execute(
        "UPDATE tasks SET text=?, updated_at=? WHERE user_id=? AND id=?",
        (new_text, now, user_id, task_id),
    )
    con.commit()
    return cur.rowcount > 0


def update_task_date(con: sqlite3.Connection, user_id: int, task_id: int, new_date: str) -> bool:
    now = _now_iso()
    cur = con.cursor()
    cur.execute(
        "UPDATE tasks SET target_date=?, updated_at=? WHERE user_id=? AND id=?",
        (new_date, now, user_id, task_id),
    )
    con.commit()
    return cur.rowcount > 0


def update_task_date_text(con: sqlite3.Connection, user_id: int, task_id: int, new_date: str, new_text: str) -> bool:
    now = _now_iso()
    cur = con.cursor()
    cur.execute(
        "UPDATE tasks SET target_date=?, text=?, updated_at=? WHERE user_id=? AND id=?",
        (new_date, new_text, now, user_id, task_id),
    )
    con.commit()
    return cur.rowcount > 0


def delete_task_by_id(con: sqlite3.Connection, user_id: int, task_id: int) -> bool:
    cur = con.cursor()
    cur.execute("DELETE FROM tasks WHERE user_id=? AND id=?", (user_id, task_id))
    con.commit()
    return cur.rowcount > 0


def mark_tasks_missed_for_date(con: sqlite3.Connection, user_id: int, target_date: str) -> int:
    """
    pending -> missed para el día dado. Retorna cuántas cambió.
    """
    now = _now_iso()
    cur = con.cursor()
    cur.execute(
        "UPDATE tasks SET status='missed', missed_at=?, updated_at=? WHERE user_id=? AND target_date=? AND status='pending'",
        (now, now, user_id, target_date),
    )
    con.commit()
    return cur.rowcount


# ---------------- Reminders DB (para no romper tu proyecto) ----------------

def create_reminder(con: sqlite3.Connection, user_id: int, name: str, message: str, schedule: str, timezone: str = "America/Bogota") -> int:
    cur = con.cursor()
    cur.execute(
        "INSERT INTO reminders(user_id, name, message, schedule, timezone, active, created_at) VALUES(?,?,?,?,?,?,?)",
        (user_id, name, message, schedule, timezone, 1, _now_iso()),
    )
    con.commit()
    return int(cur.lastrowid)


def get_reminder(con: sqlite3.Connection, user_id: int, reminder_id: int) -> Optional[Dict[str, Any]]:
    cur = con.cursor()
    cur.execute("SELECT * FROM reminders WHERE user_id=? AND id=?", (user_id, reminder_id))
    row = cur.fetchone()
    return dict(row) if row else None


def get_reminder_by_id(con: sqlite3.Connection, reminder_id: int) -> Optional[Dict[str, Any]]:
    cur = con.cursor()
    cur.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def list_reminders(con: sqlite3.Connection, user_id: int, only_active: bool = True) -> List[Dict[str, Any]]:
    cur = con.cursor()
    if only_active:
        cur.execute("SELECT * FROM reminders WHERE user_id=? AND active=1 ORDER BY id ASC", (user_id,))
    else:
        cur.execute("SELECT * FROM reminders WHERE user_id=? ORDER BY id ASC", (user_id,))
    return [dict(r) for r in cur.fetchall()]


def update_reminder_active(con: sqlite3.Connection, user_id: int, reminder_id: int, active: int) -> bool:
    cur = con.cursor()
    cur.execute("UPDATE reminders SET active=? WHERE user_id=? AND id=?", (active, user_id, reminder_id))
    con.commit()
    return cur.rowcount > 0


def delete_reminder(con: sqlite3.Connection, user_id: int, reminder_id: int) -> bool:
    cur = con.cursor()
    cur.execute("DELETE FROM reminders WHERE user_id=? AND id=?", (user_id, reminder_id))
    con.commit()
    return cur.rowcount > 0


def update_reminder_run_times(con: sqlite3.Connection, reminder_id: int, last_run_at: Optional[str], next_run_at: Optional[str]) -> None:
    cur = con.cursor()
    cur.execute(
        "UPDATE reminders SET last_run_at=?, next_run_at=? WHERE id=?",
        (last_run_at, next_run_at, reminder_id),
    )
    con.commit()
