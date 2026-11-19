"""Records the walkthrough video + screenshots from the local emulator.

    .venv/bin/python demo/record.py            # starts the emulator itself
Output: demo.mp4 (1080p) and docs/screenshots/*.png
"""
from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT / "demo" / "_video"
SHOTS = ROOT / "docs" / "screenshots"
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"


async def move_click(page: Page, locator, pause: float = 0.9) -> None:
    await locator.scroll_into_view_if_needed()
    box = await locator.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await page.mouse.move(x, y, steps=22)
    await asyncio.sleep(0.15)
    await page.mouse.down()
    await asyncio.sleep(0.08)
    await page.mouse.up()
    await idle(page)
    await asyncio.sleep(pause)


async def idle(page: Page) -> None:
    await asyncio.sleep(0.12)
    await page.evaluate("window.demo.idle()")


def btn(page: Page, phone: str, label: str):
    return page.locator(f"#phone-{phone} .kb button", has_text=label).last


async def type_in(page: Page, phone: str, text: str, pause: float = 1.0) -> None:
    inp = page.locator(f"#phone-{phone} .composer input")
    await move_click(page, inp, 0.2)
    await page.keyboard.type(text, delay=55)
    await asyncio.sleep(0.25)
    await page.keyboard.press("Enter")
    await idle(page)
    await asyncio.sleep(pause)


async def shot(page: Page, name: str) -> None:
    await page.evaluate("document.getElementById('cursor').style.visibility='hidden'")
    await page.screenshot(path=str(SHOTS / name))
    await page.evaluate("document.getElementById('cursor').style.visibility='visible'")


async def step(page: Page, n: int) -> None:
    await page.evaluate(f"window.demo.step({n})")


async def scenario(page: Page, recording: dict) -> None:
    await page.goto(URL)
    await page.wait_for_function("window.demoReady === true")
    await page.evaluate("window.demo.showCursor()")
    await page.mouse.move(960, 900)
    await step(page, 1)
    await asyncio.sleep(0.4)
    recording["on"] = True
    await asyncio.sleep(1.2)

    # 1. catalog
    await type_in(page, "customer", "/start", 1.4)
    await move_click(page, btn(page, "customer", "Catalog"), 0.8)
    await move_click(page, btn(page, "customer", "Coffee beans"), 0.9)
    await move_click(page, btn(page, "customer", "Ethiopia"), 1.4)

    # 2. cart
    await step(page, 2)
    await move_click(page, btn(page, "customer", "Add to cart"), 0.7)
    await move_click(page, btn(page, "customer", "Add to cart"), 1.0)
    await shot(page, "01-catalog-cart.png")
    await move_click(page, btn(page, "customer", "Cart"), 1.6)

    # 3. checkout
    await step(page, 3)
    await move_click(page, btn(page, "customer", "Checkout"), 0.8)
    await type_in(page, "customer", "Alex Weber", 0.8)
    await type_in(page, "customer", "Bahnhofstr. 12, 10115 Berlin, DE", 1.8)

    # 4. Stars payment
    await step(page, 4)
    await move_click(page, btn(page, "customer", "Pay"), 1.0)
    await move_click(page, page.locator("#phone-customer .inv-pay").last, 1.0)
    await shot(page, "02-stars-payment.png")
    await move_click(page, page.locator("#phone-customer .sheet .confirm"), 0.2)
    await page.wait_for_function(
        "[...document.querySelectorAll('#phone-admin .bubble')].some(b => b.textContent.includes('New paid order'))")
    await idle(page)

    # 5. owner gets the order
    await step(page, 5)
    await asyncio.sleep(3.0)
    await move_click(page, btn(page, "admin", "Mark as shipped"), 1.8)

    # 6. AI answers
    await step(page, 6)
    await move_click(page, btn(page, "customer", "Catalog"), 0.6)
    await move_click(page, btn(page, "customer", "Coffee beans"), 0.6)
    await move_click(page, btn(page, "customer", "Ethiopia"), 0.8)
    await move_click(page, btn(page, "customer", "Ask about this product"), 0.6)
    await type_in(page, "customer", "Is it good for espresso or filter?", 2.6)
    await shot(page, "03-admin-order-ai-answer.png")
    await step(page, 7)
    await asyncio.sleep(1.5)


async def main() -> None:
    shutil.rmtree(VIDEO_DIR, ignore_errors=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen([sys.executable, str(ROOT / "demo" / "server.py")], cwd=ROOT)
    try:
        for _ in range(50):
            try:
                import urllib.request
                urllib.request.urlopen(URL, timeout=0.5)
                break
            except Exception:
                time.sleep(0.2)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel="chrome", headless=True)
            ctx = await browser.new_context(viewport={"width": 1920, "height": 1080},
                                            device_scale_factor=1)
            page = await ctx.new_page()
            # Sharp capture: Chrome DevTools screencast (lossless-ish JPEG frames + timestamps)
            VIDEO_DIR.mkdir(parents=True)
            frames: list[tuple[float, Path]] = []
            cdp = await ctx.new_cdp_session(page)
            recording = {"on": False}

            async def on_frame(ev):
                await cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
                if not recording["on"]:
                    return
                path = VIDEO_DIR / f"{len(frames):05d}.jpg"
                path.write_bytes(base64.b64decode(ev["data"]))
                frames.append((time.time(), path))

            cdp.on("Page.screencastFrame", lambda ev: asyncio.ensure_future(on_frame(ev)))
            await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 95,
                                                    "maxWidth": 1920, "maxHeight": 1080})
            t0 = time.time()
            await scenario(page, recording)
            end_ts = time.time()
            await asyncio.sleep(0.3)
            await cdp.send("Page.stopScreencast")
            print(f"scenario: {end_ts - t0:.1f}s, frames: {len(frames)}, "
                  f"first at +{frames[0][0] - t0:.1f}s, span {frames[-1][0] - frames[0][0]:.1f}s")
            await ctx.close()
            await browser.close()
    finally:
        server.terminate()
    out = ROOT / "demo.mp4"
    concat = VIDEO_DIR / "list.txt"
    lines = []
    for i, (ts, path) in enumerate(frames):
        nxt = frames[i + 1][0] if i + 1 < len(frames) else end_ts
        lines += [f"file '{path.name}'", f"duration {max(nxt - ts, 0.001):.4f}"]
    lines.append(f"file '{frames[-1][1].name}'")
    concat.write_text("\n".join(lines) + "\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(concat), "-vf", "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-tune", "stillimage",
                    "-movflags", "+faststart", "-an", str(out)], check=True)
    shutil.rmtree(VIDEO_DIR, ignore_errors=True)
    print(f"video: {out}")


if __name__ == "__main__":
    asyncio.run(main())
