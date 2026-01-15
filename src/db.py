from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional, Iterable

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_user_id INTEGER NOT NULL UNIQUE,
  telegram_chat_id INTEGER,
  nombre TEXT,
  creado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS configuracion (
  user_id INTEGER PRIMARY KEY,
  hora_manana TEXT NOT NULL,
  hora_tarde TEXT NOT NULL,
  hora_noche TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES usuarios(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tareas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  fecha_objetivo TEXT NOT NULL,         -- YYYY-MM-DD
  texto TEXT NOT NULL,
  estado TEXT NOT NULL CHECK(estado IN ('pendiente','hecho')),
  creado_en TEXT NOT NULL,
  hecho_en TEXT,
  FOREIGN KEY(user_id) REFERENCES usuarios(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tareas_user_fecha ON tareas(user_id, fecha_objetivo);
CREATE INDEX IF NOT EXISTS idx_tareas_user_estado ON tareas(user_id, estado);

CREATE TABLE IF NOT EXISTS notas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  fecha TEXT NOT NULL,                  -- ISO datetime
  texto TEXT NOT NULL,
  tags_opcional TEXT,
  creado_en TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES usuarios(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_notas_user_fecha ON notas(user_id, fecha);
"""


@dataclass(frozen=True)
class UserRow:
    id: int
    telegram_user_id: int
    telegram_chat_id: Optional[int]
    nombre: Optional[str]
    creado_en: str


@dataclass(frozen=True)
class ConfigRow:
    user_id: int
    hora_manana: str
    hora_tarde: str
    hora_noche: str


def _ensure_parent_dir(db_path: str) -> None:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def connect(db_path: str) -> sqlite3.Connection:
    _ensure_parent_dir(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)
    con.commit()


def get_or_create_user(con: sqlite3.Connection, telegram_user_id: int, nombre: str | None) -> UserRow:
    now = datetime.utcnow().isoformat(timespec="seconds")
    cur = con.execute("SELECT * FROM usuarios WHERE telegram_user_id = ?", (telegram_user_id,))
    row = cur.fetchone()
    if row:
        return UserRow(
            id=row["id"],
            telegram_user_id=row["telegram_user_id"],
            telegram_chat_id=row["telegram_chat_id"],
            nombre=row["nombre"],
            creado_en=row["creado_en"],
        )

    con.execute(
        "INSERT INTO usuarios (telegram_user_id, telegram_chat_id, nombre, creado_en) VALUES (?,?,?,?)",
        (telegram_user_id, None, nombre, now),
    )
    con.commit()
    return get_or_create_user(con, telegram_user_id, nombre)


def set_chat_id(con: sqlite3.Connection, telegram_user_id: int, chat_id: int) -> None:
    con.execute("UPDATE usuarios SET telegram_chat_id = ? WHERE telegram_user_id = ?", (chat_id, telegram_user_id))
    con.commit()


def get_user_by_telegram(con: sqlite3.Connection, telegram_user_id: int) -> Optional[UserRow]:
    cur = con.execute("SELECT * FROM usuarios WHERE telegram_user_id = ?", (telegram_user_id,))
    row = cur.fetchone()
    if not row:
        return None
    return UserRow(
        id=row["id"],
        telegram_user_id=row["telegram_user_id"],
        telegram_chat_id=row["telegram_chat_id"],
        nombre=row["nombre"],
        creado_en=row["creado_en"],
    )


def get_config(con: sqlite3.Connection, user_id: int) -> Optional[ConfigRow]:
    cur = con.execute("SELECT * FROM configuracion WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        return None
    return ConfigRow(
        user_id=row["user_id"],
        hora_manana=row["hora_manana"],
        hora_tarde=row["hora_tarde"],
        hora_noche=row["hora_noche"],
    )


def upsert_config(con: sqlite3.Connection, user_id: int, hora_manana: str, hora_tarde: str, hora_noche: str) -> None:
    con.execute(
        """
        INSERT INTO configuracion (user_id, hora_manana, hora_tarde, hora_noche)
        VALUES (?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
          hora_manana=excluded.hora_manana,
          hora_tarde=excluded.hora_tarde,
          hora_noche=excluded.hora_noche
        """,
        (user_id, hora_manana, hora_tarde, hora_noche),
    )
    con.commit()


def add_task(con: sqlite3.Connection, user_id: int, fecha_objetivo: date, texto: str) -> None:
    now = datetime.utcnow().isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO tareas (user_id, fecha_objetivo, texto, estado, creado_en, hecho_en) VALUES (?,?,?,?,?,NULL)",
        (user_id, fecha_objetivo.isoformat(), texto, "pendiente", now),
    )
    con.commit()


def mark_done_or_create(con: sqlite3.Connection, user_id: int, fecha_objetivo: date, texto: str) -> None:
    # Intenta marcar como hecho una tarea pendiente “parecida” hoy.
    cur = con.execute(
        """
        SELECT id FROM tareas
        WHERE user_id=? AND fecha_objetivo=? AND estado='pendiente' AND lower(texto) LIKE ?
        ORDER BY id DESC LIMIT 1
        """,
        (user_id, fecha_objetivo.isoformat(), f"%{texto.lower()}%"),
    )
    row = cur.fetchone()
    now = datetime.utcnow().isoformat(timespec="seconds")

    if row:
        con.execute(
            "UPDATE tareas SET estado='hecho', hecho_en=? WHERE id=?",
            (now, row["id"]),
        )
    else:
        con.execute(
            "INSERT INTO tareas (user_id, fecha_objetivo, texto, estado, creado_en, hecho_en) VALUES (?,?,?,?,?,?)",
            (user_id, fecha_objetivo.isoformat(), texto, "hecho", now, now),
        )
    con.commit()


def add_note(con: sqlite3.Connection, user_id: int, when_iso: str, texto: str) -> None:
    now = datetime.utcnow().isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO notas (user_id, fecha, texto, tags_opcional, creado_en) VALUES (?,?,?,?,?)",
        (user_id, when_iso, texto, None, now),
    )
    con.commit()


def list_tasks(con: sqlite3.Connection, user_id: int, fecha_objetivo: date, estado: str, limit: int = 20) -> list[str]:
    cur = con.execute(
        """
        SELECT texto FROM tareas
        WHERE user_id=? AND fecha_objetivo=? AND estado=?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, fecha_objetivo.isoformat(), estado, limit),
    )
    return [r["texto"] for r in cur.fetchall()]


def list_notes_today(con: sqlite3.Connection, user_id: int, day: date, limit: int = 20) -> list[str]:
    # notas.fecha es ISO datetime; filtramos por prefijo YYYY-MM-DD
    prefix = day.isoformat()
    cur = con.execute(
        """
        SELECT texto FROM notas
        WHERE user_id=? AND substr(fecha,1,10)=?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, prefix, limit),
    )
    return [r["texto"] for r in cur.fetchall()]


def search_everything(con: sqlite3.Connection, user_id: int, term: str, limit: int = 20) -> dict[str, list[str]]:
    t = term.lower().strip()
    like = f"%{t}%"

    cur_t = con.execute(
        """
        SELECT fecha_objetivo, estado, texto FROM tareas
        WHERE user_id=? AND lower(texto) LIKE ?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, like, limit),
    )
    tareas = [f"{r['fecha_objetivo']} · {r['estado']} · {r['texto']}" for r in cur_t.fetchall()]

    cur_n = con.execute(
        """
        SELECT fecha, texto FROM notas
        WHERE user_id=? AND lower(texto) LIKE ?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, like, limit),
    )
    notas = [f"{r['fecha']} · {r['texto']}" for r in cur_n.fetchall()]

    return {"tareas": tareas, "notas": notas}


def fetch_all_for_export(con: sqlite3.Connection, user_id: int) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    cur_t = con.execute(
        "SELECT fecha_objetivo, texto, estado, creado_en, hecho_en FROM tareas WHERE user_id=? ORDER BY id ASC",
        (user_id,),
    )
    cur_n = con.execute(
        "SELECT fecha, texto, creado_en FROM notas WHERE user_id=? ORDER BY id ASC",
        (user_id,),
    )
    return (cur_t.fetchall(), cur_n.fetchall())
