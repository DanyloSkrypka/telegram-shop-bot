"""Local chat emulator: serves demo/index.html and drives the real bot handlers.

    .venv/bin/python -m demo.server        ->  http://127.0.0.1:8765

No Telegram token needed. Uses its own database in demo/.data (never the real one).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shop import fake_telegram as ft  # noqa: E402
from shop.harness import Harness  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / ".data"
CUSTOMER = ft.user(101, "Alex", "alex_buyer")
ADMIN = ft.user(999, "Shop owner", "shop_owner")
CHATS = {"customer": CUSTOMER, "admin": ADMIN}
lock = asyncio.Lock()


def new_harness() -> Harness:
    shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(parents=True)
    return Harness(db_path=str(DATA / "demo.db"), admin_id=ADMIN.id)


def serialize(h: Harness, sent: list[ft.Sent]) -> list[dict]:
    chat_name = {u.id: name for name, u in CHATS.items()}
    out = []
    for s in sent:
        p = s.payload
        ev = {"method": s.method, "chat": chat_name.get(s.chat_id), "id": s.message_id}
        if s.method in ("SendMessage", "EditMessageText", "EditMessageReplyMarkup"):
            msg = h.screen(s.chat_id).get(s.message_id, {})
            ev.update(text=msg.get("text"), markup=msg.get("markup"))
        elif s.method == "AnswerCallbackQuery":
            if not p.get("text"):
                continue
            ev.update(text=p["text"], alert=bool(p.get("show_alert")))
        elif s.method == "SendInvoice":
            ev.update(title=p["title"], description=p["description"], currency=p["currency"],
                      total=sum(x["amount"] for x in p["prices"]))
        elif s.method == "AnswerPreCheckoutQuery":
            ev.update(ok=p["ok"], error=p.get("error_message"))
        elif s.method == "SendChatAction":
            pass
        else:
            continue
        # callback toasts belong to whoever pressed; filled in by the caller
        out.append(ev)
    return out


async def index(_req: web.Request) -> web.Response:
    return web.FileResponse(HERE / "index.html")


async def reset(req: web.Request) -> web.Response:
    async with lock:
        req.app["state"]["h"] = new_harness()
    return web.json_response({"ok": True, "ai": req.app["state"]["h"].assistant.name})


async def action(req: web.Request) -> web.Response:
    body = await req.json()
    h: Harness = req.app["state"]["h"]
    who = body.get("chat", "customer")
    u = CHATS[who]
    async with lock:
        kind = body["type"]
        if kind == "text":
            sent = await h.send(u, body["text"][:1000])
        elif kind == "press":
            sent = await h.press(u, body["data"], int(body["message_id"]))
        elif kind == "pay":
            _ok, sent = await h.pay(u)
        else:
            return web.json_response({"error": "bad type"}, status=400)
    events = serialize(h, sent)
    for ev in events:
        if ev["chat"] is None:
            ev["chat"] = who
    return web.json_response({"events": events})


def make_app() -> web.Application:
    app = web.Application()
    app["state"] = {"h": new_harness()}
    app.router.add_get("/", index)
    app.router.add_post("/api/reset", reset)
    app.router.add_post("/api/action", action)
    return app


if __name__ == "__main__":
    port = int(os.getenv("DEMO_PORT", "8765"))
    print(f"Emulator: http://127.0.0.1:{port}")
    web.run_app(make_app(), host="127.0.0.1", port=port, print=None)
