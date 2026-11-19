"""Entry point and dispatcher factory (also used by tests and the web emulator)."""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .ai import Assistant, get_assistant
from .config import Settings
from .db import DB
from .handlers import admin, shop


def create_dispatcher(settings: Settings, db: DB, assistant: Assistant) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["settings"] = settings
    dp["db"] = db
    dp["assistant"] = assistant
    # admin router first so /admin and admin states win; it filters by ADMIN_IDS itself
    dp.include_router(admin.setup())
    dp.include_router(shop.build_router())
    return dp


def default_props() -> DefaultBotProperties:
    return DefaultBotProperties(parse_mode=ParseMode.HTML)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    if not settings.bot_token:
        sys.exit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    if settings.payment_mode == "provider" and not settings.provider_token:
        sys.exit("PAYMENT_MODE=provider needs PROVIDER_TOKEN (BotFather -> Payments).")
    assistant = get_assistant(settings.ai_model)
    logging.info("AI assistant: %s · payments: %s", assistant.name, settings.payment_mode)

    bot = Bot(settings.bot_token, default=default_props())
    dp = create_dispatcher(settings, DB(settings.db_path), assistant)
    await bot.set_my_commands([
        BotCommand(command="start", description="Open the shop"),
        BotCommand(command="menu", description="Main menu"),
    ])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
