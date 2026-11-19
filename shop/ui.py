"""Texts, money formatting, callback data and inline keyboards."""
from __future__ import annotations

from html import escape

from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup

from .config import Settings
from .db import Product


# --- callback data ---------------------------------------------------------
class Nav(CallbackData, prefix="nav"):
    to: str  # menu | catalog | cart | orders | ask | checkout | clear | pay | cancel


class Cat(CallbackData, prefix="cat"):
    id: int


class Prod(CallbackData, prefix="prod"):
    id: int


class CartOp(CallbackData, prefix="cart"):
    op: str  # add | inc | dec
    id: int


class Ask(CallbackData, prefix="ask"):
    id: int


class Ship(CallbackData, prefix="ship"):
    order: int


class AdminNav(CallbackData, prefix="adm"):
    to: str  # orders | broadcast | stats


# --- money -----------------------------------------------------------------
def unit_price(s: Settings, p: Product) -> int:
    return p.price_stars if s.payment_mode == "stars" else p.price_cents


def currency(s: Settings) -> str:
    return "XTR" if s.payment_mode == "stars" else s.currency


def money(amount: int, cur: str) -> str:
    if cur == "XTR":
        return f"{amount:,} ⭐".replace(",", " ")
    return f"{amount / 100:.2f} {'€' if cur == 'EUR' else cur}"


# --- texts -----------------------------------------------------------------
def welcome(s: Settings, first_name: str | None) -> str:
    name = escape(first_name or "there")
    return (f"👋 Hi, <b>{name}</b>! Welcome to <b>{escape(s.shop_name)}</b> — "
            "specialty coffee roasted every Monday.\n\n"
            "Browse the catalog, add items to your cart and pay right here in Telegram. "
            "Got a question? Just ask — our assistant answers instantly.")


def product_card(s: Settings, p: Product, in_cart: int) -> str:
    stock = f"✅ In stock: {p.stock}" if p.stock > 0 else "⏳ Sold out"
    cart = f"\n🛒 In your cart: {in_cart}" if in_cart else ""
    return (f"{p.emoji} <b>{escape(p.title)}</b>\n\n{escape(p.description)}\n\n"
            f"💰 <b>{money(unit_price(s, p), currency(s))}</b>   {stock}{cart}")


def cart_text(s: Settings, items: list[tuple[Product, int]]) -> str:
    if not items:
        return "🛒 Your cart is empty.\n\nOpen the catalog to add something tasty."
    cur = currency(s)
    lines = ["🛒 <b>Your cart</b>\n"]
    total = 0
    for p, qty in items:
        sub = unit_price(s, p) * qty
        total += sub
        lines.append(f"{p.emoji} {escape(p.title)} × {qty} — {money(sub, cur)}")
    lines.append(f"\n<b>Total: {money(total, cur)}</b>")
    return "\n".join(lines)


def cart_total(s: Settings, items: list[tuple[Product, int]]) -> int:
    return sum(unit_price(s, p) * q for p, q in items)


def admin_order_text(order, buyer: str) -> str:
    """Order card for the shop owner (also re-rendered after "Mark as shipped")."""
    import json

    items = "\n".join(f"• {i['emoji']} {escape(i['title'])} × {i['qty']}"
                      for i in json.loads(order["items"]))
    text = (f"🆕 <b>New paid order #{order['id']}</b>\n\n{items}\n\n"
            f"💰 <b>{money(order['total'], order['currency'])}</b>\n"
            f"👤 {escape(order['customer'])} ({escape(buyer)})\n📍 {escape(order['address'])}")
    if order["status"] == "shipped":
        text += "\n\n✅ <b>Shipped</b>"
    return text


# --- keyboards -------------------------------------------------------------
def main_menu(cart_count: int = 0) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🛍 Catalog", callback_data=Nav(to="catalog"))
    kb.button(text=f"🛒 Cart ({cart_count})" if cart_count else "🛒 Cart",
              callback_data=Nav(to="cart"))
    kb.button(text="📦 My orders", callback_data=Nav(to="orders"))
    kb.button(text="💬 Ask a question", callback_data=Nav(to="ask"))
    kb.adjust(2, 2)
    return kb.as_markup()


def categories_kb(cats) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in cats:
        kb.button(text=f"{c['emoji']} {c['title']}", callback_data=Cat(id=c["id"]))
    kb.button(text="⬅️ Back", callback_data=Nav(to="menu"))
    kb.adjust(1)
    return kb.as_markup()


def products_kb(s: Settings, products: list[Product]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in products:
        kb.button(text=f"{p.emoji} {p.title} · {money(unit_price(s, p), currency(s))}",
                  callback_data=Prod(id=p.id))
    kb.button(text="⬅️ Categories", callback_data=Nav(to="catalog"))
    kb.adjust(1)
    return kb.as_markup()


def product_kb(p: Product, cart_count: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if p.stock > 0:
        kb.button(text="+ Add to cart", callback_data=CartOp(op="add", id=p.id))
    kb.button(text="❓ Ask about this product", callback_data=Ask(id=p.id))
    kb.button(text=f"🛒 Cart ({cart_count})" if cart_count else "🛒 Cart",
              callback_data=Nav(to="cart"))
    kb.button(text="⬅️ Back", callback_data=Cat(id=p.category_id))
    kb.adjust(1, 1, 2)
    return kb.as_markup()


def cart_kb(items: list[tuple[Product, int]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    sizes = []
    for p, qty in items:
        kb.button(text="−", callback_data=CartOp(op="dec", id=p.id))
        kb.button(text=f"{p.emoji} {qty}", callback_data=Prod(id=p.id))
        kb.button(text="+", callback_data=CartOp(op="inc", id=p.id))
        sizes.append(3)
    if items:
        kb.button(text="✅ Checkout", callback_data=Nav(to="checkout"))
        kb.button(text="🗑 Clear", callback_data=Nav(to="clear"))
        sizes.append(2)
    kb.button(text="🛍 Catalog", callback_data=Nav(to="catalog"))
    kb.button(text="⬅️ Menu", callback_data=Nav(to="menu"))
    sizes.append(2)
    kb.adjust(*sizes)
    return kb.as_markup()


def confirm_kb(total_label: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"💳 Pay {total_label}", callback_data=Nav(to="pay"))
    kb.button(text="✖️ Cancel", callback_data=Nav(to="cancel"))
    kb.adjust(1)
    return kb.as_markup()


def answer_kb(pid: int | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if pid:
        kb.button(text="⬅️ Back to product", callback_data=Prod(id=pid))
    kb.button(text="🛍 Catalog", callback_data=Nav(to="catalog"))
    kb.button(text="🏠 Menu", callback_data=Nav(to="menu"))
    kb.adjust(1, 2) if pid else kb.adjust(2)
    return kb.as_markup()


def menu_only() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Menu", callback_data=Nav(to="menu"))
    return kb.as_markup()


def admin_order_kb(order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📦 Mark as shipped", callback_data=Ship(order=order_id))
    return kb.as_markup()


def admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Recent orders", callback_data=AdminNav(to="orders"))
    kb.button(text="📣 Broadcast", callback_data=AdminNav(to="broadcast"))
    kb.button(text="🔄 Refresh stats", callback_data=AdminNav(to="stats"))
    kb.adjust(2, 1)
    return kb.as_markup()
