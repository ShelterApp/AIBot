"""
fix_targets.py — Build the in-place-fix worklist from a saved inventory.

Selects listings that (a) have a placeholder/shared address and (b) have a
website we can source the real address/phone from. Emits a CSV worklist; the
agent then fetches each website and fills proposals.csv with verified values.

    py fix_targets.py                       # uses newest audit_results/inventory_*.json
    py fix_targets.py path/to/inventory.json
"""
import json, sys, csv, glob, os
import match_audit as ma


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    elif os.path.exists("audit_results/inventory_master.json"):
        path = "audit_results/inventory_master.json"
    else:
        files = sorted(glob.glob("audit_results/inventory_*.json"), key=os.path.getmtime)
        if not files:
            print("No audit_results/inventory_*.json found — run an audit first."); return
        path = files[-1]
    inv = json.load(open(path, encoding="utf-8"))
    shared = ma.shared_address_map(inv)

    targets = []
    for it in inv:
        share_n = shared.get(ma.norm_addr(it.get("address1")), 1)
        if share_n >= 2 and it.get("website"):   # bad address AND a source to fix from
            targets.append((it, share_n))

    out = "audit_results/fix_targets.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "website", "current_address1", "current_zip",
                    "current_phone", "shared_count"])
        for it, n in targets:
            w.writerow([it.get("name"), it.get("website"), it.get("address1"),
                        it.get("zip"), it.get("phone"), n])
    print(f"inventory: {path}")
    print(f"{len(targets)} fix target(s) with a website -> {out}")
    no_site = sum(1 for it in inv
                  if shared.get(ma.norm_addr(it.get('address1')), 1) >= 2 and not it.get("website"))
    if no_site:
        print(f"({no_site} more have a placeholder address but NO website — flag for human)")


if __name__ == "__main__":
    main()
