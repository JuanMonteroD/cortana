from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def init_db(con: sqlite3.Connection) -> None:
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_user_id INTEGER UNIQUE NOT NULL,
        telegram_chat_id INTEGER,
        name TEXT,
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        target_date TEXT NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL, -- pending/done
        created_at TEXT NOT NULL,
        done_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

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

    # Tabla minimalista para recordatorios
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        message TEXT NOT NULL,
        schedule TEXT NOT NULL,
        timezone TEXT NOT NULL DEFAULT 'America/Bogota',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_run_at TEXT,
        next_run_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_reminders_user_active ON reminders(user_id, active)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_date_status ON tasks(user_id, target_date, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notes_user_datetime ON notes(user_id, note_datetime)")

    con.commit()


# ---------------- USERS ----------------

def upsert_user(con: sqlite3.Connection, telegram_user_id: int, chat_id: int, name: str) -> int:
    cur = con.cursor()
    cur.execute("SELECT id FROM users WHERE telegram_user_id=?", (telegram_user_id,))
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE users SET telegram_chat_id=?, name=? WHERE telegram_user_id=?",
            (chat_id, name, telegram_user_id),
        )
        con.commit()
        return int(row["id"])

    cur.execute(
        "INSERT INTO users(telegram_user_id, telegram_chat_id, name, created_at) VALUES (?,?,?,?)",
        (telegram_user_id, chat_id, name, now_iso()),
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
    if not row:
        return None
    return row["telegram_chat_id"]


# ---------------- TASKS/NOTES (MVP) ----------------

def add_task(con: sqlite3.Connection, user_id: int, target_date: str, text: str) -> int:
    cur = con.cursor()
    cur.execute(
        "INSERT INTO tasks(user_id, target_date, text, status, created_at) VALUES (?,?,?,?,?)",
        (user_id, target_date, text.strip(), "pending", now_iso()),
    )
    con.commit()
    return int(cur.lastrowid)


def mark_task_done_by_text(con: sqlite3.Connection, user_id: int, text: str) -> bool:
    cur = con.cursor()
    cur.execute(
        "SELECT id FROM tasks WHERE user_id=? AND status='pending' AND text=? ORDER BY id DESC LIMIT 1",
        (user_id, text.strip()),
    )
    row = cur.fetchone()
    if not row:
        return False
    cur.execute(
        "UPDATE tasks SET status='done', done_at=? WHERE id=?",
        (now_iso(), int(row["id"])),
    )
    con.commit()
    return True


def list_tasks(con: sqlite3.Connection, user_id: int, target_date: str, status: Optional[str]) -> List[Dict[str, Any]]:
    cur = con.cursor()
    if status:
        cur.execute(
            "SELECT * FROM tasks WHERE user_id=? AND target_date=? AND status=? ORDER BY id DESC",
            (user_id, target_date, status),
        )
    else:
        cur.execute(
            "SELECT * FROM tasks WHERE user_id=? AND target_date=? ORDER BY id DESC",
            (user_id, target_date),
        )
    return [dict(r) for r in cur.fetchall()]


def add_note(con: sqlite3.Connection, user_id: int, note_datetime: str, text: str, tags: Optional[str] = None) -> int:
    cur = con.cursor()
    cur.execute(
        "INSERT INTO notes(user_id, note_datetime, text, tags, created_at) VALUES (?,?,?,?,?)",
        (user_id, note_datetime, text.strip(), tags, now_iso()),
    )
    con.commit()
    return int(cur.lastrowid)


def list_notes_by_date(con: sqlite3.Connection, user_id: int, yyyy_mm_dd: str) -> List[Dict[str, Any]]:
    cur = con.cursor()
    cur.execute(
        "SELECT * FROM notes WHERE user_id=? AND substr(note_datetime,1,10)=? ORDER BY note_datetime DESC",
        (user_id, yyyy_mm_dd),
    )
    return [dict(r) for r in cur.fetchall()]


def search_all(con: sqlite3.Connection, user_id: int, needle: str) -> Dict[str, List[Dict[str, Any]]]:
    n = f"%{needle.strip()}%"
    cur = con.cursor()
    cur.execute("SELECT * FROM tasks WHERE user_id=? AND text LIKE ? ORDER BY id DESC LIMIT 25", (user_id, n))
    tasks = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM notes WHERE user_id=? AND text LIKE ? ORDER BY id DESC LIMIT 25", (user_id, n))
    notes = [dict(r) for r in cur.fetchall()]
    return {"tasks": tasks, "notes": notes}


# ---------------- REMINDERS (Minimalista) ----------------

def create_reminder(con: sqlite3.Connection, user_id: int, name: str, message: str, schedule: str, timezone: str = "America/Bogota") -> int:
    cur = con.cursor()
    now = now_iso()
    cur.execute(
        """
        INSERT INTO reminders(user_id, name, message, schedule, timezone, active, created_at, updated_at)
        VALUES (?,?,?,?,?,1,?,?)
        """,
        (user_id, name.strip(), message.strip(), schedule.strip(), timezone.strip(), now, now),
    )
    con.commit()
    return int(cur.lastrowid)


def update_reminder_active(con: sqlite3.Connection, user_id: int, reminder_id: int, active: int) -> bool:
    cur = con.cursor()
    cur.execute(
        "UPDATE reminders SET active=?, updated_at=? WHERE id=? AND user_id=?",
        (1 if active else 0, now_iso(), reminder_id, user_id),
    )
    con.commit()
    return cur.rowcount > 0


def delete_reminder(con: sqlite3.Connection, user_id: int, reminder_id: int) -> bool:
    cur = con.cursor()
    cur.execute("DELETE FROM reminders WHERE id=? AND user_id=?", (reminder_id, user_id))
    con.commit()
    return cur.rowcount > 0


def get_reminder(con: sqlite3.Connection, user_id: int, reminder_id: int) -> Optional[Dict[str, Any]]:
    cur = con.cursor()
    cur.execute("SELECT * FROM reminders WHERE id=? AND user_id=?", (reminder_id, user_id))
    row = cur.fetchone()
    return dict(row) if row else None


def get_reminder_by_id(con: sqlite3.Connection, reminder_id: int) -> Optional[Dict[str, Any]]:
    cur = con.cursor()
    cur.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def list_reminders(con: sqlite3.Connection, user_id: int, only_active: bool = False) -> List[Dict[str, Any]]:
    cur = con.cursor()
    if only_active:
        cur.execute("SELECT * FROM reminders WHERE user_id=? AND active=1 ORDER BY id DESC", (user_id,))
    else:
        cur.execute("SELECT * FROM reminders WHERE user_id=? ORDER BY id DESC", (user_id,))
    return [dict(r) for r in cur.fetchall()]


def update_reminder_run_times(con: sqlite3.Connection, reminder_id: int, last_run_at: Optional[str], next_run_at: Optional[str]) -> None:
    cur = con.cursor()
    cur.execute(
        "UPDATE reminders SET last_run_at=COALESCE(?, last_run_at), next_run_at=?, updated_at=? WHERE id=?",
        (last_run_at, next_run_at, now_iso(), reminder_id),
    )
    con.commit()
