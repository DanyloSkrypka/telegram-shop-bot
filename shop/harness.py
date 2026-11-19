"""A tiny driver around the real dispatcher: send text, press buttons, pay.

Shared by pytest and the web emulator so both exercise exactly the same handlers.
"""
from __future__ import annotations

from aiogram.types import User

from . import fake_telegram as ft
from .ai import Assistant, TemplateAssistant, get_assistant
from .bot import create_dispatcher
from .config import Settings
from .db import DB


class Harness:
    def __init__(self, db_path: str = ":memory:", admin_id: int = 999,
                 assistant: Assistant | None = None, payment_mode: str = "stars"):
        self.settings = Settings(admin_ids={admin_id}, db_path=db_path, payment_mode=payment_mode,
                                 provider_token="TEST:provider" if payment_mode != "stars" else "")
        self.db = DB(db_path)
        self.assistant = assistant or (get_assistant() if db_path != ":memory:" else TemplateAssistant())
        self.dp = create_dispatcher(self.settings, self.db, self.assistant)
        self.bot, self.session = ft.make_bot()
        self.admin_id = admin_id

    # --- what the bot sent -------------------------------------------------
    @property
    def log(self) -> list[ft.Sent]:
        return self.session.log

    def since(self, mark: int) -> list[ft.Sent]:
        return self.session.log[mark:]

    def screen(self, chat_id: int) -> dict[int, dict]:
        return self.session._chat(chat_id).messages

    def last_message(self, chat_id: int) -> tuple[int, dict]:
        msgs = self.screen(chat_id)
        mid = max(msgs)
        return mid, msgs[mid]

    def find_button(self, chat_id: int, label: str) -> tuple[int, str]:
        """Newest bot message in the chat that has a button containing `label`."""
        for mid in sorted(self.screen(chat_id), reverse=True):
            for row in self.screen(chat_id)[mid].get("markup") or []:
                for b in row:
                    if label in b["text"]:
                        return mid, b["data"]
        raise LookupError(f"No button {label!r} in chat {chat_id}")

    def last_invoice(self, chat_id: int) -> ft.Sent:
        return next(s for s in reversed(self.log)
                    if s.method == "SendInvoice" and s.chat_id == chat_id)

    # --- user actions --------------------------------------------------------
    async def send(self, u: User, text: str) -> list[ft.Sent]:
        mark = len(self.log)
        await self.dp.feed_update(self.bot, ft.text_update(u, text))
        return self.since(mark)

    async def click(self, u: User, label: str) -> list[ft.Sent]:
        mid, data = self.find_button(u.id, label)
        return await self.press(u, data, mid)

    async def press(self, u: User, data: str, message_id: int) -> list[ft.Sent]:
        mark = len(self.log)
        text = self.screen(u.id).get(message_id, {}).get("text") or "."
        await self.dp.feed_update(self.bot, ft.callback_update(u, data, message_id, text))
        return self.since(mark)

    async def pay(self, u: User) -> tuple[bool, list[ft.Sent]]:
        """Simulate Telegram's payment sheet: pre-checkout, then successful_payment."""
        inv = self.last_invoice(u.id).payload
        total = sum(p["amount"] for p in inv["prices"])
        mark = len(self.log)
        await self.dp.feed_update(
            self.bot, ft.pre_checkout_update(u, inv["payload"], inv["currency"], total))
        answer = next(s for s in self.since(mark) if s.method == "AnswerPreCheckoutQuery")
        if not answer.payload["ok"]:
            return False, self.since(mark)
        await self.dp.feed_update(
            self.bot, ft.payment_update(u, inv["payload"], inv["currency"], total))
        return True, self.since(mark)
