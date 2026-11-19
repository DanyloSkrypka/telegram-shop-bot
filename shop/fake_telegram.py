"""Offline Telegram: a fake Bot API session + helpers to build incoming updates.

Every outgoing Bot API call is recorded instead of sent, and a plausible result is
returned, so the real aiogram handlers run unchanged. Used by pytest and the web emulator.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery, AnswerPreCheckoutQuery, EditMessageReplyMarkup, EditMessageText,
    SendChatAction, SendInvoice, SendMessage, SetMyCommands, TelegramMethod,
)
from aiogram.types import (
    CallbackQuery, Chat, InlineKeyboardMarkup, Message, PreCheckoutQuery,
    SuccessfulPayment, Update, User,
)

from .bot import default_props

BOT_USER = User(id=7000000001, is_bot=True, first_name="Nordic Roast", username="nordic_roast_bot")


@dataclass
class Sent:
    method: str
    chat_id: int | None
    payload: dict[str, Any]
    message_id: int | None = None


@dataclass
class ChatState:
    """What the bot has on screen per chat: message_id -> {text, markup}."""
    messages: dict[int, dict] = field(default_factory=dict)


def _markup(m) -> list | None:
    if not isinstance(m, InlineKeyboardMarkup):
        return None
    return [[{"text": b.text, "data": b.callback_data} for b in row] for row in m.inline_keyboard]


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.log: list[Sent] = []
        self.chats: dict[int, ChatState] = {}
        self._ids = itertools.count(1000)

    async def close(self) -> None:  # nothing to close
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover - not used
        raise NotImplementedError

    def _chat(self, chat_id: int) -> ChatState:
        return self.chats.setdefault(chat_id, ChatState())

    def _message(self, chat_id: int, mid: int, text: str | None = None, **extra) -> Message:
        return Message(message_id=mid, date=int(time.time()),
                       chat=Chat(id=chat_id, type="private"), from_user=BOT_USER,
                       text=text, **extra)

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):
        name = type(method).__name__
        data = method.model_dump(exclude_none=True)
        chat_id = data.get("chat_id")
        rec = Sent(name, chat_id, data)
        self.log.append(rec)

        if isinstance(method, SendMessage):
            mid = next(self._ids)
            rec.message_id = mid
            self._chat(chat_id).messages[mid] = {"text": method.text,
                                                 "markup": _markup(method.reply_markup)}
            return self._message(chat_id, mid, method.text, reply_markup=method.reply_markup)
        if isinstance(method, EditMessageText):
            rec.message_id = method.message_id
            self._chat(chat_id).messages[method.message_id] = {
                "text": method.text, "markup": _markup(method.reply_markup)}
            return self._message(chat_id, method.message_id, method.text,
                                 reply_markup=method.reply_markup)
        if isinstance(method, EditMessageReplyMarkup):
            rec.message_id = method.message_id
            msg = self._chat(chat_id).messages.setdefault(method.message_id, {"text": ""})
            msg["markup"] = _markup(method.reply_markup)
            return True
        if isinstance(method, SendInvoice):
            mid = next(self._ids)
            rec.message_id = mid
            return self._message(chat_id, mid)
        if isinstance(method, (AnswerCallbackQuery, AnswerPreCheckoutQuery, SendChatAction,
                               SetMyCommands)):
            return True
        return True


def make_bot() -> tuple[Bot, FakeSession]:
    session = FakeSession()
    return Bot("123456:TEST-TOKEN-NOT-REAL", session=session, default=default_props()), session


# --- incoming update builders ----------------------------------------------
_update_ids = itertools.count(1)
_msg_ids = itertools.count(1)
_cb_ids = itertools.count(1)


def user(uid: int, first_name: str = "Alex", username: str | None = "alex_buyer") -> User:
    return User(id=uid, is_bot=False, first_name=first_name, username=username, language_code="en")


def text_update(u: User, text: str) -> Update:
    return Update(update_id=next(_update_ids), message=Message(
        message_id=next(_msg_ids), date=int(time.time()),
        chat=Chat(id=u.id, type="private"), from_user=u, text=text,
        entities=[{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        if text.startswith("/") else None))


def callback_update(u: User, data: str, message_id: int, text: str = "") -> Update:
    msg = Message(message_id=message_id, date=int(time.time()),
                  chat=Chat(id=u.id, type="private"), from_user=BOT_USER, text=text or ".")
    return Update(update_id=next(_update_ids), callback_query=CallbackQuery(
        id=str(next(_cb_ids)), from_user=u, chat_instance="ci", message=msg, data=data))


def pre_checkout_update(u: User, payload: str, currency: str, total: int) -> Update:
    return Update(update_id=next(_update_ids), pre_checkout_query=PreCheckoutQuery(
        id=f"pcq{next(_cb_ids)}", from_user=u, currency=currency,
        total_amount=total, invoice_payload=payload))


def payment_update(u: User, payload: str, currency: str, total: int) -> Update:
    sp = SuccessfulPayment(currency=currency, total_amount=total, invoice_payload=payload,
                           telegram_payment_charge_id=f"stxTEST{int(time.time())}",
                           provider_payment_charge_id="")
    return Update(update_id=next(_update_ids), message=Message(
        message_id=next(_msg_ids), date=int(time.time()),
        chat=Chat(id=u.id, type="private"), from_user=u, successful_payment=sp))
