from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, date, time, timezone

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    Defaults,
    filters,
)

# Tu reminders.py sigue igual
import reminders as remmod

import db as dbmod
from config import get_settings
from export_csv import export_user_data_to_csv

log = logging.getLogger("cortana")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

# Termux-friendly: Bogotá es UTC-5 fijo (sin DST)
BOGOTA_TZ = timezone(timedelta(hours=-5))


def is_owner(update: Update, owner_id: int) -> bool:
    u = update.effective_user
    return bool(u and u.id == owner_id)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Ocurrió un error:", exc_info=context.error)


async def post_init(app):
    # Guardamos el loop REAL donde corre el bot (para reminders + termux)
    app.bot_data["loop"] = asyncio.get_running_loop()


# ---------------- START ----------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    con = context.application.bot_data["db"]

    if not is_owner(update, settings.owner_telegram_user_id):
        await update.message.reply_text("Este bot es privado.")
        return

    user = update.effective_user
    chat = update.effective_chat
    name = (user.full_name or "Juan David").strip()

    dbmod.upsert_user(con, user.id, chat.id, name)

    await update.message.reply_text(
        "✅ Lista, Juan David. Ya estoy conectada a este chat.\n\n"
        "Tareas (nuevo):\n"
        "• Pendiente: estudiar 1 hora\n"
        "• Mañana: pagar recibo\n"
        "• Tarea: 2026-01-20 | sacar basura\n"
        "• Hice #12\n"
        "• Hice: estudiar 1 hora (fallback)\n"
        "• Tareas hoy / Tareas: 2026-01-20 / Semana\n"
        "• Pendientes / Pendientes semana / Pendientes todos\n"
        "• Hechos hoy / Hechos semana / Hechos todos\n"
        "• Incumplidas hoy / Incumplidas semana / Incumplidas todos\n"
        "• Editar #12 | nuevo texto\n"
        "• Editar #12 | 2026-01-20 | nuevo texto\n"
        "• Mover #12 | 2026-01-21\n"
        "• Borrar #12\n\n"
        "Recordatorios:\n"
        "• /rem_add ... /rem_list /rem_on /rem_off /rem_del /rem_test\n\n"
        "Export:\n"
        "• /export"
    )


# ---------------- EXPORT ----------------

async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    con = context.application.bot_data["db"]

    if not is_owner(update, settings.owner_telegram_user_id):
        return

    user = update.effective_user
    user_id = dbmod.get_user_id(con, user.id)
    if not user_id:
        await update.message.reply_text("Primero usa /start.")
        return

    filepath, filename = export_user_data_to_csv(con, user_id)
    await update.message.reply_document(document=open(filepath, "rb"), filename=filename)


# ---------------- Helpers (Tareas) ----------------

def _today_iso() -> str:
    # Bogotá por offset fijo
    return datetime.now(BOGOTA_TZ).date().isoformat()


def _tomorrow_iso() -> str:
    return (datetime.now(BOGOTA_TZ).date() + timedelta(days=1)).isoformat()


def _week_range(today: date) -> tuple[str, str]:
    # Lunes a domingo
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def _fmt_task_line(t: dict) -> str:
    return f"#{t['id']} - {t['text']}"


def _group_by_date(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["target_date"], []).append(r)
    return out


# ---------------- Job: Cierre del día (missed) ----------------

async def job_close_day(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    23:59 Bogotá:
    pending -> missed para el día actual.
    """
    app = context.application
    con = app.bot_data["db"]
    settings = app.bot_data["settings"]

    # Si no hay usuario creado aún, no hace nada
    owner_user_id = dbmod.get_user_id(con, settings.owner_telegram_user_id)
    if not owner_user_id:
        return

    today = datetime.now(BOGOTA_TZ).date().isoformat()
    changed = dbmod.mark_tasks_missed_for_date(con, owner_user_id, today)

    # Notificar (si tenemos chat_id)
    chat_id = dbmod.get_user_chat_id(con, owner_user_id)
    if chat_id and changed > 0:
        await app.bot.send_message(chat_id=chat_id, text=f"🌙 Cierre del día: {changed} tarea(s) quedaron incumplidas.")


def schedule_close_day(app) -> None:
    """
    Programar una vez el cierre del día a las 23:59 (UTC-5 fijo).
    Termux-friendly: no depende de tzdata.
    """
    t = time(hour=23, minute=59, tzinfo=BOGOTA_TZ)
    # name para evitar duplicados
    app.job_queue.run_daily(job_close_day, time=t, name="close_day_missed")


# ---------------- Reminders Commands (los de tu proyecto) ----------------
# (Si ya los tienes en tu main.py, puedes conservarlos igual)

async def cmd_rem_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    con = context.application.bot_data["db"]
    app = context.application

    if not is_owner(update, settings.owner_telegram_user_id):
        return

    user = update.effective_user
    chat = update.effective_chat
    user_id = dbmod.get_user_id(con, user.id)
    if not user_id:
        user_id = dbmod.upsert_user(con, user.id, chat.id, user.full_name or "Juan David")

    text = update.message.text or ""
    payload = text.replace("/rem_add", "", 1).strip()
    if not payload:
        await update.message.reply_text("Uso: /rem_add <SCHEDULE> <NOMBRE> | <MENSAJE>")
        return

    if "|" not in payload:
        await update.message.reply_text("Falta '|'. Ej: /rem_add WEEKDAY@23:00 Dormir | Hora de dormir 😴")
        return

    left, message = [p.strip() for p in payload.split("|", 1)]
    parts = left.split()
    if len(parts) < 2:
        await update.message.reply_text("Uso: /rem_add <SCHEDULE> <NOMBRE> | <MENSAJE>")
        return

    schedule = parts[0].strip()
    name = " ".join(parts[1:]).strip()

    try:
        remmod.parse_schedule(schedule)
    except Exception as e:
        await update.message.reply_text(f"Schedule inválido: {e}")
        return

    rid = dbmod.create_reminder(con, user_id, name=name, message=message, schedule=schedule, timezone="America/Bogota")
    row = dbmod.get_reminder(con, user_id, rid)
    remmod.schedule_one(app, con, row, chat.id)

    await update.message.reply_text(f"✅ Recordatorio creado (id={rid}).")


async def cmd_rem_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    con = context.application.bot_data["db"]

    if not is_owner(update, settings.owner_telegram_user_id):
        return

    user = update.effective_user
    user_id = dbmod.get_user_id(con, user.id)
    if not user_id:
        await update.message.reply_text("Primero usa /start.")
        return

    reminders = dbmod.list_reminders(con, user_id, only_active=False)
    if not reminders:
        await update.message.reply_text("No tienes recordatorios aún. Usa /rem_add.")
        return

    lines = []
    for r in reminders:
        rid = r["id"]
        active = "ON" if int(r["active"]) == 1 else "OFF"
        lines.append(f"- id={rid} [{active}] {r['name']} | {r['schedule']}")
    await update.message.reply_text("📌 Tus recordatorios:\n" + "\n".join(lines))


async def cmd_rem_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _rem_toggle(update, context, active=0)


async def cmd_rem_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _rem_toggle(update, context, active=1)


async def _rem_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, active: int) -> None:
    settings = context.application.bot_data["settings"]
    con = context.application.bot_data["db"]
    app = context.application

    if not is_owner(update, settings.owner_telegram_user_id):
        return

    user = update.effective_user
    chat = update.effective_chat
    user_id = dbmod.get_user_id(con, user.id)
    if not user_id:
        await update.message.reply_text("Primero usa /start.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Uso: /rem_on <id>  ó  /rem_off <id>")
        return

    rid = int(context.args[0])

    ok = dbmod.update_reminder_active(con, user_id, rid, active)
    if not ok:
        await update.message.reply_text("No encontré ese recordatorio.")
        return

    if active == 1:
        row = dbmod.get_reminder(con, user_id, rid)
        remmod.schedule_one(app, con, row, chat.id)
        await update.message.reply_text("✅ Activado.")
    else:
        remmod.unschedule_one(app, user_id, rid)
        await update.message.reply_text("🛑 Desactivado.")


async def cmd_rem_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    con = context.application.bot_data["db"]
    app = context.application

    if not is_owner(update, settings.owner_telegram_user_id):
        return

    user = update.effective_user
    user_id = dbmod.get_user_id(con, user.id)
    if not user_id:
        await update.message.reply_text("Primero usa /start.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Uso: /rem_del <id>")
        return

    rid = int(context.args[0])

    remmod.unschedule_one(app, user_id, rid)
    ok = dbmod.delete_reminder(con, user_id, rid)
    await update.message.reply_text("🗑️ Eliminado." if ok else "No encontré ese recordatorio.")


async def cmd_rem_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    con = context.application.bot_data["db"]
    app = context.application

    if not is_owner(update, settings.owner_telegram_user_id):
        return

    user = update.effective_user
    chat = update.effective_chat
    user_id = dbmod.get_user_id(con, user.id)
    if not user_id:
        await update.message.reply_text("Primero usa /start.")
        return

    now = datetime.now(BOGOTA_TZ) + timedelta(minutes=2)
    schedule = f"ONCE@{now.strftime('%Y-%m-%d')}@{now.strftime('%H:%M')}"
    rid = dbmod.create_reminder(con, user_id, name="Test", message="✅ Recordatorio de prueba", schedule=schedule)
    row = dbmod.get_reminder(con, user_id, rid)
    remmod.schedule_one(app, con, row, chat.id)
    await update.message.reply_text(f"🧪 Test creado (id={rid}) para {now.strftime('%H:%M')}.")


# ---------------- Handle Text (TAREAS + NOTAS + BUSCAR) ----------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    con = context.application.bot_data["db"]

    if not is_owner(update, settings.owner_telegram_user_id):
        return

    user = update.effective_user
    chat = update.effective_chat

    user_id = dbmod.get_user_id(con, user.id)
    if not user_id:
        user_id = dbmod.upsert_user(con, user.id, chat.id, user.full_name or "Juan David")

    msg = (update.message.text or "").strip()
    low = msg.lower()

    today = _today_iso()
    tomorrow = _tomorrow_iso()
    now_iso = datetime.now(BOGOTA_TZ).isoformat(timespec="seconds")

    # -------- Crear tareas --------
    if low.startswith("pendiente:"):
        text = msg.split(":", 1)[1].strip()
        tid = dbmod.add_task(con, user_id, today, text)
        await update.message.reply_text(f"✅ Tarea creada (# {tid}) para hoy.")
        return

    if low.startswith("mañana:") or low.startswith("manana:"):
        text = msg.split(":", 1)[1].strip()
        tid = dbmod.add_task(con, user_id, tomorrow, text)
        await update.message.reply_text(f"✅ Tarea creada (# {tid}) para mañana.")
        return

    if low.startswith("tarea:"):
        payload = msg.split(":", 1)[1].strip()
        if "|" not in payload:
            await update.message.reply_text("Uso: Tarea: YYYY-MM-DD | <texto>")
            return
        left, text = [p.strip() for p in payload.split("|", 1)]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", left):
            await update.message.reply_text("Fecha inválida. Usa YYYY-MM-DD (ej: 2026-01-20).")
            return
        tid = dbmod.add_task(con, user_id, left, text)
        await update.message.reply_text(f"✅ Tarea creada (# {tid}) para {left}.")
        return

    # -------- Completar --------
    m = re.match(r"^hice\s+#(\d+)\s*$", low)
    if m:
        tid = int(m.group(1))
        ok = dbmod.mark_task_done_by_id(con, user_id, tid)
        await update.message.reply_text(f"✅ Marcada como hecha (# {tid})." if ok else f"No encontré esa tarea # {tid}.")
        return

    if low.startswith("hice:"):
        text = msg.split(":", 1)[1].strip()
        status, ids = dbmod.mark_task_done_by_text(con, user_id, text, target_date=today)
        if status == "one":
            await update.message.reply_text(f"✅ Marcada como hecha (# {ids[0]}).")
        elif status == "many":
            ids_str = ", ".join([f"#{i}" for i in ids])
            await update.message.reply_text(f"Encontré varias tareas con ese texto hoy: {ids_str}\nEscríbeme: Hice #<id>")
        else:
            await update.message.reply_text("No encontré ese pendiente exacto hoy. ¿Quieres que lo guarde como tarea nueva?")
        return

    # -------- Eliminar --------
    m = re.match(r"^borrar\s+#(\d+)\s*$", low)
    if m:
        tid = int(m.group(1))
        ok = dbmod.delete_task_by_id(con, user_id, tid)
        await update.message.reply_text(f"🗑️ Eliminada (# {tid})." if ok else f"No encontré esa tarea # {tid}.")
        return

    # -------- Modificar --------
    if low.startswith("mover #"):
        # Mover #12 | 2026-01-20
        if "|" not in msg:
            await update.message.reply_text("Uso: Mover #<id> | YYYY-MM-DD")
            return
        left, new_date = [p.strip() for p in msg.split("|", 1)]
        m2 = re.match(r"^mover\s+#(\d+)\s*$", left.lower())
        if not m2 or not re.match(r"^\d{4}-\d{2}-\d{2}$", new_date):
            await update.message.reply_text("Uso: Mover #<id> | YYYY-MM-DD")
            return
        tid = int(m2.group(1))
        ok = dbmod.update_task_date(con, user_id, tid, new_date)
        await update.message.reply_text(f"✏️ Actualizada (# {tid}) → {new_date}." if ok else f"No encontré esa tarea # {tid}.")
        return

    if low.startswith("editar #"):
        # Editar #12 | texto
        # Editar #12 | 2026-01-20 | texto
        parts = [p.strip() for p in msg.split("|")]
        left = parts[0].strip()
        m2 = re.match(r"^editar\s+#(\d+)\s*$", left.lower())
        if not m2:
            await update.message.reply_text("Uso: Editar #<id> | <nuevo texto>  (opcional fecha)")
            return
        tid = int(m2.group(1))

        if len(parts) == 2:
            new_text = parts[1]
            ok = dbmod.update_task_text(con, user_id, tid, new_text)
            await update.message.reply_text(f"✏️ Actualizada (# {tid})." if ok else f"No encontré esa tarea # {tid}.")
            return

        if len(parts) >= 3:
            maybe_date = parts[1]
            new_text = "|".join(parts[2:]).strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", maybe_date):
                await update.message.reply_text("Fecha inválida. Usa YYYY-MM-DD.")
                return
            ok = dbmod.update_task_date_text(con, user_id, tid, maybe_date, new_text)
            await update.message.reply_text(f"✏️ Actualizada (# {tid})." if ok else f"No encontré esa tarea # {tid}.")
            return

    # -------- Consultas --------
    if low == "tareas hoy":
        rows = dbmod.list_tasks_by_date(con, user_id, today, status=None)
        if not rows:
            await update.message.reply_text("No tienes tareas para hoy.")
            return
        out = "\n".join([f"{_fmt_task_line(t)}  [{t['status']}]" for t in rows])
        await update.message.reply_text("📋 Tareas de hoy:\n" + out)
        return

    m = re.match(r"^tareas:\s*(\d{4}-\d{2}-\d{2})$", low)
    if m:
        d = m.group(1)
        rows = dbmod.list_tasks_by_date(con, user_id, d, status=None)
        if not rows:
            await update.message.reply_text(f"No tienes tareas para {d}.")
            return
        out = "\n".join([f"{_fmt_task_line(t)}  [{t['status']}]" for t in rows])
        await update.message.reply_text(f"📋 Tareas de {d}:\n" + out)
        return

    if low == "pendientes":
        rows = dbmod.list_tasks_by_date(con, user_id, today, status="pending")
        if not rows:
            await update.message.reply_text("No hay pendientes.")
            return
        out = "\n".join([_fmt_task_line(t) for t in rows])
        await update.message.reply_text("📌 Pendientes de hoy:\n" + out)
        return

    if low == "hechos hoy":
        rows = dbmod.list_tasks_by_date(con, user_id, today, status="done")
        if not rows:
            await update.message.reply_text("Aún no hay hechos hoy.")
            return
        out = "\n".join([_fmt_task_line(t) for t in rows])
        await update.message.reply_text("✅ Hechos de hoy:\n" + out)
        return

    if low == "incumplidas hoy":
        rows = dbmod.list_tasks_by_date(con, user_id, today, status="missed")
        if not rows:
            await update.message.reply_text("No hay incumplidas hoy.")
            return
        out = "\n".join([_fmt_task_line(t) for t in rows])
        await update.message.reply_text("⚠️ Incumplidas de hoy:\n" + out)
        return

    if low == "pendientes semana" or low == "hechos semana" or low == "incumplidas semana" or low == "semana":
        td = datetime.now(BOGOTA_TZ).date()
        start, end = _week_range(td)

        if low == "pendientes semana":
            rows = dbmod.list_tasks_between(con, user_id, start, end, status="pending")
            title = f"📌 Pendientes semana ({start} a {end})"
        elif low == "hechos semana":
            rows = dbmod.list_tasks_between(con, user_id, start, end, status="done")
            title = f"✅ Hechos semana ({start} a {end})"
        elif low == "incumplidas semana":
            rows = dbmod.list_tasks_between(con, user_id, start, end, status="missed")
            title = f"⚠️ Incumplidas semana ({start} a {end})"
        else:
            rows = dbmod.list_tasks_between(con, user_id, start, end, status=None)
            title = f"📊 Semana ({start} a {end})"

        if not rows:
            await update.message.reply_text("No hay tareas en esa semana.")
            return

        if low == "semana":
            # Resumen + agrupado por día
            pending = [r for r in rows if r["status"] == "pending"]
            done = [r for r in rows if r["status"] == "done"]
            missed = [r for r in rows if r["status"] == "missed"]

            text = f"{title}\n\n"
            text += f"Totales: ✅ {len(done)} | 📌 {len(pending)} | ⚠️ {len(missed)}\n\n"

            grouped = _group_by_date(rows)
            for d in sorted(grouped.keys()):
                day_rows = grouped[d]
                text += f"{d}:\n"
                for t in day_rows:
                    text += f"  - {_fmt_task_line(t)} [{t['status']}]\n"
                text += "\n"

            await update.message.reply_text(text.strip())
        else:
            grouped = _group_by_date(rows)
            out_lines = []
            for d in sorted(grouped.keys()):
                out_lines.append(d + ":")
                out_lines.extend([f"  - {_fmt_task_line(t)}" for t in grouped[d]])
            await update.message.reply_text(title + "\n" + "\n".join(out_lines))
        return

    if low == "pendientes todos":
        rows = dbmod.list_tasks_global(con, user_id, status="pending")
        if not rows:
            await update.message.reply_text("No hay pendientes.")
            return
        out = "\n".join([f"{t['target_date']} - {_fmt_task_line(t)}" for t in rows])
        await update.message.reply_text("📌 Pendientes (global):\n" + out)
        return

    if low == "hechos todos":
        rows = dbmod.list_tasks_global(con, user_id, status="done")
        if not rows:
            await update.message.reply_text("No hay hechos.")
            return
        out = "\n".join([f"{t['target_date']} - {_fmt_task_line(t)}" for t in rows])
        await update.message.reply_text("✅ Hechos (global):\n" + out)
        return

    if low == "incumplidas todos":
        rows = dbmod.list_tasks_global(con, user_id, status="missed")
        if not rows:
            await update.message.reply_text("No hay incumplidas.")
            return
        out = "\n".join([f"{t['target_date']} - {_fmt_task_line(t)}" for t in rows])
        await update.message.reply_text("⚠️ Incumplidas (global):\n" + out)
        return

    # -------- Resumen (compatibilidad) --------
    if low == "resumen":
        pend = dbmod.list_tasks_by_date(con, user_id, today, "pending")
        done = dbmod.list_tasks_by_date(con, user_id, today, "done")
        missed = dbmod.list_tasks_by_date(con, user_id, today, "missed")
        notes = dbmod.list_notes_by_date(con, user_id, today)

        text = "📊 Resumen de hoy\n\n"
        text += "✅ Hechos:\n" + ("\n".join([_fmt_task_line(t) for t in done]) if done else "- (ninguno)") + "\n\n"
        text += "📌 Pendientes:\n" + ("\n".join([_fmt_task_line(t) for t in pend]) if pend else "- (ninguno)") + "\n\n"
        text += "⚠️ Incumplidas:\n" + ("\n".join([_fmt_task_line(t) for t in missed]) if missed else "- (ninguna)") + "\n\n"
        text += "📝 Notas:\n" + ("\n".join([f"- {n['text']}" for n in notes]) if notes else "- (ninguna)")

        await update.message.reply_text(text)
        return

    # -------- Notas --------
    if low.startswith("nota:"):
        text = msg.split(":", 1)[1].strip()
        dbmod.add_note(con, user_id, now_iso, text)
        await update.message.reply_text("📝 Nota guardada.")
        return

    # -------- Buscar --------
    if low.startswith("buscar:"):
        needle = msg.split(":", 1)[1].strip()
        res = dbmod.search_all(con, user_id, needle)
        lines = []
        if res["tasks"]:
            lines.append("📌 Tareas:")
            lines.extend([f"- {t['target_date']} [{t['status']}] #{t['id']} - {t['text']}" for t in res["tasks"]])
        if res["notes"]:
            lines.append("\n📝 Notas:")
            lines.extend([f"- {n['note_datetime']} {n['text']}" for n in res["notes"]])
        await update.message.reply_text("\n".join(lines) if lines else "No encontré coincidencias.")
        return

    # fallback
    await update.message.reply_text(
        "No entendí.\n"
        "Ejemplos:\n"
        "• Pendiente: estudiar 1 hora\n"
        "• Tarea: 2026-01-20 | sacar basura\n"
        "• Hice #12\n"
        "• Pendientes / Hechos hoy / Incumplidas hoy\n"
        "• Semana\n"
        "• Editar #12 | nuevo texto\n"
        "• Borrar #12"
    )


def main() -> None:
    settings = get_settings()
    con = dbmod.connect(settings.db_path)
    dbmod.init_db(con)

    defaults = Defaults(tzinfo=BOGOTA_TZ)

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .defaults(defaults)
        .post_init(post_init)
        .build()
    )

    app.add_error_handler(on_error)

    app.bot_data["settings"] = settings
    app.bot_data["db"] = con

    # comandos base
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("export", cmd_export))

    # recordatorios (si los usas)
    app.add_handler(CommandHandler("rem_add", cmd_rem_add))
    app.add_handler(CommandHandler("rem_list", cmd_rem_list))
    app.add_handler(CommandHandler("rem_on", cmd_rem_on))
    app.add_handler(CommandHandler("rem_off", cmd_rem_off))
    app.add_handler(CommandHandler("rem_del", cmd_rem_del))
    app.add_handler(CommandHandler("rem_test", cmd_rem_test))

    # texto (tareas/notas/buscar)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Programar recordatorios existentes (tu módulo)
    try:
        remmod.schedule_all_active(app, con)
    except Exception as e:
        log.warning("No pude programar recordatorios al inicio: %s", e)

    # Programar cierre del día (tareas pending -> missed)
    schedule_close_day(app)

    log.info("Bot iniciado. Polling local...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
