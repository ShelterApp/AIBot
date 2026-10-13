"""
inspect_search.py — One-off diagnostic. Dumps the Manage Services top-bar
elements so we can find the real selector for the search (magnifier) control.
Writes audit_results/search_inspect.json.

    py inspect_search.py
"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://shelter.app/manage_services"
JS = r"""
() => {
  const out = [];
  const rec = (el, why) => {
    const r = el.getBoundingClientRect();
    out.push({
      why, tag: el.tagName,
      cls: (el.className && el.className.toString) ? el.className.toString().slice(0,80) : "",
      aria: el.getAttribute && el.getAttribute('aria-label'),
      role: el.getAttribute && el.getAttribute('role'),
      text: (el.textContent || "").trim().slice(0,40),
      x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)
    });
  };
  // leaf elements whose text is exactly 'search' (Material icon ligature)
  document.querySelectorAll('*').forEach(el => {
    if (el.children.length === 0) {
      const t = (el.textContent || "").trim().toLowerCase();
      if (t === 'search') rec(el, 'ligature=search');
    }
  });
  // anything with a search-ish aria-label
  document.querySelectorAll('[aria-label]').forEach(el => {
    if (/search/i.test(el.getAttribute('aria-label'))) rec(el, 'aria-label');
  });
  // clickable things in the top bar (y < 130)
  document.querySelectorAll("button, svg, i, [role='button'], img").forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.top < 130 && r.width > 0 && r.width < 120) rec(el, 'topbar');
  });
  return out;
}
"""

with sync_playwright() as p:
    ud = str(Path.home() / ".shelterapp-audit-browser")
    ctx = p.chromium.launch_persistent_context(ud, headless=False, slow_mo=60,
        viewport={"width": 1400, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        pg.goto(URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print("note:", e)
    print("\nLog in, get to Manage Services (do NOT open search yet), then press ENTER.")
    input(">>> ")
    ap = ctx.pages; pg = ap[-1] if len(ap) > 1 else pg
    pg.wait_for_timeout(1000)
    data = pg.evaluate(JS)
    placeholder = pg.locator("input[placeholder='Search Service']").count()
    out = {"candidates": data, "search_input_in_dom": placeholder}
    Path("audit_results").mkdir(exist_ok=True)
    Path("audit_results/search_inspect.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote audit_results/search_inspect.json ({len(data)} elements, "
          f"search input present in DOM: {placeholder})")
    print("Closing..."); ctx.close()
