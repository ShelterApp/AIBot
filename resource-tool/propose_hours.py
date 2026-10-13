"""
propose_hours.py — offline generator for proposals_hours.csv (no browser, no geocoding).

Finds listings already in ShelterApp that show "contact for hours"/blank but whose
matched open-data source (statewide OSM, by category) actually carries parseable
day/time hours — i.e. ones we can upgrade to real hours. Fast: pure data + Overpass.

    py propose_hours.py --category library --states CA
    py propose_hours.py --category library --states CA,AZ,AL
    py propose_hours.py --category library              # every state present in inventory

Matching mirrors the in-pipeline audit exactly (match_audit.best_match against ALL
sources, then keep only matches whose source has parseable hours) so it never
proposes hours from a same-named library in a different city.

Output: audit_results/proposals_hours.csv (apply column blank — review, set yes,
then: py pipeline.py --apply-hours audit_results\\proposals_hours.csv)

NOTE: works off audit_results/inventory_master.json. Listings added since that
snapshot won't appear until you refresh it (a --recheck pipeline run).
"""
import json, csv, argparse
from pathlib import Path
import resource_sources as rs, match_audit as ma
from pipeline import parse_opening_hours, _listing_has_hours, _ser_sched

INV = "audit_results/inventory_master.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="library", choices=rs.ALL_CATEGORIES,
                    help="open-data category to match against (default: library)")
    ap.add_argument("--states", default=None,
                    help="comma list e.g. CA,AZ (default: all states present in inventory)")
    ap.add_argument("--inventory", default=INV)
    ap.add_argument("--out", default="audit_results/proposals_hours.csv")
    ap.add_argument("--yes", action="store_true",
                    help="pre-approve every proposal (apply=yes) so you don't have to "
                         "edit the CSV; preview with --dry-run before applying")
    a = ap.parse_args()

    if not Path(a.inventory).exists():
        print(f"No inventory at {a.inventory} — run an audit/scan first."); return
    inv = json.load(open(a.inventory, encoding="utf-8"))

    # Candidates: listings that currently lack real day/time hours.
    cands = [it for it in inv if not _listing_has_hours(it)]
    if a.states:
        states = [s.strip().upper() for s in a.states.split(",") if s.strip()]
    else:
        states = sorted({(it.get("state") or "").upper() for it in cands
                         if len((it.get("state") or "")) == 2})

    proposals = []
    for st in states:
        st_cands = [it for it in cands if (it.get("state") or "").upper() == st]
        if not st_cands:
            continue
        print(f"{st}: {len(st_cands)} listing(s) without real hours; querying OSM {a.category}...")
        try:
            sources = rs.query_osm_by_state(st, a.category)
        except Exception as e:
            print(f"   OSM query failed for {st}: {e}"); continue
        n_state = 0
        for it in st_cands:
            src, _sc = ma.best_match(it, sources)   # match among ALL sources (no cross-city)
            if not src:
                continue
            sched = parse_opening_hours(src.get("schedule", ""))
            if sched:
                proposals.append((it.get("name", ""), it.get("address1", ""),
                                  it.get("city", ""), it.get("state", ""),
                                  src.get("schedule", ""), _ser_sched(sched)))
                n_state += 1
        print(f"   -> {n_state} upgrade candidate(s)")

    apply_val = "yes" if a.yes else ""
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "address1", "city", "state", "source_hours", "schedule", "apply"])
        for p in proposals:
            w.writerow(list(p) + [apply_val])
    print(f"\n{len(proposals)} hours upgrade(s) proposed -> {a.out}")
    if a.yes:
        print("All pre-approved (apply=yes). Preview, then apply:")
        print("  py pipeline.py --apply-hours " + a.out + " --dry-run")
        print("  py pipeline.py --apply-hours " + a.out)
    else:
        print("Review, set apply=yes (or re-run with --yes to approve all), then:")
        print("  py pipeline.py --apply-hours " + a.out)


if __name__ == "__main__":
    main()
