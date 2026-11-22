#!/usr/bin/env python3
"""Capture reel.html frame-by-frame via Playwright (deterministic seek)."""
import os, argparse
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dur", type=float, required=True)
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    proxy = os.environ.get("HTTPS_PROXY")  # only set in restricted envs; absent on CI
    n = int(round(args.dur * args.fps))

    with sync_playwright() as p:
        kw = dict(args=["--hide-scrollbars", "--force-color-profile=srgb"])
        if proxy:
            kw["proxy"] = {"server": proxy}
        try:
            b = p.chromium.launch(**kw)            # Playwright's bundled Chromium (CI)
        except Exception:
            b = p.chromium.launch(channel="chrome", **kw)  # system Chrome fallback
        pg = b.new_page(viewport={"width": 1080, "height": 1920})
        pg.goto("file://" + os.path.abspath(args.html))
        try:
            pg.evaluate("document.fonts.ready")
        except Exception:
            pass
        pg.wait_for_timeout(600)
        for f in range(n):
            pg.evaluate(f"seek({f/args.fps})")
            pg.screenshot(path=f"{args.out}/f_{f:05d}.jpg", quality=92, type="jpeg")
        b.close()
    print("frames:", n, flush=True)


if __name__ == "__main__":
    main()
