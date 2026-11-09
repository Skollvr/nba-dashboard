"""Browser-based desktop/mobile smoke checks of the actual Streamlit UI.

Screenshots are CI artifacts, not substitutes for manual visual approval.
No requests for real games/odds or betting operations are made.
"""
from __future__ import annotations

from pathlib import Path
import re

from playwright.sync_api import sync_playwright

OUT = Path("visual_smoke")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8501"


def _check(page, label: str):
    page.get_by_text("NBA Props Dashboard", exact=False).first.wait_for(
        state="visible", timeout=90000
    )
    text = page.locator("body").inner_text()
    assert "NBA Props Dashboard" in text, f"{label}: app heading missing"
    assert "Traceback (most recent call last)" not in text, f"{label}: visible Python error"
    width = page.evaluate("document.documentElement.scrollWidth")
    viewport = page.viewport_size["width"]
    if width > 1.5 * viewport:
        raise AssertionError(f"{label}: catastrophic horizontal overflow {width}>{viewport}")
    page.screenshot(path=str(OUT / (label + ".png")), full_page=True)
    print(f"{label}: screenshot generated, document width {width}, viewport {viewport}")


def _open_section(page, section: str):
    target = page.get_by_text(re.compile(re.escape(section))).first
    target.click(timeout=12000)
    page.wait_for_timeout(900)


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        desktop = browser.new_page(viewport={"width":1440,"height":900},device_scale_factor=1)
        desktop.goto(BASE,wait_until="domcontentloaded",timeout=60000)
        _check(desktop,"desktop-home")
        _open_section(desktop,"Radar da rodada")
        _check(desktop,"desktop-radar")
        _open_section(desktop,"Desempenho do Modelo")
        _check(desktop,"desktop-performance")
        desktop.close()

        mobile = browser.new_page(viewport={"width":390,"height":844},device_scale_factor=1,
                                  is_mobile=True,has_touch=True)
        mobile.goto(BASE,wait_until="domcontentloaded",timeout=60000)
        _check(mobile,"mobile-home")
        _open_section(mobile,"Radar da rodada")
        _check(mobile,"mobile-radar")
        mobile.close()
        browser.close()


if __name__ == "__main__":
    main()
