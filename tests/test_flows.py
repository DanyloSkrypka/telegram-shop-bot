"""End-to-end scenarios against the real handlers with a mocked Telegram API."""
import json

import pytest

from shop import fake_telegram as ft
from shop.ai import TemplateAssistant, get_assistant
from shop.harness import Harness

pytestmark = pytest.mark.asyncio

ALEX = ft.user(101, "Alex", "alex_buyer")
ADMIN = ft.user(999, "Owner", "shop_owner")


def texts(sent, method="SendMessage"):
    return [s.payload.get("text", "") for s in sent if s.method == method]


async def fill_cart(h: Harness):
    await h.send(ALEX, "/start")
    await h.click(ALEX, "Catalog")
    await h.click(ALEX, "Coffee beans")
    await h.click(ALEX, "Ethiopia")
    await h.click(ALEX, "Add to cart")
    await h.click(ALEX, "Add to cart")


async def checkout(h: Harness):
    await h.click(ALEX, "Cart")
    await h.click(ALEX, "Checkout")
    await h.send(ALEX, "Alex Weber")
    await h.send(ALEX, "Bahnhofstr. 12, 10115 Berlin, Germany")
    await h.click(ALEX, "Pay")


async def test_start_shows_menu(h):
    sent = await h.send(ALEX, "/start")
    assert "Welcome to <b>Nordic Roast</b>" in texts(sent)[0]
    labels = [b["text"] for row in sent[0].payload["reply_markup"]["inline_keyboard"] for b in row]
    assert labels == ["🛍 Catalog", "🛒 Cart", "📦 My orders", "💬 Ask a question"]
    assert h.db.user_ids() == [101]


async def test_catalog_to_product_card(h):
    await h.send(ALEX, "/start")
    sent = await h.click(ALEX, "Catalog")
    assert "Choose a category" in texts(sent, "EditMessageText")[0]
    sent = await h.click(ALEX, "Coffee beans")
    assert "Yirgacheffe" in str(sent[0].payload["reply_markup"])
    sent = await h.click(ALEX, "Ethiopia")
    card = texts(sent, "EditMessageText")[0]
    assert "Ethiopia Yirgacheffe" in card and "150 ⭐" in card and "In stock: 24" in card


async def test_add_to_cart_updates_card_and_toast(h):
    await fill_cart(h)
    toasts = [s.payload.get("text") for s in h.log if s.method == "AnswerCallbackQuery"]
    assert "✅ Ethiopia Yirgacheffe added · 2 in cart" in toasts
    _, msg = h.last_message(ALEX.id)
    assert "In your cart: 2" in msg["text"]
    assert any("Cart (2)" in b["text"] for row in msg["markup"] for b in row)


async def test_cart_plus_minus_and_total(h):
    await fill_cart(h)
    sent = await h.click(ALEX, "Cart")
    cart = texts(sent, "EditMessageText")[0]
    assert "× 2 — 300 ⭐" in cart and "Total: 300 ⭐" in cart
    sent = await h.click(ALEX, "➖")
    assert "Total: 150 ⭐" in texts(sent, "EditMessageText")[0]
    await h.click(ALEX, "➖")
    _, msg = h.last_message(ALEX.id)
    assert "cart is empty" in msg["text"]


async def test_stock_limit(h):
    await h.send(ALEX, "/start")
    h.db.conn.execute("UPDATE products SET stock=1 WHERE id=1")
    await h.click(ALEX, "Catalog")
    await h.click(ALEX, "Coffee beans")
    await h.click(ALEX, "Ethiopia")
    await h.click(ALEX, "Add to cart")
    sent = await h.click(ALEX, "Add to cart")
    alert = next(s for s in sent if s.method == "AnswerCallbackQuery")
    assert alert.payload["text"] == "Only 1 left in stock" and alert.payload["show_alert"]


async def test_checkout_validation(h):
    await fill_cart(h)
    await h.click(ALEX, "Cart")
    await h.click(ALEX, "Checkout")
    assert "at least 2 letters" in texts(await h.send(ALEX, "A"))[0]
    await h.send(ALEX, "Alex Weber")
    assert "too short" in texts(await h.send(ALEX, "Mind"))[0]
    sent = await h.send(ALEX, "Bahnhofstr. 12, 10115 Berlin, Germany")
    summary = texts(sent)[0]
    assert "Alex Weber" in summary and "Berlin" in summary and "Total: 300 ⭐" in summary


async def test_full_purchase_with_stars_and_admin_notification(h):
    await fill_cart(h)
    await checkout(h)
    inv = h.last_invoice(ALEX.id).payload
    assert inv["currency"] == "XTR" and inv["provider_token"] == ""
    assert inv["prices"][0]["amount"] == 300 and inv["payload"].startswith("order:")

    ok, sent = await h.pay(ALEX)
    assert ok
    assert any("Payment received" in t for t in texts(sent))
    order = h.db.order(int(inv["payload"].split(":")[1]))
    assert order["status"] == "paid" and order["total"] == 300
    assert h.db.cart(ALEX.id) == []
    assert h.db.product(1).stock == 22

    admin_msgs = [s for s in sent if s.method == "SendMessage" and s.chat_id == ADMIN.id]
    assert len(admin_msgs) == 1
    a = admin_msgs[0].payload["text"]
    assert "New paid order" in a and "Ethiopia Yirgacheffe × 2" in a and "@alex_buyer" in a
    assert "Bahnhofstr. 12" in a


async def test_admin_marks_shipped_and_customer_is_notified(h):
    await fill_cart(h)
    await checkout(h)
    await h.pay(ALEX)
    sent = await h.click(ADMIN, "Mark as shipped")
    to_customer = [s for s in sent if s.method == "SendMessage" and s.chat_id == ALEX.id]
    assert "is on the way" in to_customer[0].payload["text"]
    edited = texts(sent, "EditMessageText")[0]
    assert "✅ <b>Shipped</b>" in edited and "<b>New paid order #1</b>" in edited
    assert "&lt;" not in edited and "@alex_buyer" in edited
    sent = await h.click(ALEX, "My orders")
    assert "🚚 Shipped" in texts(sent, "EditMessageText")[0]


async def test_pre_checkout_rejects_sold_out(h):
    await fill_cart(h)
    await checkout(h)
    h.db.conn.execute("UPDATE products SET stock=1 WHERE id=1")
    ok, sent = await h.pay(ALEX)
    assert not ok
    ans = next(s for s in sent if s.method == "AnswerPreCheckoutQuery")
    assert "sold out" in ans.payload["error_message"]


async def test_pre_checkout_rejects_double_payment(h):
    await fill_cart(h)
    await checkout(h)
    assert (await h.pay(ALEX))[0]
    ok, _ = await h.pay(ALEX)  # same invoice again
    assert not ok


async def test_provider_mode_uses_test_provider_and_eur(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    h = Harness(payment_mode="provider")
    await fill_cart(h)
    await checkout(h)
    inv = h.last_invoice(ALEX.id).payload
    assert inv["currency"] == "EUR" and inv["prices"][0]["amount"] == 2980
    assert inv["provider_token"] == "TEST:provider"
    assert (await h.pay(ALEX))[0]


async def test_ask_about_product_template_ai(h):
    await h.send(ALEX, "/start")
    await h.click(ALEX, "Catalog")
    await h.click(ALEX, "Coffee beans")
    await h.click(ALEX, "Ethiopia")
    await h.click(ALEX, "Ask about this product")
    sent = await h.send(ALEX, "Is it good for espresso or filter?")
    assert any(s.method == "SendChatAction" for s in sent)
    reply = texts(sent)[0]
    assert reply.startswith("🤖") and "filter" in reply.lower()
    sent = await h.send(ALEX, "How fast is shipping?")
    assert "2-4 business days" in texts(sent)[0]
    sent = await h.send(ALEX, "Do you have it in stock?")
    assert "24 pcs" in texts(sent)[0]


async def test_free_text_goes_to_assistant(h):
    await h.send(ALEX, "/start")
    sent = await h.send(ALEX, "can I return it?")
    assert "14 days" in texts(sent)[0]


async def test_admin_panel_is_private_and_broadcast_works(h):
    await h.send(ALEX, "/start")
    assert await h.send(ALEX, "/admin") == []  # non-admin gets nothing
    sent = await h.send(ADMIN, "/admin")
    assert "Admin panel" in texts(sent)[0] and "Customers: <b>1</b>" in texts(sent)[0]
    await h.click(ADMIN, "Broadcast")
    sent = await h.send(ADMIN, "Fresh Kenya AA is in!")
    to_alex = [s for s in sent if s.method == "SendMessage" and s.chat_id == ALEX.id]
    assert to_alex and "Fresh Kenya AA" in to_alex[0].payload["text"]
    assert "sent to 1 customers" in texts(sent)[-1]


async def test_html_is_escaped(h):
    await fill_cart(h)
    await h.click(ALEX, "Cart")
    await h.click(ALEX, "Checkout")
    await h.send(ALEX, "<b>Hacker</b>")
    sent = await h.send(ALEX, "<script>x</script> street 1, Berlin")
    assert "&lt;b&gt;Hacker&lt;/b&gt;" in texts(sent)[0]
    assert "<script>" not in texts(sent)[0]


async def test_assistant_selection(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(get_assistant(), TemplateAssistant)


async def test_claude_assistant_falls_back_on_error(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    a = get_assistant()
    assert a.name == "claude"

    async def boom(**kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(a.client.beta.messages, "create", boom)
    h = Harness()
    reply = await a.answer("shipping time?", h.db.product(1), "Nordic Roast")
    assert "2-4 business days" in reply
