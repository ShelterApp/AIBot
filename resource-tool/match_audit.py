"""
match_audit.py — Matching, gap analysis, and verification core for the
ShelterApp audit pipeline. Pure logic (no network, no browser) so it is
testable on its own.

Inputs:
  inventory : list of dicts scraped from ShelterApp (Edit forms) — at minimum
              {name, address1, city, state, zip, schedule, ...}
  sources   : list of normalized resource dicts from resource_sources.py

Outputs:
  - match_inventory(inventory, sources) -> per-listing audit findings:
        is the org verifiable/open? does address match? does schedule match?
  - find_gaps(inventory, sources)       -> source resources NOT in inventory
                                            (the "missing" listings to add)

Run `py match_audit.py` to execute the built-in self-test.
"""
import re, difflib, math

# Common org-name noise we strip before comparing names.
_NAME_STOP = re.compile(
    r"\b(the|inc|llc|of|a|and|&|center|centre|services?|service|clinic|"
    r"healthcare|health|community|public|county|city)\b", re.I)
_NONALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def norm_name(s):
    s = (s or "").lower()
    s = _NONALNUM.sub(" ", s)
    s = _NAME_STOP.sub(" ", s)
    return _WS.sub(" ", s).strip()


def norm_addr(s):
    s = (s or "").lower()
    repl = {"street": "st", "avenue": "ave", "road": "rd", "drive": "dr",
            "boulevard": "blvd", "lane": "ln", "north": "n", "south": "s",
            "east": "e", "west": "w", "suite": "", "ste": "", "#": ""}
    s = _NONALNUM.sub(" ", s)
    toks = [repl.get(t, t) for t in s.split()]
    return _WS.sub(" ", " ".join(t for t in toks if t)).strip()


def name_sim(a, b):
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    # token overlap (Jaccard-style, /max) so ONE shared common word like "library"
    # doesn't score 1.0 against an unrelated name ("Winslow Library" vs "Community
    # Library"). Using /min previously caused mass false matches.
    ta, tb = set(a.split()), set(b.split())
    overlap = len(ta & tb) / max(1, max(len(ta), len(tb)))
    return max(ratio, overlap)


def best_match(item, sources, name_thresh=0.62):
    """Find the source row most likely to be the same org as `item`."""
    best, best_score = None, 0.0
    iz0 = (item.get("zip") or "")[:5]
    ic0 = (item.get("city") or "").strip().lower()
    for s in sources:
        score = name_sim(item.get("name"), s.get("name"))
        if score < name_thresh:
            continue
        sz = (s.get("zip") or "")[:5]
        sc = (s.get("city") or "").strip().lower()
        same_loc = (iz0 and sz and iz0 == sz) or (ic0 and sc and ic0 == sc)
        if same_loc:
            score += 0.15
        elif score < 0.90:
            # a weak name match with NO shared location isn't the same place —
            # don't treat it as a match (this is what was hiding real gaps)
            continue
        if score > best_score:
            best, best_score = s, score
    return (best, best_score) if best else (None, 0.0)


def haversine_m(lat1, lon1, lat2, lon2):
    """Distance in meters between two lat/lon points."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * r * math.asin(math.sqrt(a))


def coord_match(item, src, max_m=300):
    """True/False if both sides have coords (same place within max_m), else None."""
    try:
        ila, ilo = float(item.get("lat")), float(item.get("lon"))
        sla, slo = float(src.get("lat")), float(src.get("lon"))
    except (TypeError, ValueError):
        return None
    return haversine_m(ila, ilo, sla, slo) <= max_m


def addr_matches(item, src):
    """
    Address agreement. Prefers text comparison; falls back to coordinate
    proximity when one side lacks a street address (common for OSM records).
    Returns True / False / None(=cannot determine).
    """
    ia, sa = norm_addr(item.get("address1")), norm_addr(src.get("address1"))
    if ia and sa:
        if ia == sa:
            return True
        if difflib.SequenceMatcher(None, ia, sa).ratio() >= 0.80:
            return True
        # text disagrees — confirm with coords before calling it a mismatch
        cm = coord_match(item, src)
        return cm if cm is not None else False
    # text unavailable on one side -> rely on coordinates if we have them
    return coord_match(item, src)


def schedule_matches(item, src):
    isch, ssch = (item.get("schedule") or "").strip(), (src.get("schedule") or "").strip()
    if not isch or not ssch:
        return None
    # HRSA schedules are "~N hrs/week" — not comparable to a day/time string
    if ssch.startswith("~") or isch.startswith("~"):
        return None
    digits = lambda x: re.sub(r"[^0-9]", "", x)
    if digits(isch) and digits(isch) == digits(ssch):
        return True
    return difflib.SequenceMatcher(None, isch.lower(), ssch.lower()).ratio() >= 0.75


def audit_listing(item, sources):
    """
    Verify one existing ShelterApp listing against open data.
    Returns {match, score, findings:[(severity, msg)], verdict}.
    Scope (per spec): valid & open, address match, schedule match.
    """
    findings = []
    match, score = best_match(item, sources)
    if not match:
        findings.append(("HIGH", "UNVERIFIED: no matching open-data record "
                                 "(org may be closed, renamed, or absent from OSM/HRSA)"))
        return {"match": None, "score": 0.0, "findings": findings, "verdict": "UNVERIFIED"}

    am = addr_matches(item, match)
    if am is False:
        findings.append(("HIGH", f"ADDRESS MISMATCH: listed '{item.get('address1','')}' "
                                 f"vs source '{match.get('address1','')}'"))
    elif am is None:
        findings.append(("LOW", "ADDRESS: could not compare (missing on one side)"))

    sm = schedule_matches(item, match)
    if sm is False:
        findings.append(("MEDIUM", f"SCHEDULE MISMATCH: listed '{item.get('schedule','')}' "
                                   f"vs source '{match.get('schedule','')}'"))
    elif sm is None and match.get("schedule"):
        findings.append(("LOW", "SCHEDULE: could not compare (not in comparable format)"))

    sev = [f[0] for f in findings]
    verdict = ("HIGH" if "HIGH" in sev else "MEDIUM" if "MEDIUM" in sev
               else "LOW" if findings else "OK")
    return {"match": match, "score": round(score, 2), "findings": findings, "verdict": verdict}


def shared_address_map(inventory, min_count=2):
    """
    Map of normalized address -> count, for addresses used by >= min_count
    listings. A street address shared across multiple (especially unrelated)
    listings is the strongest no-API signal of placeholder/templated data.
    """
    from collections import Counter
    c = Counter()
    for it in inventory:
        a = norm_addr(it.get("address1"))
        if a:
            c[a] += 1
    return {a: n for a, n in c.items() if n >= min_count}


def verify_listing(listing, addr_geo, org_geo, org_display="", addr_share_count=1):
    """
    Location-aware verification of one ShelterApp listing using geocoded inputs
    (computed in the pipeline; this stays network-free/testable).

      addr_geo : (lat,lon) of the listing's typed address, or None if unresolved
      org_geo  : (lat,lon) of the organization found BY NAME in open data, or None
      org_display : display name of the org match (for the report)
      addr_share_count : how many listings share this exact address (>=3 => placeholder)

    Scope: valid & open, address match. The loudest flag is a placeholder/shared
    address (definitive, no API). Name-geocode disagreement is only a soft
    "verify" note, since multi-site orgs and generic names cause false mismatches.
    Returns {verdict, findings:[(sev,msg)], match}.
    """
    findings = []
    name = listing.get("name", "")
    addr = listing.get("address1", "")

    # 1) Placeholder / shared address — strongest signal, needs no network.
    if addr_share_count >= 3:
        findings.append(("HIGH", f"PLACEHOLDER ADDRESS: '{addr}' is shared by "
                                 f"{addr_share_count} listings — needs a real address"))
        return {"verdict": "HIGH", "findings": findings, "match": org_display or None}
    if addr_share_count == 2:
        findings.append(("MEDIUM", f"SHARED ADDRESS: '{addr}' is used by another "
                                   f"listing — verify it's correct"))

    # 2) Resolvability of address / organization.
    if addr_geo is None and org_geo is None:
        findings.append(("HIGH", "UNRESOLVED: address did not geocode and org not "
                                 "found by name — verify this listing is real"))
        return {"verdict": "HIGH", "findings": findings, "match": None}

    if org_geo and addr_geo:
        d = haversine_m(org_geo[0], org_geo[1], addr_geo[0], addr_geo[1])
        if d > 1500:
            # soft note only: chains / same-named orgs make this unreliable
            findings.append(("LOW", f"POSSIBLE address discrepancy: '{name}' also found "
                                    f"~{d/1000:.1f}km away (could be a branch or a "
                                    f"different same-named org) — spot-check"))
    elif org_geo and not addr_geo:
        findings.append(("MEDIUM", "Org found in open data, but the listed address "
                                   "did not geocode — check/fix the address"))
    elif addr_geo and not org_geo:
        findings.append(("LOW", "Address resolves, but org not found in open data "
                                "(coverage gap — may still be active; spot-check)"))

    sev = [f[0] for f in findings]
    verdict = ("HIGH" if "HIGH" in sev else "MEDIUM" if "MEDIUM" in sev
               else "LOW" if findings else "OK")
    return {"verdict": verdict, "findings": findings, "match": org_display or None}


def find_gaps(inventory, sources, name_thresh=0.62):
    """
    Source resources that have no confident match in the inventory.
    Blocked by city for speed: a source is only compared against listings in the
    same city (plus city-less listings). This is O(sources x city-bucket) instead
    of O(sources x all). Safe: a missed cross-city match just becomes a gap that
    the live per-add duplicate guard skips anyway — it can never create a dup.
    """
    from collections import defaultdict
    by_token = defaultdict(list)          # normalized-name token -> inventory items
    by_zip = defaultdict(list)            # 5-digit zip -> inventory items
    for it in inventory:
        for tok in set(norm_name(it.get("name", "")).split()):
            by_token[tok].append(it)
        z = (it.get("zip") or "")[:5]
        if z:
            by_zip[z].append(it)

    gaps = []
    for s in sources:
        seen, candidates = set(), []
        # candidates = listings sharing a name token OR the same ZIP
        for tok in set(norm_name(s.get("name", "")).split()):
            for it in by_token.get(tok, []):
                if id(it) not in seen:
                    seen.add(id(it)); candidates.append(it)
        for it in by_zip.get((s.get("zip") or "")[:5], []):
            if id(it) not in seen:
                seen.add(id(it)); candidates.append(it)
        m, _ = best_match(s, candidates, name_thresh) if candidates else (None, 0)
        if not m:
            gaps.append(s)
    return gaps


# ---------------------------------------------------------------------------
def _selftest():
    sources = [
        {"name": "Page Public Library", "address1": "479 S Lake Powell Blvd",
         "city": "Page", "state": "AZ", "zip": "86040", "schedule": "Mo 12:00-18:00",
         "category": "library"},
        {"name": "Canyonlands Healthcare - Lake Powell", "address1": "467 Vista Ave",
         "city": "Page", "state": "AZ", "zip": "86040", "schedule": "~52 hrs/week",
         "category": "fqhc"},
        {"name": "Tuba City Community Center", "address1": "", "city": "Tuba City",
         "state": "AZ", "zip": "", "schedule": "", "category": "resource_center"},
    ]
    inventory = [
        # matches library, but with a stale/wrong address -> should flag ADDRESS MISMATCH
        {"name": "Page Library", "address1": "100 Old Main St", "city": "Page",
         "state": "AZ", "zip": "86040", "schedule": "Mo 12:00-18:00"},
        # an org not present in sources -> should be UNVERIFIED
        {"name": "Ghost Shelter of Nowhere", "address1": "1 Nowhere Rd",
         "city": "Page", "state": "AZ", "zip": "86040", "schedule": ""},
    ]

    print("=== AUDIT existing listings ===")
    for it in inventory:
        r = audit_listing(it, sources)
        print(f"\n  {it['name']}  -> verdict={r['verdict']} "
              f"(matched: {r['match']['name'] if r['match'] else None}, score={r['score']})")
        for sev, msg in r["findings"]:
            print(f"      [{sev}] {msg}")

    print("\n=== GAP analysis (missing from inventory) ===")
    gaps = find_gaps(inventory, sources)
    for g in gaps:
        print(f"  + MISSING [{g['category']}] {g['name']} — "
              f"{g['address1']}, {g['city']} {g['state']}")
    print(f"\n  {len(gaps)} of {len(sources)} source resources missing from inventory")

    # assertions
    a0 = audit_listing(inventory[0], sources)
    assert any("ADDRESS MISMATCH" in m for _, m in a0["findings"]), "should catch addr mismatch"
    a1 = audit_listing(inventory[1], sources)
    assert a1["verdict"] == "UNVERIFIED", "ghost org should be unverified"
    names = {g["name"] for g in gaps}
    assert "Canyonlands Healthcare - Lake Powell" in names, "FQHC should be a gap"
    assert "Page Public Library" not in names, "library is present, not a gap"

    # coordinate-proximity address checks (the OSM-no-street-address case)
    src_geo = {"name": "X Library", "address1": "", "lat": 36.9148, "lon": -111.4573}
    near = {"name": "X Library", "address1": "", "lat": 36.9150, "lon": -111.4575}  # ~30m
    far  = {"name": "X Library", "address1": "", "lat": 37.10, "lon": -111.40}       # ~20km
    assert addr_matches(near, src_geo) is True, "near coords should match"
    assert addr_matches(far, src_geo) is False, "far coords should mismatch"
    # text disagrees but coords confirm same place -> NOT a mismatch
    t1 = {"name": "X", "address1": "100 A St", "lat": 36.9148, "lon": -111.4573}
    t2 = {"name": "X", "address1": "100 B Ave", "lat": 36.9149, "lon": -111.4574}
    assert addr_matches(t1, t2) is True, "coords should override noisy text disagreement"

    # verify_listing (location-aware) verdicts
    P = (36.9148, -111.4573)
    FAR = (40.7580, -73.9855)  # NYC
    v_ok = verify_listing({"name": "Org"}, addr_geo=P, org_geo=(36.9150, -111.4575))
    assert v_ok["verdict"] == "OK", "near org+addr should verify"
    # name-geocode disagreement is now a SOFT note, not HIGH (chains/generic names)
    v_mis = verify_listing({"name": "Org"}, addr_geo=P, org_geo=FAR)
    assert v_mis["verdict"] == "LOW", "far org-name match should be soft, not HIGH"
    assert "POSSIBLE" in v_mis["findings"][0][1]
    v_gap = verify_listing({"name": "Org"}, addr_geo=P, org_geo=None)
    assert v_gap["verdict"] == "LOW", "addr ok + no org = soft coverage gap"
    v_bad = verify_listing({"name": "Org"}, addr_geo=None, org_geo=None)
    assert v_bad["verdict"] == "HIGH", "neither resolves = suspicious"
    v_addr = verify_listing({"name": "Org"}, addr_geo=None, org_geo=P)
    assert v_addr["verdict"] == "MEDIUM", "org found but addr unresolved = check address"

    # placeholder / shared address detection (the no-API signal)
    inv = [{"address1": "2230 W 6th St"}] * 13 + [{"address1": "5 Real Rd"}]
    sm = shared_address_map(inv)
    assert sm.get(norm_addr("2230 W 6th St")) == 13, "should count the shared address"
    assert norm_addr("5 Real Rd") not in sm, "unique address not shared"
    v_ph = verify_listing({"name": "X", "address1": "2230 W 6th St"},
                          addr_geo=P, org_geo=P, addr_share_count=13)
    assert v_ph["verdict"] == "HIGH" and "PLACEHOLDER" in v_ph["findings"][0][1]
    v_sh = verify_listing({"name": "X", "address1": "9 Dup St"},
                          addr_geo=P, org_geo=(36.9150, -111.4575), addr_share_count=2)
    assert v_sh["verdict"] == "MEDIUM" and "SHARED" in v_sh["findings"][0][1]
    print("\nALL SELF-TESTS PASSED")


if __name__ == "__main__":
    _selftest()
