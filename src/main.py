from __future__ import annotations

import logging
import asyncio

from datetime import datetime, timedelta, date
from pathlib import Path

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    Defaults,
    filters,
)

from zoneinfo import ZoneInfo

import db as dbmod
from config import get_settings
from export_csv import export_user_data_to_csv
from parser import parse_hhmm
import reminders as remmod


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("cortana")


TZ = ZoneInfo("America/Bogota")


def is_owner(update: Update, owner_id: int) -> bool:
    u = update.effective_user
    return bool(u and u.id == owner_id)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Ocurrió un error:", exc_info=context.error)

async def post_init(app):
    # Guardamos el loop REAL donde corre el bot
    app.bot_data["loop"] = asyncio.get_running_loop()

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    con = context.application.bot_data["db"]

    if not is_owner(update, settings.owner_telegram_user_id):
        await update.message.reply_text("Este bot es privado.")
        return

    user = update.effective_user
    chat = update.effective_chat
    name = (user.full_name or "Juan David").strip()

    user_id = dbmod.upsert_user(con, user.id, chat.id, name)

    await update.message.reply_text(
        "✅ Lista, Juan David. Ya estoy conectada a este chat.\n\n"
        "Recordatorios (nuevo):\n"
        "• /rem_add WEEKDAY@23:00 Dormir | Hora de dormir 😴\n"
        "• /rem_add WEEKEND@10:00 Dormir | A dormir 😴\n"
        "• /rem_add DAYS@tue@20:00 Basura | Sacar la basura 🗑️\n"
        "• /rem_add ONCE@2026-01-20@09:00 Cita | Cita médica 🏥\n"
        "• /rem_list /rem_off <id> /rem_on <id> /rem_del <id>\n"
        "• /rem_test\n\n"
        "Tareas/Notas:\n"
        "• Pendiente: estudiar 1 hora\n"
        "• Mañana: pagar recibo\n"
        "• Hice: estudiar 1 hora\n"
        "• Nota: me sentí cansado\n"
        "• Resumen / Pendientes / Hechos hoy / Buscar: palabra\n"
        "Export:\n"
        "• /export"
    )


# ---------------- Reminders Commands ----------------

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
    if not left:
        await update.message.reply_text("Falta schedule y nombre.")
        return

    parts = left.split()
    if len(parts) < 2:
        await update.message.reply_text("Uso: /rem_add <SCHEDULE> <NOMBRE> | <MENSAJE>")
        return

    schedule = parts[0].strip()
    name = " ".join(parts[1:]).strip()
    if not name:
        await update.message.reply_text("Falta el nombre del recordatorio.")
        return

    try:
        remmod.parse_schedule(schedule)  # valida
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
    app = context.application

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
        schedule = r["schedule"]
        name = r["name"]

        jid = remmod.job_id_for(user_id, rid)
        job = app.job_queue.scheduler.get_job(jid)
        next_run = None
        if job and job.next_run_time:
            try:
                next_run = job.next_run_time.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
            except Exception:
                next_run = str(job.next_run_time)

        lines.append(f"- id={rid} [{active}] {name} | {schedule}" + (f" | next: {next_run}" if next_run else ""))

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

    now = datetime.now(TZ) + timedelta(minutes=2)
    schedule = f"ONCE@{now.strftime('%Y-%m-%d')}@{now.strftime('%H:%M')}"
    rid = dbmod.create_reminder(con, user_id, name="Test", message="✅ Recordatorio de prueba", schedule=schedule)
    row = dbmod.get_reminder(con, user_id, rid)
    remmod.schedule_one(app, con, row, chat.id)

    await update.message.reply_text(f"🧪 Test creado (id={rid}) para {now.strftime('%H:%M')}.")


# ---------------- Existing MVP: tasks/notes ----------------

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

    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    now_iso = datetime.now().isoformat(timespec="seconds")

    if low.startswith("pendiente:"):
        text = msg.split(":", 1)[1].strip()
        dbmod.add_task(con, user_id, today, text)
        await update.message.reply_text("✅ Listo, lo guardé como pendiente para hoy.")
        return

    if low.startswith("mañana:") or low.startswith("manana:"):
        text = msg.split(":", 1)[1].strip()
        dbmod.add_task(con, user_id, tomorrow, text)
        await update.message.reply_text("✅ Listo, lo guardé como pendiente para mañana.")
        return

    if low.startswith("hice:"):
        text = msg.split(":", 1)[1].strip()
        ok = dbmod.mark_task_done_by_text(con, user_id, text)
        await update.message.reply_text("✅ Marcado como hecho." if ok else "No encontré ese pendiente exacto. ¿Lo guardo como nota?")
        return

    if low.startswith("nota:"):
        text = msg.split(":", 1)[1].strip()
        dbmod.add_note(con, user_id, now_iso, text)
        await update.message.reply_text("📝 Nota guardada.")
        return

    if low == "pendientes":
        pend = dbmod.list_tasks(con, user_id, today, "pending")
        if not pend:
            await update.message.reply_text("No tienes pendientes para hoy.")
            return
        out = "\n".join([f"- {t['text']}" for t in pend])
        await update.message.reply_text("📌 Pendientes de hoy:\n" + out)
        return

    if low == "hechos hoy":
        done = dbmod.list_tasks(con, user_id, today, "done")
        if not done:
            await update.message.reply_text("Aún no hay hechos hoy.")
            return
        out = "\n".join([f"- {t['text']}" for t in done])
        await update.message.reply_text("✅ Hechos de hoy:\n" + out)
        return

    if low == "resumen":
        pend = dbmod.list_tasks(con, user_id, today, "pending")
        done = dbmod.list_tasks(con, user_id, today, "done")
        notes = dbmod.list_notes_by_date(con, user_id, today)
        text = "📊 Resumen de hoy\n\n"
        text += "✅ Hechos:\n" + ("\n".join([f"- {t['text']}" for t in done]) if done else "- (ninguno)") + "\n\n"
        text += "📌 Pendientes:\n" + ("\n".join([f"- {t['text']}" for t in pend]) if pend else "- (ninguno)") + "\n\n"
        text += "📝 Notas:\n" + ("\n".join([f"- {n['text']}" for n in notes]) if notes else "- (ninguna)")
        await update.message.reply_text(text)
        return

    if low.startswith("buscar:"):
        needle = msg.split(":", 1)[1].strip()
        res = dbmod.search_all(con, user_id, needle)
        lines = []
        if res["tasks"]:
            lines.append("📌 Tareas:")
            lines.extend([f"- {t['target_date']} [{t['status']}] {t['text']}" for t in res["tasks"]])
        if res["notes"]:
            lines.append("\n📝 Notas:")
            lines.extend([f"- {n['note_datetime']} {n['text']}" for n in res["notes"]])
        await update.message.reply_text("\n".join(lines) if lines else "No encontré coincidencias.")
        return

    # fallback
    await update.message.reply_text("No entendí. Usa: Pendiente:, Hice:, Nota:, Resumen, Pendientes, Hechos hoy, Buscar: palabra")


def main() -> None:
    settings = get_settings()
    con = dbmod.connect(settings.db_path)
    dbmod.init_db(con)

    defaults = Defaults(tzinfo=TZ)

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .defaults(defaults)
        .post_init(post_init)
        .build()
    )

    app.add_error_handler(on_error)

    # Guardar settings y db en app
    app.bot_data["settings"] = settings
    app.bot_data["db"] = con

    # handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("export", cmd_export))

    app.add_handler(CommandHandler("rem_add", cmd_rem_add))
    app.add_handler(CommandHandler("rem_list", cmd_rem_list))
    app.add_handler(CommandHandler("rem_on", cmd_rem_on))
    app.add_handler(CommandHandler("rem_off", cmd_rem_off))
    app.add_handler(CommandHandler("rem_del", cmd_rem_del))
    app.add_handler(CommandHandler("rem_test", cmd_rem_test))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Programar recordatorios existentes
    remmod.schedule_all_active(app, con)

    log.info("Bot iniciado. Polling local...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
