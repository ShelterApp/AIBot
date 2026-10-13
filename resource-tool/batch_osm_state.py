"""
batch_osm_state.py — Add missing OSM-category resources STATE BY STATE
(alphabetically), e.g. libraries. Mirrors batch_fqhc.py but pulls each state's
resources in one statewide Overpass query (rs.query_osm_by_state).

Resume-aware (osm_state_progress_<category>.json: done states + handled names;
osm_state_summary.csv). Same add_service safeguards: dedup guard, completeness
gate (name+address+city+state), real hours from opening_hours, category->service.

  py batch_osm_state.py --category library --dry-run            # preview all states
  py batch_osm_state.py --category library --max-states 5       # next 5 states
  py batch_osm_state.py --category library --states CA          # one state
  py batch_osm_state.py --category library --add-max-per-state 100   # chunk a big state
"""
import json, csv, argparse, time
from pathlib import Path
from datetime import datetime
import resource_sources as rs, match_audit as ma, pipeline as P
from batch_fqhc import STATES   # the 51-state alphabetical list


def prog_path(cat):
    return Path(f"audit_results/osm_state_progress_{cat}.json")


def is_complete(g):
    return bool(g.get("name") and g.get("address1") and g.get("city") and g.get("state"))


def append_summary(row):
    f = Path("audit_results/osm_state_summary.csv")
    new = not f.exists()
    with open(f, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["timestamp", "state", "category", "found", "missing", "added", "skipped", "failed"])
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="library", choices=list(rs.OSM_CATEGORIES.keys()))
    ap.add_argument("--states", default=None, help="comma list e.g. CA,NY (default: all pending)")
    ap.add_argument("--max-states", type=int, default=None)
    ap.add_argument("--add-max-per-state", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--add-url", default="https://shelter.app/add_service")
    a = ap.parse_args()

    pp = prog_path(a.category)
    try:
        prog = json.loads(pp.read_text(encoding="utf-8"))
    except Exception:
        prog = {"done": [], "handled": []}
    done, handled = set(prog.get("done", [])), set(prog.get("handled", []))

    targets = [s.strip().upper() for s in a.states.split(",")] if a.states else \
              [s for s in STATES if s not in done]
    if a.max_states:
        targets = targets[:a.max_states]
    if not targets:
        print("Nothing to do — all requested states already done."); return
    print(f"Category: {a.category} | states this session: {targets}")
    print(f"({len(done)}/{len(STATES)} states already complete)")

    try:
        master = json.loads(Path("audit_results/inventory_master.json").read_text(encoding="utf-8"))
    except Exception:
        master = []
    print(f"Cached inventory: {len(master)} listings\n")

    if a.dry_run:                                   # preview, no browser
        for st in targets:
            try:
                src = rs.query_osm_by_state(st, a.category)
            except Exception as e:
                print(f"  {st}: query failed: {e}"); continue
            gaps = [g for g in ma.find_gaps(master, src)
                    if is_complete(g) and g["name"] not in handled]
            print(f"  {st}: {len(src):4} found | {len(gaps):4} would add")
            time.sleep(0.3)
        return

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

        for st in targets:
            print(f"\n{'='*52}\nSTATE: {st}  ({a.category})")
            try:
                src = rs.query_osm_by_state(st, a.category)
            except Exception as e:
                print(f"  query failed: {e}")
                append_summary([datetime.now().isoformat(timespec="seconds"), st, a.category, 0, 0, 0, 0, "FAIL"])
                continue
            gaps = [g for g in ma.find_gaps(master, src)
                    if is_complete(g) and g["name"] not in handled]
            print(f"  {len(src)} found; {len(gaps)} to add (complete, not yet handled)")
            cap = a.add_max_per_state if a.add_max_per_state is not None else len(gaps)
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
            append_summary([datetime.now().isoformat(timespec="seconds"), st, a.category,
                            len(src), len(gaps), added, skipped, failed])
            fully = i >= len(gaps) and len(src) > 0   # don't mark done on a 0/failed query
            if fully:
                done.add(st); prog["done"] = sorted(done)
            prog["handled"] = sorted(handled); pp.write_text(json.dumps(prog, indent=1))
            print(f"  {st}: added {added}, skipped {skipped}, failed {failed}"
                  f"{' COMPLETE' if fully else ' (partial/retry)'}")
        print("\nClosing browser..."); ctx.close()
    print(f"\nProgress: {len(done)}/{len(STATES)} states done ({a.category}).")


if __name__ == "__main__":
    import sys
    Path("audit_results").mkdir(exist_ok=True)
    _logf = open("audit_results/batch_osm_state.log", "w", encoding="utf-8")
    sys.stdout = P._Tee(sys.__stdout__, _logf)
    sys.stderr = P._Tee(sys.__stderr__, _logf)
    try:
        main()
    finally:
        _logf.flush()
