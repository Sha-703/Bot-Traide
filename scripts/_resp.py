import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    nav = pw.chromium.launch(channel="msedge", headless=True)
    for width, height, tag in [(1440, 900, "desktop"), (1080, 720, "tablet-landscape"), (780, 900, "tablet"), (480, 900, "phone")]:
        page = nav.new_page(viewport={"width": width, "height": height})
        errs = []
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        page.goto("http://127.0.0.1:8080/", wait_until="networkidle")
        page.wait_for_timeout(2500)
        path = f"_resp_{tag}.png"
        page.screenshot(path=path, full_page=True)
        erreurs_layout = page.evaluate(
            "() => { return { topbarBox: document.querySelector('.topbar')?.getBoundingClientRect()?.toJSON(), overflow: document.documentElement.scrollWidth > window.innerWidth, bodyOverflow: document.body.scrollWidth > window.innerWidth }; }"
        )
        print(f"{tag:18s} viewport={width}x{height}  layout={erreurs_layout}  erreurs={errs or 'aucune'}")
        page.close()
    nav.close()
