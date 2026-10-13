"""
run.py — ONE entry point for the whole ShelterApp process.

Your process, in three steps, with nothing to remember but this file:

  1. FIND  resources relevant to people experiencing homelessness that are
           missing from ShelterApp — COMPLETE records only (name + address +
           city + state, and hours sourced from Google Places when the free
           data lacks them).
  2. ADD   them to the site (duplicate-guarded, one login, auto-advancing
           through the plan so you never pick a state or a script).
  3. AUDIT every listing for validity and flag anything to fix.

This is a thin driver over the proven pieces (pipeline.add_service, the HRSA/OSM
sources, the matcher). It REUSES the existing progress files, so every state you
have already done still counts — nothing is redone or lost.

Modes
-----
  py run.py                     continue the plan: next chunk of find+add
  py run.py --max 100           ... stopping after 100 adds this session
  py run.py --only fqhc         ... restrict to one category
  py run.py --states CA,NV      ... restrict to certain states
  py run.py --plan              preview the next pending chunk (no browser)
  py run.py --status            where everything stands (no browser, no network)
  py run.py --audit             validate listings -> report + fix proposals
  py run.py --audit --state AZ  ... just one state
  py run.py --fill-hours        find real hours (Google) for listings missing
                                them -> proposals_hours.csv  (no browser)
  py run.py --fill-hours --states AZ,NV --max 200    ... scope / cap it
  py run.py --fill-hours-web    FREE: read hours from each listing's own website
                                (structured site data only) -> proposals_hours_web.csv
                                + broken_websites.csv (dead links to fix)
  py run.py --fill-hours-web --ai      ... also let Ollama read plain-text hours
                                (review-only: often picks the wrong section)

Apply steps that need the browser stay in pipeline.py:
  py pipeline.py --apply-hours audit_results/proposals_hours.csv   (after --fill-hours)
  py pipeline.py --apply-fixes audit_results/proposals_zip.csv     (after --audit)
"""
import json, csv, argparse, time, sys
from pathlib import Path
from datetime import datetime

import resource_sources as rs
import match_audit as ma
import pipeline as P
from batch_fqhc import STATES        # canonical 51-state (incl. DC) alphabetical list

RESULTS = Path("audit_results")
MASTER = RESULTS / "inventory_master.json"

# Categories relevant to people experiencing homelessness, in rollout priority.
# (animal_shelter is intentionally excluded — not relevant to this goal.)
PLAN = [
    {"cat": "fqhc",              "src": "fqhc", "label": "Health centers (FQHC)"},
    {"cat": "resource_center",   "src": "osm",  "label": "Resource / community centers"},
    {"cat": "emergency_shelter", "src": "osm",  "label": "Emergency shelters"},
    {"cat": "library",           "src": "osm",  "label": "Libraries"},
]
PLAN_BY_CAT = {j["cat"]: j for j in PLAN}


# --------------------------------------------------------------- progress files
def prog_path(job):
    return (RESULTS / "fqhc_progress.json") if job["src"] == "fqhc" \
        else RESULTS / f"osm_state_progress_{job['cat']}.json"


def load_prog(job):
    try:
        p = json.loads(prog_path(job).read_text(encoding="utf-8"))
    except Exception:
        p = {}
    p.setdefault("done", [])
    p.setdefault("handled", [])
    return p


def save_prog(job, p):
    RESULTS.mkdir(exist_ok=True)
    prog_path(job).write_text(json.dumps(p, indent=1), encoding="utf-8")


def append_summary(job, row):
    """Append to the SAME summary CSVs the batch scripts use, so history is one log."""
    if job["src"] == "fqhc":
        f = RESULTS / "fqhc_batch_summary.csv"
        hdr = ["timestamp", "state", "fqhc_total", "missing", "added", "skipped", "failed"]
    else:
        f = RESULTS / "osm_state_summary.csv"
        hdr = ["timestamp", "state", "category", "found", "missing", "added", "skipped", "failed"]
    new = not f.exists()
    with open(f, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(hdr)
        w.writerow(row)


def load_master():
    try:
        return json.loads(MASTER.read_text(encoding="utf-8"))
    except Exception:
        return []


def is_complete(g):
    return bool(g.get("name") and g.get("address1") and g.get("city") and g.get("state"))


def sources_for(job, state):
    if job["src"] == "fqhc":
        return rs.query_fqhc_by_state(state)
    return rs.query_osm_by_state(state, job["cat"])


def pending_gaps(job, state, master, handled):
    """Complete, not-yet-handled gaps for one (category, state)."""
    src = sources_for(job, state)
    gaps = [g for g in ma.find_gaps(master, src)
            if is_complete(g) and g["name"] not in handled]
    return src, gaps


# ------------------------------------------------------------------ status view
def _hours_bucket(s):
    import re
    s = (s or "").strip().lower()
    if not s:
        return "missing"
    if "contact" in s and "hour" in s:
        return "missing"
    return "have" if re.search(r"\d", s) else "missing"


def show_status():
    """Everything on one screen, from saved files only (no browser, no network)."""
    master = load_master()
    print("=" * 60)
    print("  SHELTERAPP — PROCESS STATUS")
    print("=" * 60)
    print(f"\nListings in ShelterApp (cached inventory): {len(master)}")

    have = sum(1 for d in master if _hours_bucket(d.get("schedule", "")) == "have")
    miss = len(master) - have
    pct = (100 * have // len(master)) if master else 0
    print(f"  with real hours : {have}  ({pct}%)")
    print(f"  missing hours   : {miss}   <- 'py run.py --fill-hours-web' proposes fixes (free)")
    nocontact = sum(1 for d in master if not _has_contact(d))
    print(f"  missing contact : {nocontact}   <- 'py run.py --fill-contacts' proposes phones")
    print(f"  Google key set  : {'yes' if rs.google_key() else 'NO (see google_places_key.txt)'}")

    print("\nFIND + ADD progress by category (states complete / 51):")
    total_done = 0
    for job in PLAN:
        p = load_prog(job)
        done = [s for s in p.get("done", []) if s in STATES]
        nxt = next((s for s in STATES if s not in done), None)
        total_done += len(done)
        bar = f"{len(done):2}/51"
        print(f"  {job['label']:<28} {bar}   handled={len(p.get('handled', [])):>5}"
              f"   next: {nxt or 'COMPLETE'}")
    print(f"\n  Overall: {total_done}/{4*51} category-states done.")
    print("\nNext action:  py run.py            (does the next chunk of find+add)")
    print("              py run.py --audit    (validate listings)")
    print("=" * 60)


# -------------------------------------------------------------------- plan view
def show_plan(args):
    """Preview the next pending (category, state) and how many it would add.
    Hits the source APIs for counts but opens no browser and writes nothing."""
    master = load_master()
    print(f"Cached inventory: {len(master)} listings")
    only = set(c.strip() for c in args.only.split(",")) if args.only else None
    want_states = [s.strip().upper() for s in args.states.split(",")] if args.states else None
    shown = 0
    for job in PLAN:
        if only and job["cat"] not in only:
            continue
        p = load_prog(job)
        done, handled = set(p["done"]), set(p["handled"])
        states = want_states or [s for s in STATES if s not in done]
        for st in states:
            try:
                src, gaps = pending_gaps(job, st, master, handled)
            except Exception as e:
                print(f"  {job['cat']:<18} {st}: source error: {e}")
                continue
            print(f"  {job['label']:<28} {st}: {len(src):4} found | "
                  f"{len(gaps):4} would add (complete, new)")
            shown += 1
            if shown >= (args.max or 5):
                print("  ... (showing first few; raise with --max)")
                return
    if not shown:
        print("Nothing pending — every category/state in scope is complete.")


# ------------------------------------------------------------------- fill hours
def fill_hours(args):
    """Browser-free: for listings missing hours, look up real hours via Google
    Places and write proposals_hours.csv. Apply with:
        py pipeline.py --apply-hours audit_results/proposals_hours.csv"""
    if not rs.google_key():
        print("No Google Places key found. Put your key in google_places_key.txt "
              "(or set GOOGLE_PLACES_API_KEY), then re-run.")
        return
    master = load_master()
    want_states = set(s.strip().upper() for s in args.states.split(",")) if args.states else None
    todo = [d for d in master
            if _hours_bucket(d.get("schedule", "")) == "missing"
            and (not want_states or (d.get("state", "").upper() in want_states))]
    cap = args.max if args.max is not None else len(todo)
    print(f"{len(todo)} listing(s) missing hours"
          f"{' in ' + ','.join(sorted(want_states)) if want_states else ''}; "
          f"looking up the first {min(cap, len(todo))} via Google Places...")

    rows, found, none_, notfound = [], 0, 0, 0
    for i, d in enumerate(todo[:cap]):
        g = rs.google_hours(d.get("name", ""), d.get("address1", ""),
                            d.get("city", ""), d.get("state", ""))
        if g:
            rows.append((d.get("name", ""), d.get("address1", ""), d.get("city", ""),
                         d.get("state", ""), "Google Places", P._ser_sched(g)))
            found += 1
            print(f"  [{i+1}/{min(cap,len(todo))}] HOURS: {d.get('name','')[:40]} "
                  f"({len(g)} day(s))")
        elif g == []:
            none_ += 1                        # found, but Google lists no hours
        else:
            notfound += 1                     # not found / lookup failed
        if (i + 1) % 25 == 0:
            print(f"      ...{i+1} checked ({found} with hours)")
        time.sleep(0.05)

    out = RESULTS / "proposals_hours.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "address1", "city", "state", "source_hours", "schedule", "apply"])
        for r in rows:
            w.writerow(list(r) + ["yes"])     # pre-approved; set to 'no' to skip a row
        print()
    print(f"Done: {found} got hours, {none_} have none on Google, {notfound} not found.")
    print(f"Proposals written (apply=yes): {out}")
    print(f"Apply them live with:\n  py pipeline.py --apply-hours {out}")


# ---------------------------------------------------------------- fill contacts
def _has_contact(d):
    def has(v):
        v = (v or "").strip()
        return bool(v) and v.lower() not in ("n/a", "none", "-")
    return has(d.get("phone")) or has(d.get("email"))


def fill_contacts(args):
    """Browser-free: for listings missing BOTH phone and email, look up a phone via
    Google Places and write proposals_phone.csv (apply=yes). Apply with:
        py pipeline.py --apply-fixes audit_results/proposals_phone.csv
    Reads the cached inventory — run `--refresh` first so it sees recent adds."""
    if not rs.google_key():
        print("No Google Places key found. Put your key in google_places_key.txt "
              "(or set GOOGLE_PLACES_API_KEY), then re-run.")
        return
    master = load_master()
    want_states = set(s.strip().upper() for s in args.states.split(",")) if args.states else None
    todo = [d for d in master
            if not _has_contact(d)
            and d.get("name") and d.get("address1") and d.get("city")
            and (not want_states or d.get("state", "").upper() in want_states)]
    cap = args.max if args.max is not None else len(todo)
    print(f"{len(todo)} listing(s) missing BOTH phone and email"
          f"{' in ' + ','.join(sorted(want_states)) if want_states else ''}; "
          f"looking up phones for the first {min(cap, len(todo))} via Google Places...")

    rows, found, none_ = [], 0, 0
    for i, d in enumerate(todo[:cap]):
        ph = rs.google_phone(d.get("name", ""), d.get("address1", ""),
                             d.get("city", ""), d.get("state", ""))
        if ph:
            rows.append((d.get("name", ""), "phone", "", ph, "Google Places"))
            found += 1
            print(f"  [{i+1}/{min(cap,len(todo))}] PHONE: {d.get('name','')[:38]} -> {ph}")
        else:
            none_ += 1
        if (i + 1) % 25 == 0:
            print(f"      ...{i+1} checked ({found} with a phone)")
        time.sleep(0.05)

    out = RESULTS / "proposals_phone.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "field", "old_value", "new_value", "source_url", "apply"])
        for r in rows:
            w.writerow(list(r) + ["yes"])
    print(f"\nDone: {found} got a phone, {none_} had none on Google.")
    print(f"Proposals written (apply=yes): {out}")
    print(f"Apply live with:\n  py pipeline.py --apply-fixes {out}")


# ------------------------------------------------------------- refresh inventory
def refresh_inventory(args):
    """Re-scrape the whole ShelterApp Manage Services list into inventory_master.json.
    Read-only — changes nothing on the site. Needed so --status / --audit /
    --fill-contacts see EVERY listing (incl. recent adds), not just the cache."""
    import json as _json
    master = P.load_master_dict(MASTER)
    print(f"Refreshing inventory (currently {len(master)} cached).")
    print("This re-scrolls the ENTIRE list and can take an hour+. Read-only; nothing")
    print("on the site is changed. Log in, press ENTER, and leave it running.\n")
    p, ctx, pg = _launch(P.URL)
    list_url = pg.url
    try:
        master = P.collect_inventory(pg, list_url, master, max_new=10 ** 9, recheck=True)
        MASTER.write_text(_json.dumps(list(master.values()), indent=1), encoding="utf-8")
        print(f"\nDone. Inventory now holds {len(master)} listings -> {MASTER}")
        miss = sum(1 for d in master.values() if not _has_contact(d))
        print(f"Missing both phone and email: {miss}  "
              f"(run 'py run.py --fill-contacts' to propose phones)")
    finally:
        if ctx:
            print("Closing browser..."); ctx.close()
        if p:
            p.stop()


# ------------------------------------------------------------------ find + add
def _launch(url):
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    ud = str(Path.home() / ".shelterapp-audit-browser")
    ctx = p.chromium.launch_persistent_context(
        ud, headless=False, slow_mo=60,
        viewport={"width": 1400, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print("note:", e)
    print("Log in, get to Manage Services, then press ENTER.  *** keep window open ***")
    input(">>> ")
    pages = ctx.pages
    pg = pages[-1] if len(pages) > 1 else pg
    pg.wait_for_timeout(1500)
    return p, ctx, pg


def find_and_add(args):
    """The core loop: walk the plan, adding the next missing resources, one login,
    auto-advancing across states and categories until the budget runs out."""
    master = load_master()
    print(f"Cached inventory: {len(master)} listings")
    print(f"Google hours source: {'ON' if rs.google_key() else 'OFF (no key yet)'}")
    budget = args.max if args.max is not None else 200
    only = set(c.strip() for c in args.only.split(",")) if args.only else None
    want_states = [s.strip().upper() for s in args.states.split(",")] if args.states else None

    # Build the work queue: (job, state) pairs still pending, in plan/alpha order.
    queue = []
    for job in PLAN:
        if only and job["cat"] not in only:
            continue
        done = set(load_prog(job)["done"])
        states = want_states or [s for s in STATES if s not in done]
        for st in states:
            queue.append((job, st))
    if not queue:
        print("Nothing pending in scope."); return
    print(f"Plan: {len(queue)} category-state(s) pending; budget {budget} adds this session.\n")

    p = ctx = pg = None
    if not args.dry_run:
        p, ctx, pg = _launch(P.URL)
        list_url = pg.url
    else:
        list_url = P.URL

    total_added = 0
    try:
        for job, st in queue:
            if total_added >= budget:
                break
            prog = load_prog(job)
            handled = set(prog["handled"])
            print(f"{'='*52}\n{job['label']}  —  {st}")
            try:
                src, gaps = pending_gaps(job, st, master, handled)
            except Exception as e:
                print(f"  source error: {e}")
                append_summary(job, _summary_row(job, st, 0, 0, 0, 0, "SRC_FAIL"))
                continue
            print(f"  {len(src)} found; {len(gaps)} to add (complete, new)")
            added = skipped = failed = i = 0
            exhausted = True
            while i < len(gaps):
                if total_added >= budget:
                    exhausted = False; break
                g = gaps[i]; i += 1
                if args.dry_run:
                    print(f"    WOULD ADD: {g['name'][:45]}")
                    added += 1; total_added += 1
                    continue
                status = P.add_service(pg, list_url, g, add_url=args.add_url)[2]
                if status == "SUBMITTED":
                    added += 1; total_added += 1; handled.add(g["name"])
                elif status == "SKIPPED: already in ShelterApp":
                    skipped += 1; handled.add(g["name"])      # terminal: never revisit
                elif status.startswith("SKIPPED"):
                    skipped += 1                               # could-not-verify: retry next run
                else:
                    failed += 1
                print(f"    {status}: {g['name'][:42]}")
                prog["handled"] = sorted(handled); save_prog(job, prog)
                time.sleep(0.3)
            append_summary(job, _summary_row(job, st, len(src), len(gaps), added, skipped, failed))
            fully = exhausted and len(src) > 0 and not args.dry_run
            if fully:
                done = set(prog["done"]); done.add(st)
                prog["done"] = sorted(done); save_prog(job, prog)
            print(f"  {st}: added {added}, skipped {skipped}, failed {failed}"
                  f"{' COMPLETE' if fully else ' (partial — re-run to continue)'}")
        print(f"\n{'='*52}\nSession done: {total_added} added this run.")
    finally:
        if ctx:
            print("Closing browser..."); ctx.close()
        if p:
            p.stop()


def _summary_row(job, st, total, missing, added, skipped, failed):
    ts = datetime.now().isoformat(timespec="seconds")
    if job["src"] == "fqhc":
        return [ts, st, total, missing, added, skipped, failed]
    return [ts, st, job["cat"], total, missing, added, skipped, failed]


# ----------------------------------------------------------------------- audit
def run_audit(args):
    """Validate listings: geocode each, verify address/org, flag placeholders and
    mismatches, and write the xlsx report. Audits the cached inventory (optionally
    one --state). This is the step the national rollout skips."""
    import time as _t
    master = load_master()
    inv = master
    if args.state:
        inv = [d for d in master if d.get("state", "").upper() == args.state.upper()]
    if not inv:
        print("No listings to audit (empty inventory / state filter).")
        return
    label = f"AUDIT — {args.state.upper()}" if args.state else "AUDIT — all states"
    print(f"[AUDIT] {len(inv)} listing(s) — {label}")
    shared = ma.shared_address_map(inv)
    if shared:
        print(f"  placeholder check: {len(shared)} shared address(es); "
              f"worst used by {max(shared.values())} listings")
    print("  geocoding address + org name via Nominatim (cached + backoff)...")

    audit_rows, hours_props = [], []
    for it in inv:
        share_n = shared.get(ma.norm_addr(it.get("address1")), 1)
        addr_str = ", ".join(x for x in [it.get("address1"), it.get("city"),
                                         it.get("state"), it.get("zip")] if x)
        addr_geo = org_geo = None
        org_disp = ""
        try:
            if addr_str:
                g = rs.geocode(addr_str)
                if g:
                    addr_geo = (g[0], g[1])
            o = rs.geocode_org(it.get("name", ""), it.get("city", ""), it.get("state", ""))
            if o:
                org_geo = (o[0], o[1]); org_disp = o[2]
            # (rate limit now lives in rs._get, so cached listings don't wait)
        except Exception as e:
            print(f"  [WARN] geocode failed for {it.get('name','')[:30]}: {e}")
        a = ma.verify_listing(it, addr_geo, org_geo, org_disp, addr_share_count=share_n)
        # flag missing hours (validity includes completeness of the required field)
        if not P._listing_has_hours(it):
            a["findings"].append(("LOW", "MISSING HOURS — no clock hours on this "
                                  "listing (run 'py run.py --fill-hours-web')"))
            if a["verdict"] == "OK":
                a["verdict"] = "LOW"
        audit_rows.append({"item": it, "audit": a})
        print(f"  [{a['verdict']:<10}] {it.get('name','')[:45]}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = (args.state.upper() if args.state else "ALL")
    out = RESULTS / f"audit_{tag}_{ts}.xlsx"
    P.export_report(audit_rows, [], [], label, str(out))
    # verdict tally
    from collections import Counter
    c = Counter(r["audit"]["verdict"] for r in audit_rows)
    print("\nVerdicts:", ", ".join(f"{k}={v}" for k, v in c.most_common()))
    print(f"Report: {out}")


# ------------------------------------------------------------------------- main
def run_selftests():
    """Fast offline checks of the core logic (no browser, no network, no key).
    Returns True if everything passes. Wired to `py run.py --selftest` and usable in CI."""
    ok = True
    print("Running self-tests (offline)...\n")
    checks = [
        ("matching / gap / verify (match_audit)", ma._selftest),
        ("Google hours converter (resource_sources)", rs._google_selftest),
        ("website hours finder (web_hours)", __import__("web_hours")._selftest),
    ]
    for label, fn in checks:
        try:
            fn()
            print(f"  [PASS] {label}")
        except Exception as e:
            ok = False
            print(f"  [FAIL] {label}: {e}")
    try:  # opening-hours parser
        r = P.parse_opening_hours("Mo-Fr 09:00-17:00")
        assert r and r[0] == ("MONDAY", "9:00 AM", "5:00 PM"), r
        assert P.parse_opening_hours("~52 hrs/week") == [], "FQHC-style hours should be empty"
        print("  [PASS] opening-hours parser (pipeline)")
    except Exception as e:
        ok = False
        print(f"  [FAIL] opening-hours parser: {e}")
    try:  # address splitter: suites stay in the street, street numbers never become ZIPs
        a = P.split_address("1520 Bellevue Ave, Suite 100, Seattle, WA, 98122")
        assert a == {"address1": "1520 Bellevue Ave, Suite 100", "city": "Seattle",
                     "state": "WA", "zip": "98122"}, a
        assert P.split_address("10604 NE 38th Pl, Kirkland, WA, 98033")["zip"] == "98033"
        assert P.split_address("11400 Santa Monica Blvd, Los Angeles, CA 90025")["state"] == "CA"
        assert P.split_address("3575 Old Keystone Rd, Tarpon Springs, Florida, 34689")["state"] == "FL"
        assert "zip" not in P.split_address("12345 Some Rd")
        print("  [PASS] address splitter (pipeline)")
    except Exception as e:
        ok = False
        print(f"  [FAIL] address splitter: {e}")
    print("\n" + ("ALL SELF-TESTS PASSED" if ok else "SELF-TESTS FAILED"))
    return ok


def main():
    ap = argparse.ArgumentParser(
        description="ShelterApp: find -> add -> audit, one entry point.",
        epilog="Run with no flags to add the next batch (needs login). "
               "See README.md and ARCHITECTURE.md.")
    ap.add_argument("--status", action="store_true", help="where everything stands (no browser)")
    ap.add_argument("--plan", action="store_true", help="preview the next pending chunk (no browser)")
    ap.add_argument("--audit", action="store_true", help="validate listings -> report")
    ap.add_argument("--fill-hours", action="store_true", dest="fill_hours",
                    help="find real hours via Google for listings missing them -> proposals")
    ap.add_argument("--fill-hours-web", action="store_true", dest="fill_hours_web",
                    help="FREE: read hours from each listing's own website -> proposals")
    ap.add_argument("--ai", action="store_true",
                    help="fill-hours-web: ALSO let Ollama read plain-text hours (review-only; "
                         "spot checks found it often grabs the wrong section, e.g. lab hours)")
    ap.add_argument("--fill-contacts", action="store_true", dest="fill_contacts",
                    help="find phones via Google for listings missing both phone+email -> proposals")
    ap.add_argument("--refresh", action="store_true",
                    help="re-scrape the full inventory (browser; read-only) so counts include recent adds")
    ap.add_argument("--selftest", action="store_true",
                    help="run offline self-tests and exit (no browser/network/key)")
    ap.add_argument("--only", default=None, help="restrict to categories, e.g. fqhc,resource_center")
    ap.add_argument("--states", default=None, help="restrict to states, e.g. CA,NV")
    ap.add_argument("--state", default=None, help="(audit) audit just this state")
    ap.add_argument("--max", type=int, default=None,
                    help="find+add: total adds this session (default 200); "
                         "fill-hours/plan: cap the number processed/shown")
    ap.add_argument("--dry-run", action="store_true", help="find+add: preview, submit nothing")
    ap.add_argument("--add-url", default="https://shelter.app/add_service",
                    help="Add Service form URL (default: shelter.app/add_service)")
    ap.add_argument("--no-pause", action="store_true",
                    help="don't wait for ENTER on exit (for scripts/CI)")
    args = ap.parse_args()

    RESULTS.mkdir(exist_ok=True)
    if args.selftest:
        args._exit_code = 0 if run_selftests() else 1
    elif args.status:
        show_status()
    elif args.plan:
        show_plan(args)
    elif args.refresh:
        refresh_inventory(args)
    elif args.fill_hours:
        fill_hours(args)
    elif args.fill_hours_web:
        import web_hours
        states = set(s.strip().upper() for s in args.states.split(",")) if args.states else None
        web_hours.run(load_master(), states, args.max, use_ai=args.ai,
                      has_hours=lambda d: _hours_bucket(d.get("schedule", "")) == "have")
    elif args.fill_contacts:
        fill_contacts(args)
    elif args.audit:
        run_audit(args)
    else:
        find_and_add(args)
    return args


def _pause(args):
    """Wait for ENTER so a double-clicked window doesn't vanish — but skip it for
    scripts/CI (non-interactive stdin) or when --no-pause is passed."""
    if args is not None and getattr(args, "no_pause", False):
        return
    try:
        if sys.stdin and sys.stdin.isatty():
            input("\nPress ENTER to close this window... ")
    except Exception:
        pass


if __name__ == "__main__":
    P._utf8_console()
    RESULTS.mkdir(exist_ok=True)
    _logf = open(RESULTS / "run.log", "w", encoding="utf-8")
    sys.stdout = P._Tee(sys.__stdout__, _logf)
    sys.stderr = P._Tee(sys.__stderr__, _logf)
    _args, _code = None, 0
    try:
        _args = main()
        _code = getattr(_args, "_exit_code", 0)
    except SystemExit:
        _logf.flush()
        raise                        # argparse --help / errors: no pause, keep its code
    except BaseException:
        import traceback
        traceback.print_exc()
        _code = 1
    finally:
        _logf.flush()
    _pause(_args)
    sys.exit(_code)
