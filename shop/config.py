"""Settings loaded from environment variables (.env supported). No secrets in code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent


def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


@dataclass
class Settings:
    bot_token: str = ""
    admin_ids: set[int] = field(default_factory=set)
    # "stars" -> Telegram Stars (XTR, no provider needed)
    # "provider" -> classic payment provider (e.g. Stripe TEST token from BotFather)
    payment_mode: str = "stars"
    provider_token: str = ""
    currency: str = "EUR"  # used only in provider mode
    db_path: str = str(ROOT / "data" / "shop.db")
    shop_name: str = "Nordic Roast"
    ai_model: str = "claude-opus-5-5"

    @property
    def ai_enabled(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY"))

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            bot_token=os.getenv("BOT_TOKEN", ""),
            admin_ids=_ids(os.getenv("ADMIN_IDS", "")),
            payment_mode=os.getenv("PAYMENT_MODE", "stars").lower(),
            provider_token=os.getenv("PROVIDER_TOKEN", ""),
            currency=os.getenv("CURRENCY", "EUR").upper(),
            db_path=os.getenv("DB_PATH", str(ROOT / "data" / "shop.db")),
            shop_name=os.getenv("SHOP_NAME", "Nordic Roast"),
            ai_model=os.getenv("AI_MODEL", "claude-opus-5-5"),
        )
