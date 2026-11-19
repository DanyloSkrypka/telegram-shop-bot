"""Admin panel inside the bot: stats, recent orders, mark as shipped, broadcast."""
from __future__ import annotations

import asyncio
import json
from html import escape

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..config import Settings
from ..db import DB

_routes: list = []


def on(kind: str, *filters):
    """Collect handlers so a fresh Router can be built per dispatcher (tests, emulator)."""
    def deco(fn):
        _routes.append((kind, filters, fn))
        return fn
    return deco


def build_router() -> Router:
    r = Router(name="admin")
    for kind, filters, fn in _routes:
        getattr(r, kind).register(fn, *filters)
    return r


class AdminOnly(BaseMiddleware):
    """Drops every admin update that does not come from ADMIN_IDS."""

    async def __call__(self, handler, event, data):
        settings: Settings = data["settings"]
        user = data.get("event_from_user")
        if not user or user.id not in settings.admin_ids:
            return None
        return await handler(event, data)


class Broadcast(StatesGroup):
    text = State()


def _stats_text(db: DB, settings: Settings) -> str:
    s = db.stats()
    rev = ui.money(s["revenue"], ui.currency(settings))
    return ("🛠 <b>Admin panel</b>\n\n"
            f"👥 Customers: <b>{s['users']}</b>\n"
            f"🧾 Paid orders: <b>{s['orders']}</b>\n"
            f"💰 Revenue: <b>{rev}</b>\n"
            f"📦 Waiting to ship: <b>{s['to_ship']}</b>")


@on('message', Command("admin"))
async def admin(message: Message, state: FSMContext, db: DB, settings: Settings) -> None:
    await state.clear()
    await message.answer(_stats_text(db, settings), reply_markup=ui.admin_menu())


@on('callback_query', ui.AdminNav.filter(F.to == "stats"))
async def stats(cb: CallbackQuery, db: DB, settings: Settings) -> None:
    try:
        await cb.message.edit_text(_stats_text(db, settings), reply_markup=ui.admin_menu())
    except Exception:
        pass  # unchanged
    await cb.answer("Updated")


@on('callback_query', ui.AdminNav.filter(F.to == "orders"))
async def orders(cb: CallbackQuery, db: DB) -> None:
    rows = db.recent_orders()
    if not rows:
        await cb.message.answer("No paid orders yet.")
    for o in rows[:5]:
        items = ", ".join(f"{i['title']} ×{i['qty']}" for i in json.loads(o["items"]))
        status = "🚚 shipped" if o["status"] == "shipped" else "🟡 paid"
        await cb.message.answer(
            f"<b>#{o['id']}</b> · {ui.money(o['total'], o['currency'])} · {status}\n"
            f"{escape(items)}\n👤 {escape(o['customer'])}",
            reply_markup=ui.admin_order_kb(o["id"]) if o["status"] == "paid" else None)
    await cb.answer()


@on('callback_query', ui.Ship.filter())
async def ship(cb: CallbackQuery, callback_data: ui.Ship, bot: Bot, db: DB) -> None:
    order = db.order(callback_data.order)
    if not order or order["status"] != "paid":
        await cb.answer("Already handled", show_alert=True)
        return
    db.set_status(order["id"], "shipped")
    await cb.message.edit_text(ui.admin_order_text(db.order(order["id"]), order["buyer"]),
                               reply_markup=None)
    await bot.send_message(order["user_id"],
                           f"🚚 Good news! Your order <b>#{order['id']}</b> is on the way.")
    await cb.answer("Customer notified")


@on('callback_query', ui.AdminNav.filter(F.to == "broadcast"))
async def broadcast_start(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Broadcast.text)
    await cb.message.answer("📣 Send the message for all customers (or /cancel).")
    await cb.answer()


@on('message', Command("cancel"), Broadcast.text)
async def broadcast_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Broadcast cancelled.")


@on('message', Broadcast.text, F.text)
async def broadcast_send(message: Message, state: FSMContext, bot: Bot,
                         db: DB, settings: Settings) -> None:
    await state.clear()
    sent = 0
    for uid in db.user_ids():
        if uid in settings.admin_ids:
            continue
        try:
            await bot.send_message(uid, f"📣 {message.html_text}")
            sent += 1
        except Exception:
            pass  # user blocked the bot
        await asyncio.sleep(0.05)  # stay under Telegram's ~30 msg/s limit
    await message.answer(f"✅ Broadcast sent to {sent} customers.")


def setup() -> Router:
    r = build_router()
    r.message.middleware(AdminOnly())
    r.callback_query.middleware(AdminOnly())
    return r
