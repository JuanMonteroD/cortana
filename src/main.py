from __future__ import annotations

import logging
from datetime import datetime, timedelta
import pytz

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    Defaults,
    JobQueue,
)

from config import get_settings
import db as dbmod
from parser import parse_message, parse_hhmm
from reminders import (
    TZ,
    ReminderTimes,
    DEFAULT_MORNING,
    DEFAULT_AFTERNOON,
    DEFAULT_NIGHT,
    reschedule_user_reminders,
)
from export_csv import build_export_csv

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("asistente")


def is_owner(settings, update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id == settings.owner_user_id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    if not is_owner(settings, update):
        await update.message.reply_text("⛔ Este bot es privado.")
        return

    con = context.application.bot_data["db"]
    user = dbmod.get_or_create_user(con, update.effective_user.id, update.effective_user.full_name)
    dbmod.set_chat_id(con, update.effective_user.id, update.effective_chat.id)

    # Config por defecto si no existe
    cfg = dbmod.get_config(con, user.id)
    if not cfg:
        dbmod.upsert_config(con, user.id, DEFAULT_MORNING, DEFAULT_AFTERNOON, DEFAULT_NIGHT)
        cfg = dbmod.get_config(con, user.id)

    # Re-programar recordatorios
    reschedule_user_reminders(
        context.application,
        user_id=user.id,
        chat_id=update.effective_chat.id,
        times=ReminderTimes(cfg.hora_manana, cfg.hora_tarde, cfg.hora_noche),
    )

    await update.message.reply_text(
        "✅ Lista, Juan David. Ya estoy conectada a este chat.\n\n"
        "Ejemplos rápidos:\n"
        "• Pendiente: estudiar 1 hora\n"
        "• Mañana: pagar recibo\n"
        "• Hice: estudiar 1 hora\n"
        "• Nota: me sentí cansado\n"
        "• Resumen / Pendientes / Hechos hoy\n"
        "• Buscar: palabra\n\n"
        "Config horarios:\n"
        "• /config 08:00 14:00 21:30\n"
        "Export:\n"
        "• /export"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    if not is_owner(settings, update):
        await update.message.reply_text("⛔ Este bot es privado.")
        return

    await update.message.reply_text(
        "Comandos:\n"
        "/start\n"
        "/config HH:MM HH:MM HH:MM  (mañana tarde noche)\n"
        "/export\n\n"
        "Mensajes:\n"
        "Pendiente: ...\n"
        "Mañana: ...\n"
        "Hice: ...\n"
        "Nota: ...\n"
        "Resumen | Pendientes | Hechos hoy\n"
        "Buscar: palabra"
    )


async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    if not is_owner(settings, update):
        await update.message.reply_text("⛔ Este bot es privado.")
        return

    con = context.application.bot_data["db"]
    user = dbmod.get_or_create_user(con, update.effective_user.id, update.effective_user.full_name)

    args = context.args or []
    cfg = dbmod.get_config(con, user.id)

    if len(args) == 0:
        if not cfg:
            await update.message.reply_text(
                "No tienes configuración aún. Usa:\n/config 08:00 14:00 21:30"
            )
            return
        await update.message.reply_text(
            "🕒 Horarios actuales:\n"
            f"• Mañana: {cfg.hora_manana}\n"
            f"• Tarde: {cfg.hora_tarde}\n"
            f"• Noche: {cfg.hora_noche}\n\n"
            "Para cambiar:\n/config 08:00 14:00 21:30"
        )
        return

    if len(args) != 3:
        await update.message.reply_text("Formato: /config 08:00 14:00 21:30")
        return

    for a in args:
        if not parse_hhmm(a):
            await update.message.reply_text(f"Hora inválida: {a}. Usa HH:MM (ej: 08:00)")
            return

    dbmod.upsert_config(con, user.id, args[0], args[1], args[2])
    cfg = dbmod.get_config(con, user.id)

    # Reprogramar si ya tenemos chat_id
    dbmod.set_chat_id(con, update.effective_user.id, update.effective_chat.id)
    reschedule_user_reminders(
        context.application,
        user_id=user.id,
        chat_id=update.effective_chat.id,
        times=ReminderTimes(cfg.hora_manana, cfg.hora_tarde, cfg.hora_noche),
    )

    await update.message.reply_text(
        "✅ Listo. Actualicé tus recordatorios:\n"
        f"• Mañana: {cfg.hora_manana}\n"
        f"• Tarde: {cfg.hora_tarde}\n"
        f"• Noche: {cfg.hora_noche}"
    )


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    if not is_owner(settings, update):
        await update.message.reply_text("⛔ Este bot es privado.")
        return

    con = context.application.bot_data["db"]
    user = dbmod.get_or_create_user(con, update.effective_user.id, update.effective_user.full_name)

    tasks, notes = dbmod.fetch_all_for_export(con, user.id)
    f = build_export_csv(tasks, notes)
    await update.message.reply_document(document=f, filename=f.name, caption="📦 Export CSV (tareas + notas)")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    if not is_owner(settings, update):
        return

    text = (update.message.text or "").strip()
    parsed = parse_message(text)

    con = context.application.bot_data["db"]
    user = dbmod.get_or_create_user(con, update.effective_user.id, update.effective_user.full_name)

    now_local = datetime.now(TZ)
    today = now_local.date()
    tomorrow = (now_local + timedelta(days=1)).date()

    if not parsed:
        await update.message.reply_text(
            "No te entendí 🙈\n\n"
            "Prueba así:\n"
            "• Pendiente: ...\n"
            "• Hice: ...\n"
            "• Nota: ...\n"
            "• Mañana: ...\n"
            "• Resumen | Pendientes | Hechos hoy\n"
            "• Buscar: palabra"
        )
        return

    if parsed.kind == "pendiente":
        dbmod.add_task(con, user.id, today, parsed.payload)
        await update.message.reply_text("✅ Listo, lo guardé como pendiente para hoy.")
        return

    if parsed.kind == "manana":
        dbmod.add_task(con, user.id, tomorrow, parsed.payload)
        await update.message.reply_text("✅ Listo, lo guardé como pendiente para mañana.")
        return

    if parsed.kind == "hice":
        dbmod.mark_done_or_create(con, user.id, today, parsed.payload)
        await update.message.reply_text("✅ Perfecto, marcado como hecho hoy.")
        return

    if parsed.kind == "nota":
        dbmod.add_note(con, user.id, now_local.isoformat(timespec="seconds"), parsed.payload)
        await update.message.reply_text("📝 Listo, guardé la nota.")
        return

    if parsed.kind == "pendientes":
        pendientes = dbmod.list_tasks(con, user.id, today, "pendiente", limit=20)
        if not pendientes:
            await update.message.reply_text("🎉 No tienes pendientes para hoy.")
            return
        msg = "📌 Pendientes de hoy:\n" + "\n".join([f"• {t}" for t in pendientes])
        await update.message.reply_text(msg)
        return

    if parsed.kind == "hechos_hoy":
        hechos = dbmod.list_tasks(con, user.id, today, "hecho", limit=20)
        if not hechos:
            await update.message.reply_text("Aún no hay hechos hoy. Puedes registrar con: Hice: ...")
            return
        msg = "✅ Hechos de hoy:\n" + "\n".join([f"• {t}" for t in hechos])
        await update.message.reply_text(msg)
        return

    if parsed.kind == "buscar":
        res = dbmod.search_everything(con, user.id, parsed.payload, limit=20)
        if not res["tareas"] and not res["notas"]:
            await update.message.reply_text("No encontré nada con esa palabra.")
            return
        parts = []
        if res["tareas"]:
            parts.append("🔎 Tareas:\n" + "\n".join([f"• {x}" for x in res["tareas"]]))
        if res["notas"]:
            parts.append("🗒️ Notas:\n" + "\n".join([f"• {x}" for x in res["notas"]]))
        await update.message.reply_text("\n\n".join(parts))
        return

    if parsed.kind == "resumen":
        pendientes = dbmod.list_tasks(con, user.id, today, "pendiente", limit=10)
        hechos = dbmod.list_tasks(con, user.id, today, "hecho", limit=10)
        notas = dbmod.list_notes_today(con, user.id, today, limit=10)

        msg = [f"📅 Resumen de hoy ({today.isoformat()})"]
        msg.append("")
        msg.append("✅ Hechos:" if hechos else "✅ Hechos: (ninguno)")
        if hechos:
            msg.extend([f"• {t}" for t in hechos])

        msg.append("")
        msg.append("📌 Pendientes:" if pendientes else "📌 Pendientes: (ninguno)")
        if pendientes:
            msg.extend([f"• {t}" for t in pendientes])

        msg.append("")
        msg.append("📝 Notas:" if notas else "📝 Notas: (ninguna)")
        if notas:
            msg.extend([f"• {t}" for t in notas])

        await update.message.reply_text("\n".join(msg))
        return


def main() -> None:
    settings = get_settings()

    # DB
    con = dbmod.connect(settings.db_path)
    dbmod.init_db(con)

    defaults = Defaults(tzinfo=TZ)  # para que el bot “piense” en Bogota

    app = (
        ApplicationBuilder()
        .token(settings.token)
        .defaults(defaults)
        .job_queue(JobQueue())  # requiere instalar python-telegram-bot[job-queue] :contentReference[oaicite:2]{index=2}
        .build()
    )

    app.bot_data["settings"] = settings
    app.bot_data["db"] = con

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot iniciado. Polling local...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
