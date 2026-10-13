"""
find_duplicates.py — Detect duplicate ShelterApp listings (same org at same
address listed more than once). Reads audit_results/inventory_master.json.

A true duplicate = same normalized name AND same normalized street address
(optionally same ZIP). Placeholder-address clusters (different orgs sharing one
fake address) are NOT flagged — only same-name+same-address collisions.

  py find_duplicates.py            # check current master
  py find_duplicates.py --csv out  # also write audit_results/duplicates.csv
"""
import json, csv, argparse, collections
import match_audit as ma


def key_of(d):
    nm = ma.norm_name(d.get("name", "")) or (d.get("name", "") or "").strip().lower()
    return (nm, ma.norm_addr(d.get("address1", "")), (d.get("zip") or "")[:5])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", default="audit_results/inventory_master.json")
    ap.add_argument("--csv", action="store_true")
    a = ap.parse_args()
    inv = json.load(open(a.inventory, encoding="utf-8"))
    print(f"Checking {len(inv)} listings for duplicates...\n")

    groups = collections.defaultdict(list)
    for d in inv:
        k = key_of(d)
        if k[0] and k[1]:                      # need a name AND an address to call it a dup
            groups[k].append(d)
    dups = {k: v for k, v in groups.items() if len(v) > 1}

    total_extra = sum(len(v) - 1 for v in dups.values())
    print(f"Duplicate groups (same name + same address): {len(dups)}")
    print(f"Redundant listings (extra copies to delete): {total_extra}\n")
    rows = []
    for (nm, addr, z), v in sorted(dups.items(), key=lambda x: -len(x[1])):
        print(f"  x{len(v)}  {v[0].get('name','')[:40]:40} | {v[0].get('address1','')[:24]} {v[0].get('city','')}, {v[0].get('state','')}")
        for d in v:
            rows.append([len(v), d.get("name", ""), d.get("address1", ""),
                         d.get("city", ""), d.get("state", ""), d.get("zip", "")])
    if not dups:
        print("  none — no same-name+same-address duplicates found.")
    if a.csv and rows:
        out = "audit_results/duplicates.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["count", "name", "address", "city", "state", "zip"])
            w.writerows(rows)
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
