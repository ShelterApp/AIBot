"""
batch_osm.py — Add missing OSM-category resources (default: resource_center)
across major US metros, piece by piece. Resume-aware like batch_fqhc.py.

OSM is queried by radius around a city (no statewide query), so this loops a
list of metros. Reuses the proven add_service (dedup guard, completeness gate,
hours from opening_hours, category->service box).

  py batch_osm.py --dry-run                       # preview all metros (no browser)
  py batch_osm.py --max-metros 3                  # next 3 pending metros
  py batch_osm.py --category library              # a different OSM category
  py batch_osm.py --add-max-per-metro 50          # cap adds per metro
  py batch_osm.py --metros "Los Angeles, CA;Phoenix, AZ"   # specific metros

Progress: audit_results/osm_progress_<category>.json ; summary: osm_batch_summary.csv
"""
import json, csv, argparse, time
from pathlib import Path
from datetime import datetime
import resource_sources as rs, match_audit as ma, pipeline as P

# ~60 of the largest US metros (city, state). Radius covers the surrounding area;
# overlaps are harmless — the per-add duplicate guard prevents double-adds.
METROS = [
    "New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX", "Phoenix, AZ",
    "Philadelphia, PA", "San Antonio, TX", "San Diego, CA", "Dallas, TX", "San Jose, CA",
    "Austin, TX", "Jacksonville, FL", "Fort Worth, TX", "Columbus, OH", "Charlotte, NC",
    "San Francisco, CA", "Indianapolis, IN", "Seattle, WA", "Denver, CO", "Washington, DC",
    "Boston, MA", "El Paso, TX", "Nashville, TN", "Oklahoma City, OK", "Las Vegas, NV",
    "Detroit, MI", "Portland, OR", "Memphis, TN", "Louisville, KY", "Milwaukee, WI",
    "Baltimore, MD", "Albuquerque, NM", "Tucson, AZ", "Fresno, CA", "Sacramento, CA",
    "Kansas City, MO", "Atlanta, GA", "Miami, FL", "Raleigh, NC", "Omaha, NE",
    "Minneapolis, MN", "Tulsa, OK", "Cleveland, OH", "Wichita, KS", "New Orleans, LA",
    "Honolulu, HI", "Anchorage, AK", "Bakersfield, CA", "Tampa, FL", "Aurora, CO",
    "Pittsburgh, PA", "Cincinnati, OH", "St. Louis, MO", "Salt Lake City, UT",
    "Birmingham, AL", "Richmond, VA", "Boise, ID", "Little Rock, AR", "Billings, MT",
    "Charleston, WV", "Jackson, MS", "Sioux Falls, SD", "Fargo, ND", "Portland, ME",
]


def progress_path(cat):
    return Path(f"audit_results/osm_progress_{cat}.json")


def is_complete(g):
    return bool(g.get("name") and g.get("address1") and g.get("city") and g.get("state"))


def append_summary(row):
    f = Path("audit_results/osm_batch_summary.csv")
    new = not f.exists()
    with open(f, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["timestamp", "metro", "category", "found", "missing", "added", "skipped", "failed"])
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="resource_center", choices=rs.OSM_CATEGORIES.keys())
    ap.add_argument("--metros", default=None, help="';'-separated 'City, ST' (default: built-in list)")
    ap.add_argument("--max-metros", type=int, default=None)
    ap.add_argument("--add-max-per-metro", type=int, default=None)
    ap.add_argument("--radius", type=float, default=30)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--add-url", default="https://shelter.app/add_service")
    a = ap.parse_args()

    pp = progress_path(a.category)
    try:
        prog = json.loads(pp.read_text(encoding="utf-8"))
    except Exception:
        prog = {"done": [], "handled": []}
    done, handled = set(prog.get("done", [])), set(prog.get("handled", []))

    metros = [m.strip() for m in a.metros.split(";")] if a.metros else \
             [m for m in METROS if m not in done]
    if a.max_metros:
        metros = metros[:a.max_metros]
    if not metros:
        print("Nothing to do — all requested metros already done."); return
    print(f"Category: {a.category} | metros this session: {len(metros)} "
          f"({len(done)}/{len(METROS)} already done)")

    try:
        master = json.loads(Path("audit_results/inventory_master.json").read_text(encoding="utf-8"))
    except Exception:
        master = []
    print(f"Cached inventory: {len(master)} listings\n")

    def run_browser(loop):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ud = str(Path.home() / ".shelterapp-audit-browser")
            ctx = p.chromium.launch_persistent_context(ud, headless=False, slow_mo=60,
                viewport={"width": 1400, "height": 900},
                args=["--disable-blink-features=AutomationControlled"])
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                pg.goto(P.URL, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print("note:", e)
            print("Log in, get to Manage Services, then press ENTER.  *** keep window open ***")
            input(">>> ")
            loop(pg)
            print("\nClosing browser..."); ctx.close()

    def process(pg):
        for metro in metros:
            print(f"\n{'='*52}\nMETRO: {metro}  ({a.category})")
            try:
                geo, src = rs.find_resources(metro, a.radius, [a.category])
            except Exception as e:
                print(f"  lookup failed: {e}")
                append_summary([datetime.now().isoformat(timespec="seconds"), metro, a.category, 0, 0, 0, 0, "FAIL"])
                continue
            gaps = [g for g in ma.find_gaps(master, src)
                    if is_complete(g) and g["name"] not in handled]
            print(f"  {len(src)} found; {len(gaps)} to add (complete, not yet handled)")
            cap = a.add_max_per_metro if a.add_max_per_metro is not None else len(gaps)
            added = skipped = failed = i = 0
            while i < len(gaps) and added < cap:
                g = gaps[i]; i += 1
                status = P.add_service(pg, P.URL, g, add_url=a.add_url)[2]
                if status == "SUBMITTED":
                    added += 1; handled.add(g["name"])
                elif status == "SKIPPED: already in ShelterApp":
                    skipped += 1; handled.add(g["name"])
                elif status.startswith("SKIPPED"):
                    skipped += 1
                else:
                    failed += 1
                print(f"    {status}: {g['name'][:42]}")
                prog["handled"] = sorted(handled); pp.write_text(json.dumps(prog, indent=1))
                time.sleep(0.3)
            append_summary([datetime.now().isoformat(timespec="seconds"), metro, a.category,
                            len(src), len(gaps), added, skipped, failed])
            # Mark done only if fully processed AND the lookup actually returned
            # data — 0 found in a major metro almost always means an Overpass
            # failure, so leave it pending to retry rather than skip it forever.
            fully = i >= len(gaps) and len(src) > 0
            if fully:
                done.add(metro); prog["done"] = sorted(done)
            prog["handled"] = sorted(handled); pp.write_text(json.dumps(prog, indent=1))
            tag = " COMPLETE" if fully else (" (0 found — will retry)" if not src else " (partial)")
            print(f"  {metro}: added {added}, skipped {skipped}, failed {failed}{tag}")

    if a.dry_run:                                    # preview, no browser
        for metro in metros:
            try:
                geo, src = rs.find_resources(metro, a.radius, [a.category])
            except Exception as e:
                print(f"  {metro}: lookup failed: {e}"); continue
            gaps = [g for g in ma.find_gaps(master, src)
                    if is_complete(g) and g["name"] not in handled]
            print(f"  {metro:28} {len(src):4} found | {len(gaps):4} would add")
        return
    run_browser(process)
    print(f"\nProgress: {len(done)}/{len(METROS)} metros done ({a.category}).")


if __name__ == "__main__":
    import sys
    Path("audit_results").mkdir(exist_ok=True)
    _logf = open("audit_results/batch_osm.log", "w", encoding="utf-8")
    sys.stdout = P._Tee(sys.__stdout__, _logf)
    sys.stderr = P._Tee(sys.__stderr__, _logf)
    try:
        main()
    finally:
        _logf.flush()
