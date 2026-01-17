from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from typing import Optional

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from telegram.ext import Application
from zoneinfo import ZoneInfo

import db as dbmod
from parser import parse_hhmm, parse_yyyy_mm_dd

log = logging.getLogger("cortana.reminders")

DAY_TOKENS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_DOW = "mon,tue,wed,thu,fri"
WEEKEND_DOW = "sat,sun"
EVERYDAY_DOW = "mon,tue,wed,thu,fri,sat,sun"


def get_tz(tzname: str) -> ZoneInfo | dt_timezone:
    try:
        return ZoneInfo(tzname)
    except Exception:
        # Fallback para no reventar si falta tzdata
        return dt_timezone.utc


@dataclass(frozen=True)
class ParsedSchedule:
    kind: str  # WEEKDAY/WEEKEND/DAYS/ONCE/EVERYDAY
    dow: Optional[str]          # "mon,tue"...
    date_once: Optional[str]    # "YYYY-MM-DD"
    hhmm: str                   # "HH:MM"


def parse_schedule(schedule: str) -> ParsedSchedule:
    s = schedule.strip()

    # Permitir mayúsculas/minúsculas en el "tipo"
    up = s.upper()

    if up.startswith("WEEKDAY@"):
        hhmm = s.split("@", 1)[1]
        _validate_time(hhmm)
        return ParsedSchedule(kind="WEEKDAY", dow=WEEKDAY_DOW, date_once=None, hhmm=hhmm)

    if up.startswith("WEEKEND@"):
        hhmm = s.split("@", 1)[1]
        _validate_time(hhmm)
        return ParsedSchedule(kind="WEEKEND", dow=WEEKEND_DOW, date_once=None, hhmm=hhmm)

    if up.startswith("EVERYDAY@") or up.startswith("DAILY@"):
        hhmm = s.split("@", 1)[1]
        _validate_time(hhmm)
        return ParsedSchedule(kind="EVERYDAY", dow=EVERYDAY_DOW, date_once=None, hhmm=hhmm)

    if up.startswith("DAYS@"):
        parts = s.split("@")
        # DAYS@mon,tue@HH:MM
        if len(parts) != 3:
            raise ValueError("Formato DAYS invalido. Usa: DAYS@mon,tue@HH:MM")
        _, days_part, hhmm = parts
        _validate_time(hhmm)
        dow = _validate_days_part(days_part)
        return ParsedSchedule(kind="DAYS", dow=dow, date_once=None, hhmm=hhmm)

    if up.startswith("ONCE@"):
        parts = s.split("@")
        # ONCE@YYYY-MM-DD@HH:MM
        if len(parts) != 3:
            raise ValueError("Formato ONCE invalido. Usa: ONCE@YYYY-MM-DD@HH:MM")
        _, date_part, hhmm = parts
        if not parse_yyyy_mm_dd(date_part):
            raise ValueError("Fecha invalida. Usa YYYY-MM-DD")
        _validate_time(hhmm)
        return ParsedSchedule(kind="ONCE", dow=None, date_once=date_part, hhmm=hhmm)

    raise ValueError(
        "Schedule invalido. Soportados: WEEKDAY@HH:MM, WEEKEND@HH:MM, EVERYDAY@HH:MM, "
        "DAYS@mon,tue@HH:MM, ONCE@YYYY-MM-DD@HH:MM"
    )


def _validate_time(hhmm: str) -> None:
    if not parse_hhmm(hhmm):
        raise ValueError("Hora invalida. Usa HH:MM (ej: 08:00)")


def _validate_days_part(days_part: str) -> str:
    raw = days_part.strip().lower()
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        raise ValueError("DAYS requiere al menos un dia (mon,tue,...)")
    for t in tokens:
        if t not in DAY_TOKENS:
            raise ValueError(f"Dia invalido '{t}'. Usa: {', '.join(DAY_TOKENS)}")
    # CronTrigger acepta "mon,tue" tal cual
    return ",".join(tokens)


def job_id_for(user_id: int, reminder_id: int) -> str:
    return f"rem_{user_id}_{reminder_id}"


def build_trigger(parsed: ParsedSchedule, tzname: str):
    tz = get_tz(tzname)

    hhmm_parsed = parse_hhmm(parsed.hhmm)
    assert hhmm_parsed is not None
    hour, minute = hhmm_parsed

    if parsed.kind in ("WEEKDAY", "WEEKEND", "DAYS", "EVERYDAY"):
        return CronTrigger(
            day_of_week=parsed.dow,
            hour=hour,
            minute=minute,
            second=0,
            timezone=tz,
        )

    # ONCE
    d = parse_yyyy_mm_dd(parsed.date_once or "")
    if d is None:
        raise ValueError("Fecha invalida para ONCE")
    run_dt = datetime(d.year, d.month, d.day, hour, minute, 0, tzinfo=tz)
    return DateTrigger(run_date=run_dt, timezone=tz)


def schedule_one(app: Application, con, reminder_row: dict, chat_id: int, misfire_grace_seconds: int = 300) -> None:
    scheduler = app.job_queue.scheduler  # APScheduler AsyncIOScheduler

    user_id = int(reminder_row["user_id"])
    rid = int(reminder_row["id"])
    jid = job_id_for(user_id, rid)

    if int(reminder_row["active"]) != 1:
        unschedule_one(app, user_id, rid)
        return

    parsed = parse_schedule(reminder_row["schedule"])
    trigger = build_trigger(parsed, reminder_row.get("timezone") or "America/Bogota")

    def _runner():
        loop = app.bot_data.get("loop")

        if loop is None:
            # fallback (muy raro)
            try:
                app.create_task(_run_reminder_async(app, con, rid, chat_id))
            except RuntimeError as e:
                log.error("No hay event loop disponible: %s", e)
            return

        fut = asyncio.run_coroutine_threadsafe(
            _run_reminder_async(app, con, rid, chat_id),
            loop,
        )

        def _done_callback(f):
            try:
                f.result()
            except Exception as ex:
                log.exception("Error ejecutando reminder async: %s", ex)

        fut.add_done_callback(_done_callback)

    job = scheduler.add_job(
        _runner,
        trigger=trigger,
        id=jid,
        name=jid,
        replace_existing=True,
        misfire_grace_time=misfire_grace_seconds,
        coalesce=True,
        max_instances=1,
    )

    # En algunos entornos (Termux) el scheduler aún no ha iniciado y Job no tiene next_run_time.
    # Lo dejamos en NULL y luego lo calculamos cuando el scheduler esté corriendo.
    dbmod.update_reminder_run_times(con, rid, last_run_at=None, next_run_at=None)


def unschedule_one(app: Application, user_id: int, reminder_id: int) -> None:
    scheduler = app.job_queue.scheduler
    jid = job_id_for(user_id, reminder_id)
    try:
        scheduler.remove_job(jid)
    except Exception:
        pass


def schedule_all_active(app: Application, con) -> None:
    # programar todos los recordatorios activos de todos los usuarios
    cur = con.cursor()
    cur.execute("SELECT * FROM reminders WHERE active=1")
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        user_id = int(r["user_id"])
        chat_id = dbmod.get_user_chat_id(con, user_id)
        if not chat_id:
            continue
        schedule_one(app, con, r, chat_id)


async def _run_reminder_async(app: Application, con, reminder_id: int, chat_id: int) -> None:
    row = dbmod.get_reminder_by_id(con, reminder_id)
    if not row:
        return
    if int(row["active"]) != 1:
        return

    name = row["name"]
    message = row["message"]
    schedule = row["schedule"]

    text = f"⏰ {name}: {message}"
    await app.bot.send_message(chat_id=chat_id, text=text)

    # actualizar last_run_at
    last = datetime.now().isoformat(timespec="seconds")

    # si es ONCE, auto-desactivar y cancelar job
    parsed = parse_schedule(schedule)
    if parsed.kind == "ONCE":
        dbmod.update_reminder_active(con, int(row["user_id"]), int(row["id"]), 0)
        unschedule_one(app, int(row["user_id"]), int(row["id"]))
        dbmod.update_reminder_run_times(con, int(row["id"]), last_run_at=last, next_run_at=None)
        return

    # si es recurrente, actualizar next_run_at desde scheduler
    jid = job_id_for(int(row["user_id"]), int(row["id"]))
    job = app.job_queue.scheduler.get_job(jid)
    next_run_dt = getattr(job, "next_run_time", None) if job else None
    next_run = next_run_dt.isoformat() if next_run_dt else None
    dbmod.update_reminder_run_times(con, int(row["id"]), last_run_at=last, next_run_at=next_run)
