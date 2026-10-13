"""
add_manual.py — Add resources you supply by hand (for ones OSM/HRSA lack, or
where you have better data). Same safeguards as the rest: duplicate guard,
completeness gate, real hours from `schedule` (opening_hours format), HEALTH/
SHELTER/etc. service box by category.

CSV columns: name,category,address1,city,state,zip,phone,website,schedule
  category: emergency_shelter | fqhc | library | animal_shelter | resource_center
  schedule (optional): OSM opening_hours, e.g. "Mo-Fr 09:00-17:00; Sa 10:00-14:00"

  py add_manual.py --dry-run            # preview (no browser, no submit)
  py add_manual.py                      # add them (live)
  py add_manual.py --csv other.csv
"""
import csv, argparse
from pathlib import Path
import pipeline as P

FIELDS = ["name", "category", "address1", "city", "state", "zip", "phone", "website", "schedule"]


def load(path):
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        res = {k: (r.get(k) or "").strip() for k in FIELDS}
        res["category"] = res["category"] or "resource_center"
        res["country"] = "United States"
        rows.append(res)
    return rows


def complete(r):
    return bool(r["name"] and r["address1"] and r["city"] and r["state"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="manual_adds.csv")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--add-url", default="https://shelter.app/add_service")
    a = ap.parse_args()
    rows = load(a.csv)
    print(f"{len(rows)} resource(s) in {a.csv}")

    if a.dry_run:                       # preview only — no browser needed
        for r in rows:
            tag = "WOULD ADD" if complete(r) else "INCOMPLETE (need name+address+city+state)"
            print(f"  {tag}: {r['name']} | {r['address1']}, {r['city']} {r['state']} {r['zip']}"
                  f"{'  hours=' + r['schedule'] if r['schedule'] else ''}")
        return

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ud = str(Path.home() / ".shelterapp-audit-browser")
        ctx = p.chromium.launch_persistent_context(ud, headless=False, slow_mo=70,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            pg.goto(P.URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print("note:", e)
        print("\nLog in, get to Manage Services, then press ENTER.")
        input(">>> ")
        for r in rows:
            if not complete(r):
                print(f"  SKIPPED (incomplete): {r['name']}"); continue
            status = P.add_service(pg, P.URL, r, add_url=a.add_url)[2]
            print(f"  {status}: {r['name']}")
        print("\nClosing browser..."); ctx.close()


if __name__ == "__main__":
    main()
