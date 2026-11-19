"""Product Q&A assistant.

`get_assistant()` returns a Claude-backed assistant when ANTHROPIC_API_KEY is set,
otherwise a template assistant that answers from product data with keyword rules.
Both share one interface: `await assistant.answer(question, product, shop_name)`.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Protocol

from .db import Product

log = logging.getLogger(__name__)

SHOP_FAQ = (
    "Shipping: EU-wide, 2-4 business days, free over 40 EUR, otherwise 4.90 EUR. "
    "Returns: 14 days for unopened items. Payment: Telegram Stars or card via Telegram. "
    "Support: reply in this chat, a human answers within 24 h on business days."
)


class Assistant(Protocol):
    name: str

    async def answer(self, question: str, product: Product | None, shop_name: str) -> str: ...


def _context(product: Product | None) -> str:
    if not product:
        return "No specific product selected."
    return (f"Product: {product.title}\nDescription: {product.description}\n"
            f"Details: {product.details}\nIn stock: {product.stock} pcs")


class TemplateAssistant:
    """Offline fallback: rule-based answers built only from known product facts."""

    name = "template"

    RULES: list[tuple[str, str]] = [
        (r"ship|deliver|post|send|arriv", "shipping"),
        (r"return|refund|exchange", "returns"),
        (r"stock|availab|left|have", "stock"),
        (r"pay|stars|card|price|cost|how much", "price"),
        (r"espresso|filter|v60|french|moka|chemex|brew|grind", "brew"),
        (r"origin|where|from|process|altitude|farm", "origin"),
        (r"size|weight|gsm|material|burr|capacity|warranty|fit", "specs"),
        (r"decaf|caffeine", "caffeine"),
    ]

    async def answer(self, question: str, product: Product | None, shop_name: str) -> str:
        q = question.lower()
        topic = next((t for pattern, t in self.RULES if re.search(pattern, q)), None)
        facts = self._facts(product)

        if topic == "shipping":
            return ("🚚 We ship across the EU in 2-4 business days. "
                    "Shipping is free on orders over 40 EUR, otherwise 4.90 EUR.")
        if topic == "returns":
            return "↩️ Unopened items can be returned within 14 days for a full refund."
        if product is None:
            return ("I can help with shipping, returns and payment. For product questions, "
                    "open a product in 🛍 Catalog and tap ❓ Ask about this product.")
        if topic == "stock":
            if product.stock > 0:
                return f"✅ Yes, {product.title} is in stock: {product.stock} pcs ready to ship."
            return f"⏳ {product.title} is sold out right now. Restock usually takes 1-2 weeks."
        if topic == "price":
            return (f"💳 {product.title} costs {product.price_stars} ⭐ "
                    f"(about {product.price_cents / 100:.2f} EUR). "
                    "You pay inside Telegram, no extra apps needed.")
        hits = self._match(facts, topic, q)
        if hits:
            return " ".join(hits[:2])
        return (f"Good question! Here is what I know about {product.title}: "
                f"{product.description} A team member will follow up if you need more.")

    @staticmethod
    def _facts(product: Product | None) -> list[str]:
        if not product:
            return []
        return [s.strip() for s in re.split(r"(?<=\.)\s+", product.details) if s.strip()]

    @staticmethod
    def _match(facts: list[str], topic: str | None, q: str) -> list[str]:
        keys = {
            "brew": ["best for", "great for", "good for", "espresso", "filter", "press"],
            "origin": ["origin", "process", "altitude"],
            "specs": ["size", "weight", "material", "burrs", "capacity", "warranty", "fit"],
            "caffeine": ["decaf", "caffeine"],
        }.get(topic or "", [])
        words = [w for w in re.findall(r"[a-z0-9]{4,}", q)]
        scored = []
        for f in facts:
            fl = f.lower()
            score = sum(2 for k in keys if k in fl) + sum(1 for w in words if w in fl)
            if score:
                scored.append((score, f))
        return [f for _, f in sorted(scored, key=lambda x: -x[0])]


class ClaudeAssistant:
    """Answers with Claude via the official Anthropic SDK. Key comes from the environment."""

    name = "claude"

    def __init__(self, model: str):
        from anthropic import AsyncAnthropic  # imported lazily: optional dependency

        self.client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY itself
        self.model = model
        self.fallback = TemplateAssistant()

    async def answer(self, question: str, product: Product | None, shop_name: str) -> str:
        system = (
            f"You are the shop assistant of {shop_name}, a specialty coffee shop on Telegram. "
            "Answer the customer's question in 1-3 short sentences, friendly, in the customer's "
            "language. Use only the facts below; if the answer is not there, say a team member "
            "will reply and do not invent details. Plain text, at most one emoji.\n\n"
            f"Shop policies: {SHOP_FAQ}\n\n{_context(product)}"
        )
        try:
            resp = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": question[:1000]}],
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if resp.stop_reason == "refusal":
                return await self.fallback.answer(question, product, shop_name)
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return text or await self.fallback.answer(question, product, shop_name)
        except Exception as e:  # network/API problems must never break the shop
            log.warning("AI request failed (%s), using template answer", type(e).__name__)
            return await self.fallback.answer(question, product, shop_name)


def get_assistant(model: str = "claude-opus-5-5") -> Assistant:
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            return ClaudeAssistant(model)
        except ImportError:
            log.warning("ANTHROPIC_API_KEY is set but `anthropic` is not installed")
    return TemplateAssistant()
