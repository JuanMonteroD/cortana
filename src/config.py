from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    token: str
    owner_user_id: int
    db_path: str


def get_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    owner = os.getenv("OWNER_TELEGRAM_USER_ID", "").strip()
    db_path = os.getenv("DB_PATH", "data/assistant.db").strip()

    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en tu .env")
    if not owner.isdigit():
        raise RuntimeError("OWNER_TELEGRAM_USER_ID debe ser un número (tu user_id de Telegram).")

    return Settings(token=token, owner_user_id=int(owner), db_path=db_path)
