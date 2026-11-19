"""Customer-facing handlers: menu, catalog, cart, checkout, payment, AI questions."""
from __future__ import annotations

import json
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from .. import ui
from ..ai import Assistant
from ..config import Settings
from ..db import DB

log = logging.getLogger(__name__)
_routes: list = []


def on(kind: str, *filters):
    """Collect handlers so a fresh Router can be built per dispatcher (tests, emulator)."""
    def deco(fn):
        _routes.append((kind, filters, fn))
        return fn
    return deco


def build_router() -> Router:
    r = Router(name="shop")
    for kind, filters, fn in _routes:
        getattr(r, kind).register(fn, *filters)
    return r


class Checkout(StatesGroup):
    name = State()
    address = State()
    confirm = State()


class Asking(StatesGroup):
    question = State()


def _cart_count(db: DB, user_id: int) -> int:
    return sum(q for _, q in db.cart(user_id))


async def _show(cb: CallbackQuery, text: str, markup) -> None:
    """Edit the message the button belongs to (keeps the chat clean, like real shop bots)."""
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except Exception:  # message too old / unchanged -> send a new one
        await cb.message.answer(text, reply_markup=markup)


# --- start & menu ----------------------------------------------------------
@on('message', CommandStart())
async def start(message: Message, state: FSMContext, db: DB, settings: Settings) -> None:
    await state.clear()
    u = message.from_user
    db.touch_user(u.id, u.username, u.first_name)
    await message.answer(ui.welcome(settings, u.first_name),
                         reply_markup=ui.main_menu(_cart_count(db, u.id)))


@on('message', Command("menu"))
async def menu_cmd(message: Message, state: FSMContext, db: DB) -> None:
    await state.clear()
    await message.answer("🏠 <b>Main menu</b>",
                         reply_markup=ui.main_menu(_cart_count(db, message.from_user.id)))


@on('callback_query', ui.Nav.filter(F.to == "menu"))
async def menu_cb(cb: CallbackQuery, state: FSMContext, db: DB) -> None:
    await state.clear()
    await _show(cb, "🏠 <b>Main menu</b>\n\nWhat would you like to do?",
                ui.main_menu(_cart_count(db, cb.from_user.id)))
    await cb.answer()


# --- catalog ---------------------------------------------------------------
@on('callback_query', ui.Nav.filter(F.to == "catalog"))
async def catalog(cb: CallbackQuery, state: FSMContext, db: DB) -> None:
    await state.clear()
    await _show(cb, "🛍 <b>Catalog</b>\n\nChoose a category:", ui.categories_kb(db.categories()))
    await cb.answer()


@on('callback_query', ui.Cat.filter())
async def category(cb: CallbackQuery, callback_data: ui.Cat, db: DB, settings: Settings) -> None:
    cat = db.category(callback_data.id)
    if not cat:
        await cb.answer("Category not found", show_alert=True)
        return
    await _show(cb, f"{cat['emoji']} <b>{escape(cat['title'])}</b>\n\nTap a product to see details:",
                ui.products_kb(settings, db.products(cat["id"])))
    await cb.answer()


@on('callback_query', ui.Prod.filter())
async def product(cb: CallbackQuery, callback_data: ui.Prod, state: FSMContext,
                  db: DB, settings: Settings) -> None:
    await state.clear()
    p = db.product(callback_data.id)
    if not p:
        await cb.answer("Product not found", show_alert=True)
        return
    in_cart = dict((x.id, q) for x, q in db.cart(cb.from_user.id)).get(p.id, 0)
    await _show(cb, ui.product_card(settings, p, in_cart),
                ui.product_kb(p, _cart_count(db, cb.from_user.id)))
    await cb.answer()


# --- cart ------------------------------------------------------------------
@on('callback_query', ui.CartOp.filter())
async def cart_op(cb: CallbackQuery, callback_data: ui.CartOp, db: DB, settings: Settings) -> None:
    uid = cb.from_user.id
    p = db.product(callback_data.id)
    if not p:
        await cb.answer("Product not found", show_alert=True)
        return
    current = dict((x.id, q) for x, q in db.cart(uid)).get(p.id, 0)
    if callback_data.op in ("add", "inc"):
        if current >= p.stock:
            await cb.answer(f"Only {p.stock} left in stock", show_alert=True)
            return
        qty = db.cart_add(uid, p.id, 1)
    else:
        qty = db.cart_add(uid, p.id, -1)

    if callback_data.op == "add":
        await cb.answer(f"✅ {p.title} added · {qty} in cart")
        await _show(cb, ui.product_card(settings, p, qty), ui.product_kb(p, _cart_count(db, uid)))
    else:
        await cb.answer()
        items = db.cart(uid)
        await _show(cb, ui.cart_text(settings, items), ui.cart_kb(items))


@on('callback_query', ui.Nav.filter(F.to == "cart"))
async def cart(cb: CallbackQuery, state: FSMContext, db: DB, settings: Settings) -> None:
    await state.clear()
    items = db.cart(cb.from_user.id)
    await _show(cb, ui.cart_text(settings, items), ui.cart_kb(items))
    await cb.answer()


@on('callback_query', ui.Nav.filter(F.to == "clear"))
async def cart_clear(cb: CallbackQuery, db: DB, settings: Settings) -> None:
    db.cart_clear(cb.from_user.id)
    await _show(cb, ui.cart_text(settings, []), ui.cart_kb([]))
    await cb.answer("Cart cleared")


# --- checkout --------------------------------------------------------------
@on('callback_query', ui.Nav.filter(F.to == "checkout"))
async def checkout(cb: CallbackQuery, state: FSMContext, db: DB) -> None:
    if not db.cart(cb.from_user.id):
        await cb.answer("Your cart is empty", show_alert=True)
        return
    await state.set_state(Checkout.name)
    await _show(cb, "📝 <b>Checkout · step 1/2</b>\n\nWhat name should we put on the parcel?", None)
    await cb.answer()


@on('message', Checkout.name, F.text)
async def checkout_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()[:80]
    if len(name) < 2:
        await message.answer("Please send your full name (at least 2 letters).")
        return
    await state.update_data(name=name)
    await state.set_state(Checkout.address)
    await message.answer("📍 <b>Checkout · step 2/2</b>\n\n"
                         "Delivery address? (street, house, ZIP, city, country)")


@on('message', Checkout.address, F.text)
async def checkout_address(message: Message, state: FSMContext, db: DB, settings: Settings) -> None:
    address = message.text.strip()[:300]
    if len(address) < 8:
        await message.answer("That looks too short — please send the full address.")
        return
    await state.update_data(address=address)
    await state.set_state(Checkout.confirm)
    data = await state.get_data()
    items = db.cart(message.from_user.id)
    total = ui.cart_total(settings, items)
    text = (f"{ui.cart_text(settings, items)}\n\n"
            f"👤 {escape(data['name'])}\n📍 {escape(address)}\n\nAll correct?")
    await message.answer(text, reply_markup=ui.confirm_kb(ui.money(total, ui.currency(settings))))


@on('callback_query', ui.Nav.filter(F.to == "cancel"))
async def checkout_cancel(cb: CallbackQuery, state: FSMContext, db: DB) -> None:
    await state.clear()
    await _show(cb, "Checkout cancelled. Your cart is saved.",
                ui.main_menu(_cart_count(db, cb.from_user.id)))
    await cb.answer()


@on('callback_query', ui.Nav.filter(F.to == "pay"), Checkout.confirm)
async def pay(cb: CallbackQuery, state: FSMContext, bot: Bot, db: DB, settings: Settings) -> None:
    uid = cb.from_user.id
    items = db.cart(uid)
    if not items:
        await cb.answer("Your cart is empty", show_alert=True)
        return
    data = await state.get_data()
    cur = ui.currency(settings)
    total = ui.cart_total(settings, items)
    order_items = [{"id": p.id, "title": p.title, "emoji": p.emoji, "qty": q,
                    "price": ui.unit_price(settings, p)} for p, q in items]
    u = cb.from_user
    buyer = f"@{u.username}" if u.username else (u.first_name or str(u.id))
    order_id = db.create_order(uid, data["name"], data["address"], order_items, total, cur, buyer)
    await state.clear()
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)

    desc = ", ".join(f"{i['title']} ×{i['qty']}" for i in order_items)[:255]
    await bot.send_invoice(
        chat_id=cb.message.chat.id,
        title=f"{settings.shop_name} · Order #{order_id}",
        description=desc,
        payload=f"order:{order_id}",
        currency=cur,
        prices=[LabeledPrice(label=f"Order #{order_id}", amount=total)],
        # Telegram Stars need an empty provider token; card payments use the BotFather token
        provider_token="" if cur == "XTR" else settings.provider_token,
    )


@on('pre_checkout_query', )
async def pre_checkout(query: PreCheckoutQuery, db: DB) -> None:
    """Last check before Telegram charges the user: order exists, unpaid, stock is enough."""
    try:
        order_id = int(query.invoice_payload.split(":")[1])
    except (IndexError, ValueError):
        await query.answer(ok=False, error_message="Unknown order.")
        return
    order = db.order(order_id)
    if not order or order["status"] != "pending" or order["user_id"] != query.from_user.id:
        await query.answer(ok=False, error_message="This order is no longer valid.")
        return
    if query.total_amount != order["total"]:
        await query.answer(ok=False, error_message="Amount mismatch, please checkout again.")
        return
    for it in json.loads(order["items"]):
        p = db.product(it["id"])
        if not p or p.stock < it["qty"]:
            await query.answer(ok=False, error_message=f"Sorry, {it['title']} just sold out.")
            return
    await query.answer(ok=True)


@on('message', F.successful_payment)
async def paid(message: Message, bot: Bot, db: DB, settings: Settings) -> None:
    sp = message.successful_payment
    order_id = int(sp.invoice_payload.split(":")[1])
    db.mark_paid(order_id, sp.telegram_payment_charge_id)
    db.cart_clear(message.from_user.id)
    order = db.order(order_id)
    total = ui.money(order["total"], order["currency"])
    await message.answer(
        f"✅ <b>Payment received — thank you!</b>\n\nOrder <b>#{order_id}</b> · {total}\n"
        "We roast on Monday and ship within 48 h. You'll get a message when it's on the way.",
    )
    await message.answer("🏠 <b>Main menu</b>\n\nAnything else?", reply_markup=ui.main_menu(0))
    admin_text = ui.admin_order_text(order, order["buyer"])
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, admin_text, reply_markup=ui.admin_order_kb(order_id))
        except Exception as e:
            log.warning("Cannot notify admin %s: %s", admin_id, e)


# --- orders ----------------------------------------------------------------
@on('callback_query', ui.Nav.filter(F.to == "orders"))
async def my_orders(cb: CallbackQuery, db: DB) -> None:
    orders = db.user_orders(cb.from_user.id)
    if not orders:
        text = "📦 You have no orders yet."
    else:
        icon = {"paid": "🟡 Paid", "shipped": "🚚 Shipped"}
        text = "📦 <b>Your orders</b>\n\n" + "\n".join(
            f"#{o['id']} · {ui.money(o['total'], o['currency'])} · {icon.get(o['status'], o['status'])}"
            for o in orders)
    await _show(cb, text, ui.menu_only())
    await cb.answer()


# --- AI questions ----------------------------------------------------------
@on('callback_query', ui.Ask.filter())
async def ask_product(cb: CallbackQuery, callback_data: ui.Ask, state: FSMContext, db: DB) -> None:
    p = db.product(callback_data.id)
    if not p:
        await cb.answer("Product not found", show_alert=True)
        return
    await state.set_state(Asking.question)
    await state.update_data(pid=p.id)
    await cb.message.answer(f"❓ Ask anything about <b>{escape(p.title)}</b> — "
                            "brewing, origin, stock, shipping…")
    await cb.answer()


@on('callback_query', ui.Nav.filter(F.to == "ask"))
async def ask_general(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Asking.question)
    await state.update_data(pid=None)
    await cb.message.answer("💬 Type your question — shipping, returns, payment, anything.")
    await cb.answer()


async def _answer_question(message: Message, bot: Bot, pid: int | None, db: DB,
                           settings: Settings, assistant: Assistant) -> None:
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    product = db.product(pid) if pid else None
    reply = await assistant.answer(message.text, product, settings.shop_name)
    await message.answer(f"🤖 {escape(reply)}", reply_markup=ui.answer_kb(pid))


@on('message', Asking.question, F.text)
async def asked(message: Message, state: FSMContext, bot: Bot, db: DB,
                settings: Settings, assistant: Assistant) -> None:
    pid = (await state.get_data()).get("pid")
    await _answer_question(message, bot, pid, db, settings, assistant)


@on('message', StateFilter(None), F.text & ~F.text.startswith("/"))
async def free_text(message: Message, bot: Bot, db: DB, settings: Settings,
                    assistant: Assistant) -> None:
    """Any text outside a flow is treated as a question to the assistant."""
    await _answer_question(message, bot, None, db, settings, assistant)
