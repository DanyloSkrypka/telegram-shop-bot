"""Records the walkthrough video + screenshots from the local emulator.

    .venv/bin/python demo/record.py            # starts the emulator itself
Output: demo.mp4 (1080p60) and docs/screenshots/*.png
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
THEME = "light"  # Demo Showcase kit: "light" or "dark"


async def move_click(page: Page, locator, pause: float = 0.9) -> None:
    """No cursor in the video: a soft tap ripple on the element, then a real DOM click."""
    await locator.scroll_into_view_if_needed()
    await locator.evaluate("el => window.demo.tap(el)")
    await asyncio.sleep(0.22)
    await locator.evaluate("el => el.click()")
    await idle(page)
    await asyncio.sleep(pause)


async def idle(page: Page) -> None:
    await asyncio.sleep(0.12)
    await page.evaluate("window.demo.idle()")


def btn(page: Page, phone: str, label: str):
    return page.locator(f"#phone-{phone} .ds-kb__btn", has_text=label).last


async def type_in(page: Page, phone: str, text: str, pause: float = 1.0) -> None:
    inp = page.locator(f"#phone-{phone} .ds-composer input")
    await inp.evaluate("el => el.focus()")
    await asyncio.sleep(0.2)
    await page.keyboard.type(text, delay=55)
    await asyncio.sleep(0.25)
    await page.keyboard.press("Enter")
    await idle(page)
    await asyncio.sleep(pause)


async def shot(page: Page, name: str) -> None:
    await page.screenshot(path=str(SHOTS / name))


async def step(page: Page, n: int) -> None:
    await page.evaluate(f"window.demo.step({n})")


async def scenario(page: Page, recording: dict) -> None:
    await page.goto(URL + ("?theme=" + THEME))
    await page.wait_for_function("window.demoReady === true")
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
    await type_in(page, "customer", "Invalidenstr. 12, 10115 Berlin, DE", 1.8)

    # 4. Stars payment
    await step(page, 4)
    await move_click(page, btn(page, "customer", "Pay"), 1.0)
    await move_click(page, page.locator("#phone-customer .inv-pay").last, 1.0)
    await shot(page, "02-stars-payment.png")
    await move_click(page, page.locator("#phone-customer .ds-sheet .confirm"), 0.2)
    await page.wait_for_function(
        "[...document.querySelectorAll('#phone-admin .ds-bubble')].some(b => b.textContent.includes('New paid order'))")
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
    await page.evaluate("window.demo.endCard()")
    await asyncio.sleep(4.0)
    await shot(page, "04-end-card.png")


def encode_60fps(frames: list[tuple[float, Path]], end_ts: float, out: Path, fps: int = 60) -> None:
    """Screencast frames come only when the page changes, at irregular times. Resample them
    ourselves onto an exact 60 fps grid (latest frame at each tick) and pipe to ffmpeg."""
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps),
                             "-c:v", "mjpeg", "-i", "-", "-vf", "scale=1920:1080:flags=lanczos,format=yuv420p",
                             "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-r", str(fps),
                             "-movflags", "+faststart", "-an", str(out)], stdin=subprocess.PIPE)
    t0, j = frames[0][0], 0
    for k in range(int((end_ts - t0) * fps) + 1):
        t = t0 + k / fps
        while j + 1 < len(frames) and frames[j + 1][0] <= t + 1e-4:
            j += 1
        gap = frames[j + 1][0] - frames[j][0] if j + 1 < len(frames) else 0
        if 1.5 / fps < gap < 4.5 / fps and t - frames[j][0] >= 0.5 / fps:
            # Chrome dropped frames in the middle of an animation: fill the hole with a weighted
            # blend of the neighbours instead of holding the old frame (no visible hitch)
            w = (t - frames[j][0]) / gap
            proc.stdin.write(subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(frames[j][1]), "-i", str(frames[j + 1][1]), "-filter_complex",
                 f"blend=all_expr='A*{1 - w:.3f}+B*{w:.3f}'", "-q:v", "1", "-f", "mjpeg", "-"], capture_output=True, check=True).stdout)
            continue
        proc.stdin.write(frames[j][1].read_bytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed")


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
            browser = await pw.chromium.launch(channel="chrome", headless=True, args=[
                "--disable-gpu-vsync", "--disable-frame-rate-limit"])  # capture faster than 60 fps, resample to 60
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
                frames.append((ev["metadata"]["timestamp"], path))

            cdp.on("Page.screencastFrame", lambda ev: asyncio.ensure_future(on_frame(ev)))
            await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 95,
                                                    "maxWidth": 1920, "maxHeight": 1080})
            t0 = time.time()
            await scenario(page, recording)
            end_ts = time.time()
            await asyncio.sleep(0.3)
            await cdp.send("Page.stopScreencast")
            print(f"scenario: {time.time() - t0:.1f}s, frames: {len(frames)}, "
                  f"span {frames[-1][0] - frames[0][0]:.1f}s")
            await ctx.close()
            await browser.close()
    finally:
        server.terminate()
    out = ROOT / "demo.mp4"
    encode_60fps(frames, end_ts, out)
    shutil.rmtree(VIDEO_DIR, ignore_errors=True)
    print(f"video: {out}")


if __name__ == "__main__":
    asyncio.run(main())
