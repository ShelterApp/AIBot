"""
batch_fqhc.py — Add missing FQHCs across states (HRSA), piece by piece.

Resume-aware: tracks completed states in audit_results/fqhc_progress.json and
skips them, so you can run a few states at a time and stop/restart anytime.
Reuses the proven pieces: HRSA statewide query, gap analysis, and add_service
(complete-records gate + duplicate guard). One browser session for the run.

  py batch_fqhc.py --states AZ                 # one state
  py batch_fqhc.py --max-states 3              # next 3 pending states
  py batch_fqhc.py --max-states 3 --dry-run    # preview only
  py batch_fqhc.py --states CA --add-max-per-state 50   # cap adds (chunk a big state)
  py batch_fqhc.py                             # all remaining states (long!)

Writes per-state counts to audit_results/fqhc_batch_summary.csv.
"""
import json, csv, argparse, time
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright
import resource_sources as rs, match_audit as ma, pipeline as P

STATES = ["AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL",
          "IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE",
          "NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD",
          "TN","TX","UT","VT","VA","WA","WV","WI","WY"]

PROGRESS = Path("audit_results/fqhc_progress.json")
SUMMARY = Path("audit_results/fqhc_batch_summary.csv")


def load_progress():
    try:
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    except Exception:
        return {"done": []}


def save_progress(p):
    PROGRESS.parent.mkdir(exist_ok=True)
    PROGRESS.write_text(json.dumps(p, indent=1), encoding="utf-8")


def append_summary(row):
    new = not SUMMARY.exists()
    with open(SUMMARY, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "state", "fqhc_total", "missing", "added", "skipped", "failed"])
        w.writerow(row)


def is_complete(g):
    return bool(g.get("name") and g.get("address1") and g.get("city") and g.get("state"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default=None, help="comma list e.g. AZ,CA (default: all pending)")
    ap.add_argument("--max-states", type=int, default=None, help="process at most N states this session")
    ap.add_argument("--add-max-per-state", type=int, default=None, help="cap adds per state")
    ap.add_argument("--dry-run", action="store_true", help="preview; submit nothing")
    ap.add_argument("--add-url", default="https://shelter.app/add_service")
    a = ap.parse_args()

    progress = load_progress()
    done = set(progress.get("done", []))
    handled = set(progress.get("handled", []))   # names already added or confirmed-existing
    if a.states:
        targets = [s.strip().upper() for s in a.states.split(",") if s.strip()]
    else:
        targets = [s for s in STATES if s not in done]
    if a.max_states:
        targets = targets[:a.max_states]
    if not targets:
        print("Nothing to do — all requested states already done."); return
    print(f"States this session: {targets}")
    print(f"({len(done)}/{len(STATES)} states already complete)")

    try:
        master = json.loads(Path("audit_results/inventory_master.json").read_text(encoding="utf-8"))
    except Exception:
        master = []
    print(f"Cached inventory: {len(master)} listings\n")

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
        pages = ctx.pages; pg = pages[-1] if len(pages) > 1 else pg
        pg.wait_for_timeout(1500); list_url = pg.url

        for st in targets:
            print(f"\n{'='*52}\nSTATE: {st}")
            try:
                sources = rs.query_fqhc_by_state(st)
            except Exception as e:
                print(f"  HRSA query failed: {e}")
                append_summary([datetime.now().isoformat(timespec="seconds"), st, 0, 0, 0, 0, "HRSA_FAIL"])
                continue
            print(f"  {len(sources)} FQHCs in {st}; analyzing gaps "
                  f"(comparing vs {len(master)} listings)...", flush=True)
            # exclude names already handled in a prior run so chunks ADVANCE
            gaps = [g for g in ma.find_gaps(master, sources)
                    if is_complete(g) and g["name"] not in handled]
            print(f"  {len(gaps)} still to do (complete records, excluding already-handled)")
            cap = a.add_max_per_state if a.add_max_per_state is not None else len(gaps)
            added = skipped = failed = i = 0
            while i < len(gaps) and added < cap:
                g = gaps[i]; i += 1
                if a.dry_run:
                    print(f"    WOULD ADD: {g['name'][:45]}"); added += 1; continue
                status = P.add_service(pg, list_url, g, add_url=a.add_url)[2]
                if status == "SUBMITTED":
                    added += 1; handled.add(g["name"])
                elif status == "SKIPPED: already in ShelterApp":
                    skipped += 1; handled.add(g["name"])     # terminal: don't revisit (doesn't use cap)
                elif status.startswith("SKIPPED"):
                    skipped += 1                              # could-not-verify: retry next run
                else:
                    failed += 1
                print(f"    {status}: {g['name'][:42]}")
                progress["handled"] = sorted(handled)        # persist after each
                save_progress(progress)
                time.sleep(0.3)
            capped = i < len(gaps)                            # stopped before exhausting
            append_summary([datetime.now().isoformat(timespec="seconds"), st,
                            len(sources), len(gaps), added, skipped, failed])
            if not a.dry_run and not capped:
                done.add(st); progress["done"] = sorted(done)
                progress["handled"] = sorted(handled); save_progress(progress)
                print(f"  {st} COMPLETE — added {added}, skipped {skipped}, failed {failed}")
            else:
                print(f"  {st} partial ({'dry-run' if a.dry_run else 'capped'}) — re-run to continue")

        print("\nClosing browser..."); ctx.close()
    print(f"\nProgress: {len(done)}/{len(STATES)} states complete. Summary: {SUMMARY}")


if __name__ == "__main__":
    # tee all output to a log file so progress is visible / readable later
    import sys
    Path("audit_results").mkdir(exist_ok=True)
    _logf = open("audit_results/batch_fqhc.log", "w", encoding="utf-8")
    sys.stdout = P._Tee(sys.__stdout__, _logf)
    sys.stderr = P._Tee(sys.__stderr__, _logf)
    try:
        main()
    finally:
        _logf.flush()
