from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime
import pytz

from telegram.ext import Application, ContextTypes

from parser import parse_hhmm

TZ = pytz.timezone("America/Bogota")

DEFAULT_MORNING = "08:00"
DEFAULT_AFTERNOON = "14:00"
DEFAULT_NIGHT = "21:30"


@dataclass(frozen=True)
class ReminderTimes:
    morning: str
    afternoon: str
    night: str


def build_time(hhmm: str) -> dtime:
    hh_mm = parse_hhmm(hhmm)
    if not hh_mm:
        raise ValueError(f"Hora inválida: {hhmm}. Usa HH:MM (ej: 08:00)")
    h, m = hh_mm
    return dtime(hour=h, minute=m, tzinfo=TZ)


async def reminder_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    kind = data.get("kind")

    if not chat_id:
        return

    if kind == "morning":
        msg = "🌤️ Buenos días. ¿Qué 3 cosas harás hoy? (Responde con 'Pendiente: ...')"
    elif kind == "afternoon":
        msg = "⏳ ¿Qué ya hiciste? Respóndeme con 'Hice: ...' o cuéntame una 'Nota: ...'"
    else:
        msg = "🌙 Cierre del día: ¿qué faltó y qué aprendiste? (Puedes usar 'Nota: ...' y 'Pendiente: ...')"

    await context.bot.send_message(chat_id=chat_id, text=msg)


def reschedule_user_reminders(app: Application, *, user_id: int, chat_id: int, times: ReminderTimes) -> None:
    """
    Borra recordatorios existentes del usuario y crea 3 nuevos diarios.
    """
    jq = app.job_queue
    if jq is None:
        return

    # borrar existentes
    for kind in ("morning", "afternoon", "night"):
        name = f"reminder_{user_id}_{kind}"
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()

    # crear nuevos
    jq.run_daily(
        reminder_callback,
        time=build_time(times.morning),
        name=f"reminder_{user_id}_morning",
        data={"chat_id": chat_id, "kind": "morning"},
    )
    jq.run_daily(
        reminder_callback,
        time=build_time(times.afternoon),
        name=f"reminder_{user_id}_afternoon",
        data={"chat_id": chat_id, "kind": "afternoon"},
    )
    jq.run_daily(
        reminder_callback,
        time=build_time(times.night),
        name=f"reminder_{user_id}_night",
        data={"chat_id": chat_id, "kind": "night"},
    )
