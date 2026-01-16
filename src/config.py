from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    owner_telegram_user_id: int
    db_path: Path


def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en tu .env")

    owner_raw = os.getenv("OWNER_TELEGRAM_USER_ID", "").strip()
    if not owner_raw.isdigit():
        raise RuntimeError("Falta OWNER_TELEGRAM_USER_ID (debe ser numerico) en tu .env")
    owner_id = int(owner_raw)

    db_path_raw = os.getenv("DB_PATH", "data/assistant.db").strip()
    db_path = Path(db_path_raw)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    return Settings(
        telegram_bot_token=token,
        owner_telegram_user_id=owner_id,
        db_path=db_path,
    )
