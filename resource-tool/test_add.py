"""
test_add.py — Isolated test of the Add Service form (Stage 4).

Fills the form with one real resource and PAUSES without submitting, so you can
eyeball whether every field landed before any live listing is created.

    py test_add.py --add-url "https://shelter.app/<add-form-route>"
    py test_add.py --add-url "..." --submit     # actually create it

If you don't know the URL, omit --add-url and it'll try to click an
'Add Service' control on Manage Services after you log in.
"""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright
import pipeline as P

# A shelter WITH opening_hours so we can verify the schedule widget fills (and
# that contact-for-hours stays UNchecked). Fill-only test — never submitted.
RES = {"name": "ZZ Test Library (do not submit)", "address1": "123 Main St",
       "city": "Phoenix", "state": "AZ", "zip": "85001", "phone": "(602) 555-0100",
       "website": "", "category": "library", "country": "United States",
       "schedule": "Mo-Fr 09:00-17:00; Sa 10:00-14:00"}

ap = argparse.ArgumentParser()
ap.add_argument("--add-url", default=None)
ap.add_argument("--submit", action="store_true", help="actually submit (default: fill only)")
args = ap.parse_args()

print(f"Test resource: {RES['name']} | {RES['address1']}, {RES['city']} {RES['state']} {RES['zip']}")
with sync_playwright() as p:
    ud = str(Path.home() / ".shelterapp-audit-browser")
    ctx = p.chromium.launch_persistent_context(ud, headless=False, slow_mo=80,
        viewport={"width": 1400, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        pg.goto(args.add_url or P.URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print("note:", e)
    print("\nLog in (and if no --add-url, navigate to Manage Services). Then press ENTER.")
    input(">>> ")
    pages = ctx.pages; pg = pages[-1] if len(pages) > 1 else pg
    # duplicate guard searches the Manage Services list, so pass THAT url
    # (not the add-form url we may have landed on)
    list_url = P.URL
    result = P.add_service(pg, list_url, RES, dry_run=not args.submit, add_url=args.add_url)
    print("\nRESULT:", result)
    if not args.submit:
        print("(fill-only — nothing was submitted)")
    input("\nPress ENTER to close... ")
    ctx.close()
