"""
fix_zips.py — Scalable ZIP auto-fix (no website fetching).

Scans the master inventory for listings with a clearly-bad ZIP (empty, not
5 digits, or equal to the street number — a real bug in this data), geocodes
the street+city, and proposes the correct ZIP from Nominatim. Safe by design:
lookup_zip returns None unless it's confident (5-digit postcode + city match),
so we never propose a guessed value.

    py fix_zips.py                 # scans audit_results/inventory_master.json
    py fix_zips.py --max 100       # cap how many bad-zip listings to check

Output: audit_results/proposals_zip.csv (apply column blank — review, set yes,
then `py pipeline.py --apply-fixes proposals_zip.csv`).
"""
import json, csv, re, os, sys, time, argparse
import resource_sources as rs


def lead_num(addr):
    m = re.match(r"\s*(\d+)", addr or "")
    return m.group(1) if m else None


def bad_zip(z, addr1):
    z = (z or "").strip()
    if not z:
        return True
    if not re.fullmatch(r"\d{5}", z):
        return True
    if z == lead_num(addr1):           # street number landed in the ZIP field
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None, help="cap bad-zip listings checked")
    ap.add_argument("--inventory", default="audit_results/inventory_master.json")
    a = ap.parse_args()
    if not os.path.exists(a.inventory):
        print(f"No inventory at {a.inventory} — run an audit first."); return
    inv = json.load(open(a.inventory, encoding="utf-8"))

    candidates = [it for it in inv
                  if it.get("address1") and it.get("city") and bad_zip(it.get("zip"), it["address1"])]
    print(f"{len(candidates)} listing(s) have a bad ZIP and a usable street+city.")
    if a.max:
        candidates = candidates[:a.max]

    proposals, unresolved = [], 0
    for i, it in enumerate(candidates):
        addr = f"{it['address1']}, {it['city']}, {it.get('state','')}".strip(", ")
        real = rs.lookup_zip(addr, it["city"])
        time.sleep(1.1)  # Nominatim politeness (cached, so re-runs are free)
        cur = (it.get("zip") or "").strip()
        if real and real != cur:
            proposals.append((it["name"], "zip", cur, real, "geocoded (Nominatim)"))
            print(f"  FIX  {it['name'][:40]:40} {cur or '(blank)'} -> {real}")
        else:
            unresolved += 1
        if (i + 1) % 25 == 0:
            print(f"  ...checked {i+1}/{len(candidates)}")

    out = "audit_results/proposals_zip.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "field", "old_value", "new_value", "source_url", "apply"])
        for p in proposals:
            w.writerow(list(p) + [""])
    print(f"\n{len(proposals)} ZIP correction(s) proposed -> {out}")
    print(f"({unresolved} couldn't be confirmed by geocoding — left for website/human)")
    print("Review the file, set apply=yes, then: py pipeline.py --apply-fixes " + out)


if __name__ == "__main__":
    main()
