"""SQLite storage: products, carts, orders, users. Small and synchronous on purpose."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, emoji TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    title TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',      -- facts the AI assistant may use
    price_cents INTEGER NOT NULL,          -- price in EUR cents (provider mode)
    price_stars INTEGER NOT NULL,          -- price in Telegram Stars
    stock INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cart (
    user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, qty INTEGER NOT NULL,
    PRIMARY KEY (user_id, product_id)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    customer TEXT NOT NULL,
    address TEXT NOT NULL,
    buyer TEXT NOT NULL DEFAULT '',        -- @username for the owner
    items TEXT NOT NULL,                   -- JSON list
    total INTEGER NOT NULL,                -- in Stars or cents, see currency
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',-- pending -> paid -> shipped
    charge_id TEXT,
    created_at REAL NOT NULL
);
"""

SEED_CATEGORIES = [(1, "Coffee beans", "☕"), (2, "Brewing gear", "⚙️"), (3, "Merch", "👕")]

SEED_PRODUCTS = [
    (1, 1, "Ethiopia Yirgacheffe", "🌸",
     "Light roast with jasmine, bergamot and lemon notes. 250 g whole beans.",
     "Origin: Gedeo zone, Ethiopia. Process: washed. Altitude 1,900-2,200 m. Roast: light, "
     "roasted every Monday, shipped within 48 h of roasting. Best for filter/V60/Chemex. "
     "Available as whole beans or ground for filter. 100% Arabica, organic certified. "
     "Best before: 6 months after roast date.", 1490, 150, 24),
    (2, 1, "Colombia Huila", "🍫",
     "Medium roast: milk chocolate, caramel, red apple. 250 g whole beans.",
     "Origin: Huila, Colombia. Process: washed. Roast: medium. Great for espresso and "
     "moka pot, also works in French press. Low acidity. 100% Arabica.", 1290, 130, 40),
    (3, 1, "Decaf Brazil", "🌙",
     "Swiss Water decaf, nutty and sweet. 250 g whole beans.",
     "Origin: Cerrado, Brazil. Decaf method: Swiss Water (chemical free, 99.9% caffeine "
     "free). Roast: medium-dark. Good for evening espresso and milk drinks.", 1390, 140, 15),
    (4, 2, "Hand grinder Pro", "⚙️",
     "Stainless steel conical burrs, 40 click settings, fits in a travel bag.",
     "Burrs: 38 mm stainless steel conical. Capacity: 25 g. Weight: 480 g. Grind range: "
     "espresso to French press. Warranty: 2 years. Comes with cleaning brush.", 7900, 790, 8),
    (5, 2, "Glass V60 dripper", "🫖",
     "Heat-resistant glass pour-over dripper, size 02 (1-4 cups).",
     "Size 02, borosilicate glass, uses V60 02 paper filters (40 included). "
     "Dishwasher safe.", 2490, 250, 12),
    (6, 3, "Logo hoodie", "👕",
     "Heavyweight 400 gsm cotton hoodie with embroidered logo.",
     "Material: 100% organic cotton, 400 gsm. Sizes S-XXL, unisex relaxed fit. "
     "Color: washed black. Wash at 30 °C inside out.", 5900, 590, 20),
]


@dataclass
class Product:
    id: int
    category_id: int
    title: str
    emoji: str
    description: str
    details: str
    price_cents: int
    price_stars: int
    stock: int


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        if not self.conn.execute("SELECT 1 FROM products LIMIT 1").fetchone():
            self.seed()

    def seed(self) -> None:
        self.conn.executemany("INSERT INTO categories VALUES (?,?,?)", SEED_CATEGORIES)
        self.conn.executemany(
            "INSERT INTO products (id, category_id, title, emoji, description, details,"
            " price_cents, price_stars, stock) VALUES (?,?,?,?,?,?,?,?,?)",
            SEED_PRODUCTS,
        )
        self.conn.commit()

    # --- users -----------------------------------------------------------
    def touch_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        self.conn.execute(
            "INSERT INTO users (id, username, first_name, joined_at) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (user_id, username, first_name, time.time()),
        )
        self.conn.commit()

    def user_ids(self) -> list[int]:
        return [r[0] for r in self.conn.execute("SELECT id FROM users")]

    # --- catalog ---------------------------------------------------------
    def categories(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM categories ORDER BY id"))

    def category(self, cat_id: int):
        return self.conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()

    def products(self, cat_id: int) -> list[Product]:
        rows = self.conn.execute(
            "SELECT * FROM products WHERE category_id=? AND active=1 ORDER BY id", (cat_id,)
        )
        return [self._p(r) for r in rows]

    def all_products(self) -> list[Product]:
        return [self._p(r) for r in self.conn.execute("SELECT * FROM products WHERE active=1")]

    def product(self, pid: int) -> Product | None:
        r = self.conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        return self._p(r) if r else None

    @staticmethod
    def _p(r: sqlite3.Row) -> Product:
        return Product(**{k: r[k] for k in Product.__dataclass_fields__})

    # --- cart ------------------------------------------------------------
    def cart_add(self, user_id: int, pid: int, qty: int = 1) -> int:
        self.conn.execute(
            "INSERT INTO cart VALUES (?,?,?) ON CONFLICT(user_id, product_id) "
            "DO UPDATE SET qty = qty + excluded.qty",
            (user_id, pid, qty),
        )
        self.conn.execute("DELETE FROM cart WHERE qty <= 0")
        self.conn.commit()
        r = self.conn.execute(
            "SELECT qty FROM cart WHERE user_id=? AND product_id=?", (user_id, pid)
        ).fetchone()
        return r[0] if r else 0

    def cart_remove(self, user_id: int, pid: int) -> None:
        self.conn.execute("DELETE FROM cart WHERE user_id=? AND product_id=?", (user_id, pid))
        self.conn.commit()

    def cart_clear(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM cart WHERE user_id=?", (user_id,))
        self.conn.commit()

    def cart(self, user_id: int) -> list[tuple[Product, int]]:
        rows = self.conn.execute(
            "SELECT product_id, qty FROM cart WHERE user_id=? ORDER BY rowid", (user_id,)
        ).fetchall()
        out = []
        for pid, qty in rows:
            p = self.product(pid)
            if p:
                out.append((p, qty))
        return out

    # --- orders ----------------------------------------------------------
    def create_order(self, user_id: int, customer: str, address: str,
                     items: list[dict], total: int, currency: str, buyer: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO orders (user_id, customer, address, buyer, items, total, currency,"
            " created_at) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, customer, address, buyer, json.dumps(items), total, currency, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def order(self, order_id: int):
        return self.conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()

    def mark_paid(self, order_id: int, charge_id: str) -> None:
        o = self.order(order_id)
        if not o or o["status"] != "pending":
            return
        for it in json.loads(o["items"]):
            self.conn.execute(
                "UPDATE products SET stock = MAX(stock - ?, 0) WHERE id=?", (it["qty"], it["id"])
            )
        self.conn.execute(
            "UPDATE orders SET status='paid', charge_id=? WHERE id=?", (charge_id, order_id)
        )
        self.conn.commit()

    def set_status(self, order_id: int, status: str) -> None:
        self.conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
        self.conn.commit()

    def user_orders(self, user_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM orders WHERE user_id=? AND status!='pending' ORDER BY id DESC LIMIT 10",
            (user_id,)))

    def recent_orders(self, limit: int = 10) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM orders WHERE status!='pending' ORDER BY id DESC LIMIT ?", (limit,)))

    def stats(self) -> dict:
        q = self.conn.execute
        return {
            "users": q("SELECT COUNT(*) FROM users").fetchone()[0],
            "orders": q("SELECT COUNT(*) FROM orders WHERE status!='pending'").fetchone()[0],
            "revenue": q("SELECT COALESCE(SUM(total),0) FROM orders WHERE status!='pending'")
            .fetchone()[0],
            "to_ship": q("SELECT COUNT(*) FROM orders WHERE status='paid'").fetchone()[0],
        }
