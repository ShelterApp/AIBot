"""
resource_sources.py — Stage data layer for the ShelterApp audit pipeline.

Pulls real-world resource listings from FREE, key-less open datasets so the
pipeline can (a) verify existing ShelterApp listings and (b) find resources
that SHOULD exist in an area but are missing.

Sources:
  - OpenStreetMap via Overpass API : libraries, animal shelters, emergency
                                      shelters, resource/community centers
                                      (includes address, phone, website,
                                      opening_hours)
  - HRSA GIS portal (MapServer)     : Federally Qualified Health Center sites
  - Nominatim                       : geocode an area name -> lat/lon

All requests use urllib only (no extra deps) and send a descriptive
User-Agent, per OSM/Nominatim usage policy.

Run standalone to test:
    py resource_sources.py --area "Page, Arizona" --radius 50
    py resource_sources.py --area "Page, Arizona" --radius 25 --category library
"""
import sys, json, time, argparse, urllib.parse, urllib.request, urllib.error
from pathlib import Path

import os as _os
# Public data services (e.g. Nominatim) ask for a contact in the User-Agent.
# Set SHELTERAPP_CONTACT to your email or site; it defaults to the ShelterApp site.
UA = f"ShelterApp-Audit/1.0 ({_os.environ.get('SHELTERAPP_CONTACT', 'https://shelter.app')})"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS  = "https://overpass-api.de/api/interpreter"
HRSA_FQHC = ("https://gisportal.hrsa.gov/server/rest/services/HealthCareFacilities/"
             "PrimaryHealthCareFacilities_FS/MapServer/0/query")

MILES_TO_M = 1609.34

# OpenStreetMap selectors per category. Each entry is a list of element-type +
# tag filters; we expand node/way for each and union them in one query.
OSM_CATEGORIES = {
    "library":           [['"amenity"="library"']],
    "animal_shelter":    [['"amenity"="animal_shelter"']],
    "emergency_shelter": [['"amenity"="social_facility"', '"social_facility"="shelter"'],
                          ['"social_facility:for"="homeless"']],
    "resource_center":   [['"amenity"="community_centre"'],
                          ['"office"="charity"'],
                          ['"amenity"="social_facility"', '"social_facility"="outreach"']],
}
ALL_CATEGORIES = list(OSM_CATEGORIES.keys()) + ["fqhc"]


def _get(url, data=None, timeout=60, retries=4):
    """
    HTTP GET/POST returning parsed JSON. data=dict -> urlencoded POST body.
    Retries with backoff on transient failures (429 rate limit; 502/503/504
    gateway errors — common with Overpass; and connection timeouts) so they
    are not misread as 'not found'. Raises on final failure.
    """
    body = urllib.parse.urlencode(data).encode() if data else None
    for attempt in range(retries):
        if url.startswith(NOMINATIM):
            _nominatim_wait()
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)  # 2s, 4s, 8s
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise


_NOM_LAST = [0.0]


def _nominatim_wait():
    """Nominatim's usage policy: max 1 request/second. Throttle here, at the
    network call, so cache hits cost nothing."""
    gap = time.time() - _NOM_LAST[0]
    if gap < 1.1:
        time.sleep(1.1 - gap)
    _NOM_LAST[0] = time.time()


# --- persistent geocode cache (dedupes repeated/placeholder addresses,
#     survives across runs, and keeps us under the free endpoints' rate limits)
_CACHE_PATH = Path(__file__).resolve().parent / "audit_results" / "geocode_cache.json"
_CACHE = None


def _cache():
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _CACHE = {}
    return _CACHE


def _cache_get(key):
    c = _cache()
    return c.get(key, "__miss__")


_DIRTY = [0]


def _cache_flush():
    """Write the cache to disk (atomically, via a temp file)."""
    if _CACHE is None or not _DIRTY[0]:
        return
    try:
        _CACHE_PATH.parent.mkdir(exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_CACHE), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
        _DIRTY[0] = 0
    except Exception:
        pass


def _cache_put(key, value):
    # Batched: rewriting the multi-MB file on EVERY put made big audits crawl.
    # Flushes every 25 new entries and at exit, so at most 25 lookups are lost
    # if the process is killed.
    c = _cache()
    c[key] = value
    _DIRTY[0] += 1
    if _DIRTY[0] >= 25:
        _cache_flush()


import atexit as _atexit
_atexit.register(_cache_flush)


def geocode(area):
    """Area/address -> (lat, lon, display_name). Returns None if not found (cached)."""
    if not area:
        return None
    key = "geo:" + area.lower().strip()
    hit = _cache_get(key)
    if hit != "__miss__":
        return tuple(hit) if hit else None
    q = urllib.parse.urlencode({"q": area, "format": "json", "limit": 1})
    res = _get(f"{NOMINATIM}?{q}", timeout=30)
    out = None
    if res:
        top = res[0]
        out = [float(top["lat"]), float(top["lon"]), top.get("display_name", area)]
    _cache_put(key, out)
    return tuple(out) if out else None


def lookup_zip(address_str, expect_city=""):
    """
    Geocode a street address and return its real 5-digit ZIP from Nominatim's
    structured address. Returns None if not found or if the match's city doesn't
    contain expect_city (guards against geocoding the wrong place). Cached.
    """
    if not address_str:
        return None
    key = "zip:" + address_str.lower().strip()
    hit = _cache_get(key)
    if hit != "__miss__":
        return hit or None
    params = urllib.parse.urlencode({"q": address_str, "format": "json",
                                     "limit": 1, "addressdetails": 1})
    res = _get(f"{NOMINATIM}?{params}", timeout=30)
    out = None
    if res:
        top = res[0]
        addr = top.get("address", {})
        pc = (addr.get("postcode") or "")[:5]
        disp = top.get("display_name", "").lower()
        city_ok = (not expect_city) or expect_city.lower() in disp
        if pc and len(pc) == 5 and pc.isdigit() and city_ok:
            out = pc
    _cache_put(key, out)
    return out


# Nominatim 'class' values that mean we only matched a city/area, not the org
_GENERIC_CLASSES = {"boundary", "place"}


def geocode_org(name, city="", state="", country="USA"):
    """
    Geocode an organization BY NAME. Returns (lat, lon, display_name) only when
    Nominatim matches something specific (a building/POI), not when it falls
    back to a city/administrative area. Returns None otherwise.
    """
    if not name:
        return None
    q = ", ".join(x for x in [name, city, state, country] if x)
    key = "org:" + q.lower().strip()
    hit = _cache_get(key)
    if hit != "__miss__":
        return tuple(hit) if hit else None
    params = urllib.parse.urlencode({"q": q, "format": "json", "limit": 1,
                                     "addressdetails": 0})
    res = _get(f"{NOMINATIM}?{params}", timeout=30)
    out = None
    if res:
        top = res[0]
        disp = top.get("display_name", "")
        first = name.split(",")[0].split("/")[0].strip().lower()
        head = (first.split()[0] if first.split() else "")
        # accept only a specific match whose name contains the org's leading word
        if top.get("class") not in _GENERIC_CLASSES and (not head or head in disp.lower()):
            out = [float(top["lat"]), float(top["lon"]), disp]
    _cache_put(key, out)
    return tuple(out) if out else None


# =====================================================================
# Google Places — hours of operation source (the ONE required field the
# free datasets can't supply: HRSA has no clock hours, OSM has them for a
# minority). Optional: only used when a key is present. Keyed usage is
# capped + cached so cost stays inside Google's free monthly allowance.
# =====================================================================
GOOGLE_FIND    = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
GOOGLE_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"
GOOGLE_KEY_FILE = Path(__file__).resolve().parent / "google_places_key.txt"

# Hard per-process call cap so a runaway loop can't rack up charges. Override
# with env GOOGLE_MAX_LOOKUPS. Each lookup = 2 billable calls (find + details).
import os as _os
_G_MAX = int(_os.environ.get("GOOGLE_MAX_LOOKUPS", "5000"))
_g_used = 0
_g_warned = False
_GKEY = None


def google_key():
    """The Google Places API key, from env GOOGLE_PLACES_API_KEY or the file
    google_places_key.txt next to this script. Returns None if unset/placeholder
    (so the pipeline runs key-free, just without Google-sourced hours)."""
    global _GKEY
    if _GKEY is not None:
        return _GKEY or None
    k = _os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not k:
        try:
            # take the first non-blank, non-comment line (so leftover # comment
            # lines in the file don't get swallowed into the key). utf-8-sig
            # strips a BOM some editors prepend, which would corrupt the key.
            for line in GOOGLE_KEY_FILE.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip().lstrip("﻿").strip()
                if line and not line.startswith("#"):
                    k = line
                    break
        except Exception:
            k = ""
    # ignore the placeholder line so an un-filled file reads as "no key"
    if k.upper().startswith("PASTE") or "your-key" in k.lower():
        k = ""
    _GKEY = k
    return k or None


_G_DAY = {0: "SUNDAY", 1: "MONDAY", 2: "TUESDAY", 3: "WEDNESDAY",
          4: "THURSDAY", 5: "FRIDAY", 6: "SATURDAY"}
_G_DAY_SORT = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def _g_time(t):
    """Google 'HHMM' (e.g. '0900', '1730') -> 'H:MM AM/PM'."""
    t = (t or "").zfill(4)
    try:
        h, m = int(t[:2]), int(t[2:])
    except ValueError:
        return "12:00 AM"
    ampm = "AM" if h < 12 else "PM"
    hh = h % 12 or 12
    return f"{hh}:{m:02d} {ampm}"


def google_hours_to_schedule(opening_hours):
    """Convert a Google Places `opening_hours` dict into the pipeline's internal
    schedule format: [(DAY, 'H:MM AM/PM' start, 'H:MM AM/PM' end)], Monday-first.
    Pure/offline — network-free and unit-testable.

      - Uses the structured `.periods` (each {open:{day,time}, close:{day,time}}).
      - Google day index: 0=Sunday .. 6=Saturday.
      - True 24/7 (single open period, no close, time '0000') -> every day
        12:00 AM - 11:59 PM.
      - An open period with no close, or a close on a LATER day (overnight),
        is clamped to 11:59 PM on the open day (the widget takes one day/row).
      Returns [] when there is no usable hours data.
    """
    if not opening_hours:
        return []
    periods = opening_hours.get("periods") or []
    if (len(periods) == 1 and "close" not in periods[0]
            and (periods[0].get("open") or {}).get("time", "0000") in ("0000", "000", "00", "")):
        return [(d, "12:00 AM", "11:59 PM") for d in _G_DAY_SORT]
    out = []
    for p in periods:
        o = p.get("open") or {}
        c = p.get("close") or {}
        day = o.get("day")
        if day is None or day not in _G_DAY:
            continue
        start = _g_time(o.get("time"))
        if c and c.get("time") and c.get("day") == day:
            end = _g_time(c.get("time"))
        else:
            end = "11:59 PM"      # no close, or overnight -> clamp to end of day
        out.append((_G_DAY[day], start, end))
    out.sort(key=lambda e: _G_DAY_SORT.index(e[0]) if e[0] in _G_DAY_SORT else 9)
    return out


def google_hours(name, address="", city="", state="", key=None):
    """Look up a place's weekly hours via Google Places (Find Place -> Details).
    Returns:
      - [(DAY,start,end), ...]  when Google has hours for the place,
      - []                      when the place is found but has NO hours listed
                                (caller should use contact-for-hours),
      - None                    when the lookup couldn't run (no key, not found,
                                cap reached, or an API error) — caller falls back.
    Cached in geocode_cache.json (key 'ghours:<query>') so repeats are free."""
    global _g_used, _g_warned
    key = key or google_key()
    if not key:
        return None
    query = ", ".join(x for x in [name, address, city, state] if x).strip(", ").strip()
    if not query:
        return None
    ck = "ghours:" + query.lower()
    hit = _cache_get(ck)
    if hit != "__miss__":
        if hit == "__nohours__":
            return []
        if hit == "__notfound__":
            return None
        return [tuple(e) for e in hit]           # cached schedule
    if _g_used >= _G_MAX:
        if not _g_warned:
            print(f"      [google] cap of {_G_MAX} lookups reached this run — "
                  f"skipping further hours lookups (set GOOGLE_MAX_LOOKUPS to raise)")
            _g_warned = True
        return None
    _g_used += 1
    # 1) resolve the place_id from a text query
    try:
        q = urllib.parse.urlencode({"input": query, "inputtype": "textquery",
                                    "fields": "place_id", "key": key})
        r = _get(f"{GOOGLE_FIND}?{q}", timeout=30)
    except Exception:
        return None
    st = r.get("status")
    if st in ("REQUEST_DENIED", "OVER_QUERY_LIMIT", "INVALID_REQUEST") and not _g_warned:
        print(f"      [google] API returned {st}: {r.get('error_message','')} "
              f"— check the key is enabled for Places API + billing is on")
        _g_warned = True
        return None
    if st == "ZERO_RESULTS" or not r.get("candidates"):
        _cache_put(ck, "__notfound__")
        return None
    pid = r["candidates"][0].get("place_id")
    if not pid:
        _cache_put(ck, "__notfound__")
        return None
    # 2) fetch opening_hours for that place
    try:
        q2 = urllib.parse.urlencode({"place_id": pid, "fields": "opening_hours", "key": key})
        r2 = _get(f"{GOOGLE_DETAILS}?{q2}", timeout=30)
    except Exception:
        return None
    if r2.get("status") != "OK":
        return None
    oh = (r2.get("result") or {}).get("opening_hours")
    entries = google_hours_to_schedule(oh)
    _cache_put(ck, [list(e) for e in entries] if entries else "__nohours__")
    return entries


def google_phone(name, address="", city="", state="", key=None):
    """Look up a place's phone number via Google Places (Find Place -> Details).
    Returns the formatted phone string, or None (no key / not found / no phone /
    error). Cached in geocode_cache.json under 'gphone:<query>'. Mirrors
    google_hours() so a listing missing a contact can be given one."""
    global _g_used, _g_warned
    key = key or google_key()
    if not key:
        return None
    query = ", ".join(x for x in [name, address, city, state] if x).strip(", ").strip()
    if not query:
        return None
    ck = "gphone:" + query.lower()
    hit = _cache_get(ck)
    if hit != "__miss__":
        return None if hit in ("__nophone__", "__notfound__") else hit
    if _g_used >= _G_MAX:
        return None
    _g_used += 1
    try:
        q = urllib.parse.urlencode({"input": query, "inputtype": "textquery",
                                    "fields": "place_id", "key": key})
        r = _get(f"{GOOGLE_FIND}?{q}", timeout=30)
    except Exception:
        return None
    st = r.get("status")
    if st in ("REQUEST_DENIED", "OVER_QUERY_LIMIT", "INVALID_REQUEST"):
        if not _g_warned:
            print(f"      [google] API returned {st}: {r.get('error_message','')}")
            _g_warned = True
        return None
    if st == "ZERO_RESULTS" or not r.get("candidates"):
        _cache_put(ck, "__notfound__")
        return None
    pid = r["candidates"][0].get("place_id")
    if not pid:
        _cache_put(ck, "__notfound__")
        return None
    try:
        q2 = urllib.parse.urlencode(
            {"place_id": pid, "fields": "formatted_phone_number", "key": key})
        r2 = _get(f"{GOOGLE_DETAILS}?{q2}", timeout=30)
    except Exception:
        return None
    if r2.get("status") != "OK":
        return None
    phone = (r2.get("result") or {}).get("formatted_phone_number", "").strip()
    _cache_put(ck, phone if phone else "__nophone__")
    return phone or None


def _google_selftest():
    """Offline checks for the Google hours converter (no network/key needed)."""
    # standard weekday hours (Google day 1=Mon)
    oh = {"periods": [
        {"open": {"day": 1, "time": "0900"}, "close": {"day": 1, "time": "1700"}},
        {"open": {"day": 2, "time": "0900"}, "close": {"day": 2, "time": "1730"}},
    ]}
    r = google_hours_to_schedule(oh)
    assert r[0] == ("MONDAY", "9:00 AM", "5:00 PM"), r
    assert r[1] == ("TUESDAY", "9:00 AM", "5:30 PM"), r
    # 24/7
    r = google_hours_to_schedule({"periods": [{"open": {"day": 0, "time": "0000"}}]})
    assert len(r) == 7 and r[0] == ("MONDAY", "12:00 AM", "11:59 PM"), r
    # overnight close (Fri 20:00 -> Sat 02:00) clamps to end of Friday
    r = google_hours_to_schedule({"periods": [
        {"open": {"day": 5, "time": "2000"}, "close": {"day": 6, "time": "0200"}}]})
    assert r == [("FRIDAY", "8:00 PM", "11:59 PM")], r
    # empty / missing
    assert google_hours_to_schedule({}) == []
    assert google_hours_to_schedule({"periods": []}) == []
    # Monday-first ordering even if input is unordered
    oh = {"periods": [
        {"open": {"day": 6, "time": "1000"}, "close": {"day": 6, "time": "1400"}},
        {"open": {"day": 1, "time": "0800"}, "close": {"day": 1, "time": "1200"}}]}
    r = google_hours_to_schedule(oh)
    assert [e[0] for e in r] == ["MONDAY", "SATURDAY"], r
    print("google hours converter self-test: OK")


def _osm_addr(t):
    """Assemble a street address1 from OSM addr:* tags."""
    num, street = t.get("addr:housenumber", ""), t.get("addr:street", "")
    return (f"{num} {street}").strip() or t.get("addr:full", "")


def _osm_selectors(category):
    """Build the node/way tag-filter fragments for a category (without the area)."""
    parts = []
    for filt in OSM_CATEGORIES[category]:
        tagstr = "".join("[" + f + "]" for f in filt)
        parts.append((tagstr,))
    return parts


def _parse_osm(elements, category):
    out = []
    for e in elements:
        t = e.get("tags", {})
        name = t.get("name")
        if not name:
            continue  # unnamed nodes are not actionable listings
        c = e.get("center", {})
        out.append({
            "name": name, "category": category,
            "address1": _osm_addr(t), "city": t.get("addr:city", ""),
            "state": t.get("addr:state", ""), "zip": t.get("addr:postcode", ""),
            "phone": t.get("phone") or t.get("contact:phone", ""),
            "email": t.get("email") or t.get("contact:email", ""),
            "website": t.get("website") or t.get("contact:website", ""),
            "schedule": t.get("opening_hours", ""),
            "lat": e.get("lat", c.get("lat")), "lon": e.get("lon", c.get("lon")),
            "source": "OSM",
        })
    return out


def query_osm(lat, lon, radius_mi, category):
    """Query one OSM category around a point. Returns list of normalized dicts."""
    radius_m = int(radius_mi * MILES_TO_M)
    parts = []
    for (tagstr,) in _osm_selectors(category):
        for etype in ("node", "way"):
            parts.append(f'{etype}{tagstr}(around:{radius_m},{lat},{lon});')
    query = f"[out:json][timeout:50];({''.join(parts)});out center tags;"
    return _parse_osm(_get(OVERPASS, data={"data": query}, timeout=70).get("elements", []), category)


def query_osm_by_state(state_abbr, category):
    """Query an OSM category across a WHOLE state (via ISO3166-2 boundary) — one
    query, no city-stitching. Lets OSM categories roll out state-by-state."""
    st = (state_abbr or "").upper()
    parts = []
    for (tagstr,) in _osm_selectors(category):
        for etype in ("node", "way"):
            parts.append(f"{etype}{tagstr}(area.a);")
    query = (f'[out:json][timeout:180];area["ISO3166-2"="US-{st}"]->.a;'
             f'({"".join(parts)});out center tags;')
    return _parse_osm(_get(OVERPASS, data={"data": query}, timeout=200).get("elements", []), category)


_FQHC_FIELDS = ("SITE_NM,SITE_ADDRESS,SITE_CITY,SITE_STATE_ABBR,SITE_ZIP_CD,"
                "SITE_PHONE_NUM,SITE_URL,TOT_OPER_HR_PER_WEEK")


def _parse_fqhc(features):
    out = []
    for f in features:
        a = f.get("attributes", {})
        name = a.get("SITE_NM")
        if not name:
            continue
        g = f.get("geometry") or {}
        hrs = a.get("TOT_OPER_HR_PER_WEEK")
        out.append({
            "name": name.strip(), "category": "fqhc",
            "address1": (a.get("SITE_ADDRESS") or "").strip(),
            "city": (a.get("SITE_CITY") or "").strip(),
            "state": (a.get("SITE_STATE_ABBR") or "").strip(),
            "zip": (a.get("SITE_ZIP_CD") or "").strip()[:5],
            "phone": (a.get("SITE_PHONE_NUM") or "").strip(),
            "website": (a.get("SITE_URL") or "").strip(),
            "schedule": f"~{hrs} hrs/week" if hrs else "",
            "lat": g.get("y"), "lon": g.get("x"), "source": "HRSA",
        })
    return out


def query_fqhc(lat, lon, radius_mi):
    """Query HRSA FQHC sites within radius (statute miles) of a point."""
    params = {
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
        "distance": str(radius_mi), "units": "esriSRUnit_StatuteMile",
        "spatialRel": "esriSpatialRelIntersects", "outFields": _FQHC_FIELDS,
        "returnGeometry": "true", "outSR": "4326", "f": "json",
    }
    return _parse_fqhc(_get(HRSA_FQHC, data=params, timeout=60).get("features", []))


def query_fqhc_by_state(state):
    """Query ALL HRSA FQHC sites in a state (e.g. 'AZ') — one query, no radius.
    Pages through results in case the state has more than the server's max."""
    state = (state or "").upper()
    out, offset = [], 0
    while True:
        params = {
            "where": f"SITE_STATE_ABBR='{state}'", "outFields": _FQHC_FIELDS,
            "returnGeometry": "true", "outSR": "4326", "f": "json",
            "resultOffset": str(offset), "resultRecordCount": "1000",
            "orderByFields": "SITE_NM",
        }
        data = _get(HRSA_FQHC, data=params, timeout=90)
        feats = data.get("features", [])
        out += _parse_fqhc(feats)
        if len(feats) < 1000 or not data.get("exceededTransferLimit"):
            break
        offset += 1000
        time.sleep(0.5)
    return out


def find_resources(area, radius_mi=25, categories=None, polite_delay=1.0):
    """
    Find all resources of the given categories near `area`.
    Returns (geo, results) where geo=(lat,lon,display_name) and results is a
    list of normalized resource dicts. Raises ValueError if area can't geocode.
    """
    categories = categories or ALL_CATEGORIES
    geo = geocode(area)
    if not geo:
        raise ValueError(f"Could not geocode area: {area!r}")
    lat, lon, _ = geo
    results = []
    for cat in categories:
        try:
            if cat == "fqhc":
                results += query_fqhc(lat, lon, radius_mi)
            else:
                results += query_osm(lat, lon, radius_mi, cat)
        except Exception as e:
            print(f"  [WARN] {cat} query failed: {e}", file=sys.stderr)
        time.sleep(polite_delay)  # be polite to free endpoints
    return geo, results


def _main():
    p = argparse.ArgumentParser()
    p.add_argument("--area", required=True, help='e.g. "Page, Arizona" or a ZIP')
    p.add_argument("--radius", type=float, default=25, help="search radius in miles")
    p.add_argument("--category", choices=ALL_CATEGORIES, default=None,
                   help="limit to one category (default: all)")
    a = p.parse_args()
    cats = [a.category] if a.category else None
    geo, results = find_resources(a.area, a.radius, cats)
    print(f"Area: {geo[2]}\n  ({geo[0]}, {geo[1]})  radius={a.radius}mi\n")
    by_cat = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)
    for cat in (cats or ALL_CATEGORIES):
        rows = by_cat.get(cat, [])
        print(f"== {cat.upper()} ({len(rows)}) ==")
        for r in rows:
            loc = ", ".join(x for x in [r["address1"], r["city"], r["state"], r["zip"]] if x)
            print(f"  - {r['name']}")
            print(f"      {loc or '(no address)'}")
            if r["phone"] or r["website"]:
                print(f"      {r['phone']}  {r['website']}")
            if r["schedule"]:
                print(f"      hours: {r['schedule']}")
        print()
    print(f"TOTAL: {len(results)} resources from {len(by_cat)} categories")


if __name__ == "__main__":
    _main()
