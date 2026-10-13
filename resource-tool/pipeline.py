"""
pipeline.py — Unified ShelterApp audit pipeline (replaces the fractured
audit_agent.py / audit_v2_fixed.py split with one flow).

Stages:
  1. INVENTORY  — scrape existing ShelterApp listings for the area (reuses the
                  fixed JS field reader from audit_v2_fixed).
  2. AUDIT      — verify each listing against open data (resource_sources):
                  valid & open?  address match?  schedule match?
  3. GAP        — find resources that SHOULD exist in the area but are missing
                  (emergency shelter, animal shelter, library, resource center,
                  FQHC) and are not already listed.
  4. INPUT      — add the missing resources via the Add Service form
                  (auto-submit; use --dry-run to preview).
  5. REPORT     — xlsx with Audit Findings, Missing (Added), and Summary.

Usage:
  py pipeline.py --area "Page, Arizona" --radius 40            # full pipeline
  py pipeline.py --area "Page, Arizona" --radius 40 --dry-run  # don't submit adds
  py pipeline.py --area "Page, Arizona" --no-input             # audit + gap only
  py pipeline.py --area "Page, Arizona" --max 5                # cap inventory size

NOTE: Stage 4 fills the Add Service form by the same field labels the Edit form
uses. The first live run should be done with --dry-run (or --max 1) to confirm
the Add Service entry point and field mapping on your account before bulk submit.
"""
import argparse
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

import resource_sources as rs
import match_audit as ma
# reuse the verified browser helpers from the v2 script
from audit_v2_fixed import read_all_fields, get_edit_buttons

URL = "https://shelter.app/manage_services"

# open-data category -> (ShelterApp service checkbox, population categories)
CATEGORY_TO_SERVICE = {
    "emergency_shelter": ("SHELTER", ["ALL"]),
    "fqhc":              ("HEALTH",  ["ALL"]),
    "library":           ("RESOURCES", ["ALL"]),
    "animal_shelter":    ("RESOURCES", ["ALL"]),
    "resource_center":   ("RESOURCES", ["ALL"]),
}
# Add Service form: input -> placeholder text (confirmed from the live form).
# Targeting by placeholder is more robust than label proximity, and Playwright
# fill()/check() fire the events React needs (raw JS value-setting would be lost).
ADD_PLACEHOLDERS = {
    "name":     "Non-Profit Name",
    "address1": "Broadway & E Colfax Ave",
    "city":     "Denver",
    "state":    "CO",
    "zip":      "80202",
    "phone":    "(303) 555-0100",
    "website":  "https://your-nonprofit.org",
}

US_STATES = {
    "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA",
    "colorado":"CO","connecticut":"CT","delaware":"DE","florida":"FL","georgia":"GA",
    "hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA","kansas":"KS",
    "kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD","massachusetts":"MA",
    "michigan":"MI","minnesota":"MN","mississippi":"MS","missouri":"MO","montana":"MT",
    "nebraska":"NE","nevada":"NV","new hampshire":"NH","new jersey":"NJ","new mexico":"NM",
    "new york":"NY","north carolina":"NC","north dakota":"ND","ohio":"OH","oklahoma":"OK",
    "oregon":"OR","pennsylvania":"PA","rhode island":"RI","south carolina":"SC",
    "south dakota":"SD","tennessee":"TN","texas":"TX","utah":"UT","vermont":"VT",
    "virginia":"VA","washington":"WA","west virginia":"WV","wisconsin":"WI","wyoming":"WY",
    "district of columbia":"DC",
}


def to_state_abbr(s):
    s = (s or "").strip()
    if len(s) == 2:
        return s.upper()
    return US_STATES.get(s.lower(), s)


def split_address(full):
    """Split a listing address into address1/city/state/zip, reading from the END.
    'Street, Suite 100, City, ST, ZIP' keeps the suite in address1, and a street
    number is never taken as the ZIP."""
    import re
    parts = [x.strip() for x in (full or "").split(",") if x.strip()]
    out = {}
    if not parts:
        return out
    tail = parts[-1]
    m = re.match(r"^([A-Za-z]{2})\s+(\d{5})(?:-\d{4})?$", tail)   # "..., CA 90025"
    z = re.match(r"^(\d{5})(?:-\d{4})?$", tail)
    if m:
        out["zip"] = m.group(2)
        parts[-1] = m.group(1)
    elif z:
        out["zip"] = z.group(1)
        parts = parts[:-1]
    if "zip" in out and parts:
        st = to_state_abbr(parts[-1])
        if re.fullmatch(r"[A-Za-z]{2}", st):
            out["state"] = st.upper()
            parts = parts[:-1]
            if len(parts) >= 2:
                out["city"] = parts[-1]
                out["address1"] = ", ".join(parts[:-1])
            elif parts:
                out["address1"] = parts[0]
            return out
    # Not the usual shape: keep the old first-parts reading, but take the LAST
    # 5-digit number as the ZIP (the first is often the street number).
    raw = [x.strip() for x in full.split(",")]
    out["address1"] = raw[0]
    if len(raw) >= 2:
        out["city"] = raw[1]
    if len(raw) >= 3:
        m = re.match(r"([A-Za-z]{2})\b", raw[2])
        if m:
            out["state"] = m.group(1).upper()
    if "zip" not in out:
        zips = re.findall(r"\b(\d{5})(?:-\d{4})?\b", full)
        street_no = re.match(r"\s*\d{5}\b", full)
        if zips and not (len(zips) == 1 and street_no):
            out["zip"] = zips[-1]
    return out


# --------------------------------------------------------------- browser glue
def parse_card(text):
    """Parse a Manage Services card's text into listing fields. The card shows
    everything the audit needs (address, phone, website, schedule), so we never
    have to open the Edit form just to read."""
    import re
    d = {}
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    skip = {"...", "Edit Service", "Delete Service", "Manage Services",
            "This Service is waiting for Approval",
            "This Service is Approved and Visible to Users"}
    # skip "Label: value" lines (Added By:, Address:, etc.) when picking the name,
    # so a blank-name listing doesn't take "Added By: x@y.com" as its name
    label_re = re.compile(r"^(Added By|Address|Phone|Email|Website|Services|"
                          r"Service Type|Categories|Age Group|Schedule|Comment)\b\s*:", re.I)
    for l in lines:
        if l not in skip and not l.endswith(":") and not label_re.match(l):
            d["name"] = l
            break
    # [ \t]* (not \s*) so a blank field like "Email:" doesn't swallow the next line
    for f, pat in [("address_full", r"Address:[ \t]*(.+)"), ("phone", r"Phone:[ \t]*(.+)"),
                   ("email", r"Email:[ \t]*(.+)"), ("website", r"Website:[ \t]*(.+)"),
                   ("services", r"Services:[ \t]*(.+)"), ("service_details", r"Service Type:[ \t]*(.+)"),
                   ("categories", r"Categories:[ \t]*(.+)"), ("added_by", r"Added By:[ \t]*(.+)")]:
        m = re.search(pat, text, re.I)
        if m and m.group(1).strip():
            d[f] = m.group(1).strip()
    if d.get("address_full"):
        d.update(split_address(d["address_full"]))
    # schedule: lines after a "Schedule:" line
    sched = []
    grab = False
    for l in lines:
        if re.match(r"Schedule:", l, re.I):
            grab = True
            rest = l.split(":", 1)[1].strip()
            if rest:
                sched.append(rest)
            continue
        if grab:
            if re.match(r"(Comment|Added By|This Service):", l, re.I):
                break
            sched.append(l)
    if sched:
        d["schedule"] = "; ".join(sched)
    d["approval"] = "Pending" if "waiting for approval" in text.lower() else "Approved"
    return d


_GET_CARDS_JS = r"""
() => {
  const SKIP = ['...', 'Edit Service', 'Delete Service', 'Manage Services',
    'This Service is waiting for Approval', 'This Service is Approved and Visible to Users'];
  const out = [];
  // every card has an "Edit Service" control; walk up to the card container
  const btns = Array.from(document.querySelectorAll('button, [role="button"], span, div'))
    .filter(e => (e.textContent || '').trim() === 'Edit Service'
                 && e.querySelectorAll('*').length <= 1);
  const seen = new Set();
  for (const btn of btns) {
    let p = btn;
    for (let i = 0; i < 10; i++) {
      p = p.parentElement; if (!p) break;
      const t = p.innerText || '';
      if (t.includes('Address:') || t.includes('Phone:') || t.includes('Services:')) {
        const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
        let name = '';
        const LABEL = /^(Added By|Address|Phone|Email|Website|Services|Service Type|Categories|Age Group|Schedule|Comment)\s*:/i;
        for (const l of lines) { if (!SKIP.includes(l) && !LABEL.test(l)) { name = l; break; } }
        // dedupe by the WHOLE card: same-named locations are separate listings
        if (name && !seen.has(t)) { seen.add(t); out.push({ name, text: t }); }
        break;
      }
    }
  }
  return out;
}
"""


def get_cards_js(pg):
    """Fast single-call read of all visible Manage Services cards: [{name, text}].
    Replaces hundreds of slow per-element lookups (get_edit_buttons) for scanning."""
    try:
        cards = pg.evaluate(_GET_CARDS_JS)
        return cards if cards else get_edit_buttons(pg)   # fallback if JS finds none
    except Exception:
        return get_edit_buttons(pg)


def inv_key(d):
    """Unique key for one listing: name + street + ZIP. Keying by name alone
    collapsed every same-named listing (each "Salvation Army", "Public Library",
    "Community Health Center") into ONE entry, so the inventory under-counted."""
    norm = lambda v: " ".join((v or "").lower().split())
    return "|".join((norm(d.get("name")), norm(d.get("address1")), norm(d.get("zip"))))


def load_master_dict(path):
    """Read inventory_master.json into {inv_key: listing}."""
    import json as _json
    try:
        return {inv_key(d): d for d in _json.loads(Path(path).read_text(encoding="utf-8"))}
    except Exception:
        return {}


def collect_inventory(pg, list_url, master, max_new=50, recheck=False):
    """
    Stage 1 (card-based, resume-aware). Scrolls the list and reads each listing
    FROM ITS CARD — no Edit form is opened during audit. (Edit forms are only
    opened later, during apply, for listings actually being fixed.)
    New listings are added to `master` (keyed by name); set recheck=True to
    re-read all. `max_new` caps how many new listings to add this run.
    """
    print("\n[1/5] INVENTORY — reading cards (no Edit form opened; "
          f"{len(master)} already cached)...")
    seen = set()
    stable, added, stop = 0, 0, False
    for i in range(30000):               # ~20k+ listings: 6000 scrolls could stop short
        fresh = 0
        for b in get_cards_js(pg):       # fast: one JS DOM read per scroll
            nm = b["name"]
            if not b.get("text"):
                continue
            data = parse_card(b["text"])
            if not data.get("name"):
                data["name"] = nm
            k = inv_key(data)
            if k in seen:
                continue
            seen.add(k); fresh += 1
            if k in master and not recheck:
                continue
            if k not in master:
                added += 1
                print(f"      read [{len(master) + 1}]: {nm[:55]}")
            master[k] = data
            if added >= max_new and not recheck:
                stop = True; break
        if stop:
            print(f"      batch cap reached ({max_new} new this run)")
            break
        stable = stable + 1 if fresh == 0 else 0
        if stable >= 15:
            print("      reached end of list")
            break
        if i % 25 == 0 and i:
            print(f"      ...scrolling (seen {len(seen)}, {added} new)")
        try:
            pg.evaluate("window.scrollBy(0, 1000)")
            if stable and stable % 5 == 0:
                pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            pg.wait_for_timeout(1200)
        except Exception:
            break
    print(f"      master inventory now holds {len(master)} listing(s)")
    return master


def _collect_inventory_via_edit(pg, list_url, master, max_new=50, recheck=False):
    """
    Stage 1 (resume-aware). Scroll the long infinite-scroll list and open the
    Edit form ONLY for listings not already in `master` (keyed by name), so
    repeated runs advance through the list instead of redoing the same ones.
    Stops after `max_new` new reads (a batch). Mutates and returns `master`.
    Pass recheck=True to re-read everything.
    """
    print("\n[1/5] INVENTORY — discovering (skipping "
          f"{len(master)} already-read listing(s))...")
    seen, to_read = set(), []
    stable = 0
    for i in range(6000):
        fresh = 0
        for b in get_edit_buttons(pg):
            nm = b["name"]
            if nm in seen:
                continue
            seen.add(nm); fresh += 1
            if recheck or nm not in master:
                to_read.append(nm)
                print(f"      new [{len(to_read)}]: {nm[:55]}")
        if len(to_read) >= max_new:
            print(f"      batch cap reached ({max_new} new this run)")
            break
        stable = stable + 1 if fresh == 0 else 0
        if stable >= 15:
            print("      reached end of list")
            break
        if i % 25 == 0 and i:
            print(f"      ...scrolling (seen {len(seen)}, {len(to_read)} new)")
        try:
            pg.evaluate("window.scrollBy(0, 1000)")
            if stable and stable % 5 == 0:
                pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            pg.wait_for_timeout(1500)
        except Exception:
            break
    to_read = to_read[:max_new]
    print(f"      reading {len(to_read)} new listing(s); {len(master)} already cached")

    for i, name in enumerate(to_read):
        print(f"      reading [{i+1}/{len(to_read)}] {name[:50]}")
        if not open_edit_for(pg, list_url, name):
            continue
        data = read_all_fields(pg)
        if not data.get("name"):
            data["name"] = name
        master[name] = data
    print(f"      master inventory now holds {len(master)} listing(s)")
    return master


def _open_search_box(pg):
    """Return the Manage Services search input. It's a MUI <input aria-label='search'>
    that's always in the DOM (confirmed via inspect_search.py)."""
    selectors = ["input[aria-label='search']", "input[aria-label='Search']",
                 "input[placeholder='Search Service']", "input.MuiInputBase-input"]
    for sel in selectors:
        try:
            box = pg.locator(sel).first
            if box.is_visible(timeout=1000):
                return box
        except Exception:
            continue
    # if hidden, click the top-right icon button to reveal it, then retry
    try:
        pg.locator("button.MuiIconButton-root").last.click(timeout=1200)
        for sel in selectors:
            box = pg.locator(sel).first
            if box.is_visible(timeout=1000):
                return box
    except Exception:
        pass
    return None


def _distinctive(name):
    """A distinctive single token to search on (avoids punctuation-match issues)."""
    import re
    words = [w for w in re.sub(r"[^A-Za-z0-9 ]", " ", name).split() if len(w) > 3]
    return max(words, key=len) if words else name


def same_location(card_text, address1, zip_=""):
    """True if a Manage Services card is at this street address. Each location
    is its own listing, so same-named cards are told apart by address."""
    import re
    c = parse_card(card_text or "")
    norm = lambda v: re.sub(r"[^a-z0-9 ]", "", " ".join((v or "").lower().split()))
    a, b = norm(c.get("address1")), norm(address1)
    if not a or not b:
        return False
    if zip_ and (c.get("zip") or "")[:5] and (c.get("zip") or "")[:5] != zip_[:5]:
        return False                       # "100 Main St" exists in many towns
    if a == b:
        return True
    dirs ={"n", "s", "e", "w", "ne", "nw", "se", "sw", "north", "south", "east", "west"}
    num = lambda v: (re.match(r"(\d+)", v) or [None, None])[1]
    street = lambda v: [w for w in v.split() if not w.isdigit() and w not in dirs][:1]
    heading = lambda v: [w[0] for w in v.split()[1:2] if w in dirs]
    return bool(num(a) and num(a) == num(b) and street(a) and street(a) == street(b)
                and (not heading(a) or not heading(b) or heading(a) == heading(b))
                and (not zip_ or (c.get("zip") or "")[:5] == zip_[:5]))


def _click_edit_for(pg, name, address1=None, zip_=""):
    """Among currently-visible cards, click the Edit button matching name (and,
    when address1 is given, that location's address; never a guess)."""
    btns = get_edit_buttons(pg)
    if address1:
        btns = [b for b in btns if same_location(b.get("text", ""), address1, zip_)]
    for b in btns:
        if b["name"] == name:
            try:
                b["button"].scroll_into_view_if_needed(timeout=3000)
                b["button"].click(timeout=5000); pg.wait_for_timeout(2000)
                return True
            except Exception:
                return False
    # if the filtered list has exactly one result, use it
    if len(btns) == 1:
        try:
            btns[0]["button"].click(timeout=5000); pg.wait_for_timeout(2000)
            return True
        except Exception:
            return False
    return False


def open_edit_for(pg, list_url, name, address1=None, zip_=""):
    """Open a listing's Edit form. Uses the 'Search Service' box to jump straight
    to the listing (fast on the long national list); falls back to scrolling.
    Pass address1 (and zip_) to pick the right location among same-named ones."""
    try:
        pg.goto(list_url, wait_until="domcontentloaded", timeout=15000)
        pg.wait_for_timeout(1500)
    except Exception:
        return False

    # Fast path: filter via search, then click the match. Try the full name,
    # then a distinctive token (handles punctuation/case differences in names).
    box = _open_search_box(pg)
    if box:
        for query in (name, _distinctive(name)):
            try:
                box.click(timeout=2000); box.fill(""); box.fill(query)
                pg.wait_for_timeout(1800)
                if _click_edit_for(pg, name, address1, zip_):
                    return True
            except Exception:
                continue

    # Fail-fast scroll fallback (short, so a search miss SKIPs in seconds
    # rather than grinding through the whole list).
    for _ in range(12):
        if _click_edit_for(pg, name, address1, zip_):
            return True
        try:
            pg.evaluate("window.scrollBy(0, 800)"); pg.wait_for_timeout(600)
        except Exception:
            break
    return False


def _fill_placeholder(pg, placeholder, value):
    if not value:
        return
    try:
        pg.get_by_placeholder(placeholder, exact=False).first.fill(str(value), timeout=4000)
    except Exception:
        pass


def _check_label(pg, text):
    """Check a custom checkbox identified by its adjacent label text."""
    for xp in ("xpath=ancestor::label[1]", "xpath=..", "xpath=../.."):
        try:
            cb = pg.get_by_text(text, exact=True).first.locator(xp).locator(
                "input[type='checkbox']").first
            if not cb.is_checked(timeout=1500):
                cb.check(timeout=2000)
            return True
        except Exception:
            continue
    return False


def _select_country(pg, country):
    """Country is a dropdown; default 'United States' is preselected. Best-effort."""
    try:  # native <select> path
        sel = pg.get_by_text("COUNTRY", exact=True).first.locator(
            "xpath=following::select[1]")
        sel.select_option(label=country, timeout=2000)
        return
    except Exception:
        pass
    try:  # custom dropdown path: click to open, click the option
        pg.get_by_text("COUNTRY", exact=True).first.locator(
            "xpath=following::*[self::div or self::input][1]").click(timeout=2000)
        pg.get_by_text(country, exact=True).first.click(timeout=2000)
    except Exception:
        pass  # default 'United States' stands


# Add Service form: field -> label (labels confirmed from the form screenshots).
ADD_FORM_LABELS = {"name": "COMMUNITY SERVICE", "address1": "ADDRESS 1",
                   "city": "CITY", "state": "STATE", "zip": "ZIP",
                   "phone": "PHONE", "email": "EMAIL", "website": "WEBSITE"}
# Filled by placeholder (labels don't resolve cleanly for these)
SERVICE_DETAILS_PLACEHOLDER = "FOOD PANTRY"          # SERVICE DETAILS example text
DESCRIPTION_PLACEHOLDER = "DESCRIPTION IN LESS THAN"  # DESCRIPTION field (REQUIRED)

# short summary of offerings per category (free-text "Service Details" field)
CATEGORY_TO_DETAILS = {
    "emergency_shelter": "Emergency shelter, overnight beds, and basic needs",
    "fqhc": "Community health center: primary care and medical services",
    "library": "Public library: books, computers, internet, and study space",
    "animal_shelter": "Animal shelter: adoption, rescue, and pet services",
    "resource_center": "Community resource center: information and referrals",
}


_DAY_ORDER = ["mo", "tu", "we", "th", "fr", "sa", "su"]
_DAY_VALUE = {"mo": "MONDAY", "tu": "TUESDAY", "we": "WEDNESDAY", "th": "THURSDAY",
              "fr": "FRIDAY", "sa": "SATURDAY", "su": "SUNDAY"}


def _to_ampm(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    if h >= 24:
        return "11:59 PM"
    ampm = "AM" if h < 12 else "PM"
    hh = h % 12 or 12
    return f"{hh}:{m:02d} {ampm}"


def _expand_days(daypart):
    out = []
    for part in daypart.split(","):
        part = part.strip().lower()
        if "-" in part:
            a, b = (p[:2] for p in part.split("-", 1))
            if a in _DAY_ORDER and b in _DAY_ORDER:
                i, j = _DAY_ORDER.index(a), _DAY_ORDER.index(b)
                out += _DAY_ORDER[i:j + 1] if i <= j else _DAY_ORDER[i:] + _DAY_ORDER[:j + 1]
        elif part[:2] in _DAY_ORDER:
            out.append(part[:2])
    return out


def parse_opening_hours(oh):
    """OSM opening_hours -> [(DAY_VALUE, 'H:MM AM/PM' start, end)]. [] if unparseable
    (e.g. FQHC '~52 hrs/week', '24/7', or blank) -> caller falls back to contact-for-hours.
    Handles: ';'-separated rules, ', '-separated rules (comma AFTER a time, before a
    day token), split-shift time ranges within a rule ('Mo 09:00-12:00,13:00-17:00'),
    and a day-less rule ('09:00-17:00') which applies to all seven days."""
    import re
    oh = (oh or "").strip()
    if not oh or oh.startswith("~"):
        return []
    # Some OSM data separates whole rules with ", " instead of ";"
    # (e.g. "Mo-We 10:00-19:00, Th-Sa 10:00-17:00"). Promote a comma to a rule
    # break ONLY when it follows a time (digit) and precedes a day token — so
    # commas inside a daylist ("Tu,We,Fr") and between time ranges are preserved.
    oh = re.sub(r"(?<=\d),\s*(?=(?:Mo|Tu|We|Th|Fr|Sa|Su)\b)", ";", oh, flags=re.I)
    entries = []
    for rule in oh.split(";"):
        rule = rule.strip()
        ranges = re.findall(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", rule)
        if not ranges:
            continue
        first_digit = re.search(r"\d", rule)
        head = rule[:first_digit.start()].strip()  # day part before first time
        days = _expand_days(head) if head else list(_DAY_ORDER)
        if head and not days:
            continue  # unrecognized day token (e.g. "PH"/"off") -> skip, don't assume
        for d in days:
            for t1, t2 in ranges:
                entries.append((_DAY_VALUE[d], _to_ampm(t1), _to_ampm(t2)))
    return entries


def _listing_has_hours(item):
    """True if a listing's card schedule shows real day/time hours — i.e. it is not
    blank and not the 'please contact this service for hours' placeholder. Used by
    the audit to spot listings we could upgrade from contact-for-hours to real hours."""
    import re
    s = (item.get("schedule") or "").strip().lower()
    if not s:
        return False
    if "contact" in s and "hour" in s:   # "Please contact this service for hours"
        return False
    return bool(re.search(r"\d", s))      # a real schedule carries a time


def _ser_sched(entries):
    """Serialize parse_opening_hours output for proposals_hours.csv.
    [(DAY,start,end), ...] -> 'DAY~start~end | DAY~start~end' (no commas/semicolons
    so it survives a CSV cell and stays human-skimmable)."""
    return " | ".join(f"{d}~{s}~{e}" for d, s, e in entries)


def _deser_sched(s):
    """Inverse of _ser_sched: 'DAY~start~end | ...' -> [(DAY,start,end), ...]."""
    out = []
    for part in (s or "").split("|"):
        bits = [b.strip() for b in part.split("~")]
        if len(bits) == 3 and all(bits):
            out.append(tuple(bits))
    return out


_UNCHECK_LABELS_JS = r"""
(wantUpper) => {
    function labelFor(inp) {
        const lbl = inp.closest('label');
        if (lbl) return lbl.textContent.trim();
        const p = inp.parentElement;
        return (p ? p.textContent : "").trim();
    }
    const res = {};
    document.querySelectorAll("input[type='checkbox']").forEach(inp => {
        const t = labelFor(inp).toUpperCase();
        for (const w of wantUpper) {
            if (t === w) {
                if (inp.checked) inp.click();    // click drives React/MUI onChange
                res[w] = true;
            }
        }
    });
    return res;
}
"""


def uncheck_labels_js(pg, labels):
    """Un-tick custom (MUI) checkboxes by exact label text. Returns {LABEL: True} set."""
    try:
        return pg.evaluate(_UNCHECK_LABELS_JS, [l.upper() for l in labels])
    except Exception:
        return {}


def _enter_schedule(pg, entries):
    """Fill the Add form's schedule widget, one row per (day,start,end). Best-effort."""
    done = 0
    for day, start, end in entries:
        try:
            pg.locator("select[name='dayInWeek']").first.select_option(value=day, timeout=2500)
            pg.locator("input[name='startTime']").first.fill(start, timeout=2500)
            pg.locator("input[name='endTime']").first.fill(end, timeout=2500)
            pg.get_by_role("button", name="Add to schedule").first.click(timeout=2500)
            pg.wait_for_timeout(400)
            done += 1
        except Exception:
            pass
    return done


def _open_add_form(pg, list_url, add_url=None):
    """Open the Add Service form. Prefers a direct URL (avoids guessing an entry
    button); else clicks an 'Add Service' control on Manage Services. Returns
    True once the form's name field is present."""
    def form_ready():
        try:
            return (pg.get_by_placeholder("Non-Profit Name").first.is_visible(timeout=2500)
                    or pg.locator("input[aria-label*='Community' i]").first.is_visible(timeout=500))
        except Exception:
            return False

    if add_url:
        try:
            pg.goto(add_url, wait_until="domcontentloaded", timeout=20000)
            pg.wait_for_timeout(2000)
            if form_ready():
                return True
        except Exception:
            pass
    try:
        pg.goto(list_url, wait_until="domcontentloaded", timeout=15000)
        pg.wait_for_timeout(1500)
    except Exception:
        return False
    for sel in ["[aria-label='Add Service']", "button:has-text('Add Service')",
                "a:has-text('Add Service')", "text=Add Service"]:
        try:
            loc = pg.locator(sel).first
            if loc.is_visible(timeout=1200):
                loc.click(timeout=4000); pg.wait_for_timeout(2000)
                if form_ready():
                    return True
        except Exception:
            continue
    return False


def listing_status(pg, list_url, res):
    """Check the LIVE Manage Services list (whole app, via search) for this resource.
    Returns 'exists', 'absent', or 'unknown'. Strong duplicate guard: matches on
    NORMALIZED name (case/punctuation/suffix-insensitive), or a fuzzy name match
    with the same ZIP. 'unknown' means the check itself failed."""
    name = res.get("name", "")
    target_norm = ma.norm_name(name)
    try:
        pg.goto(list_url, wait_until="domcontentloaded", timeout=15000)
        pg.wait_for_timeout(1200)
        box = _open_search_box(pg)
        if not box:
            return "unknown"
        # search a distinctive token so variants surface, then scroll through ALL
        # filtered results — a generic name (e.g. "Men's Shelter") can return many,
        # and the real match may be below the fold. Checking only the first screen
        # would miss it and create a duplicate.
        box.click(timeout=2000); box.fill(""); box.fill(_distinctive(name))
        pg.wait_for_timeout(1500)
        seen, stable = set(), 0
        for _ in range(25):
            new = 0
            for b in get_cards_js(pg):
                cn = b.get("name", "")
                if cn in seen:
                    continue
                seen.add(cn); new += 1
                # raw exact (case-insensitive) — catches generic names that
                # normalize to empty, e.g. "Community Health Center"
                if cn.strip() and cn.strip().lower() == name.strip().lower():
                    return "exists"
                if target_norm and ma.norm_name(cn) == target_norm:
                    return "exists"
                if ma.name_sim(cn, name) >= 0.85:
                    cd = parse_card(b.get("text", "") or "")
                    z1, z2 = (res.get("zip") or "")[:5], (cd.get("zip") or "")[:5]
                    if not z1 or not z2 or z1 == z2:
                        return "exists"
            stable = stable + 1 if new == 0 else 0
            if stable >= 3:                       # filtered results fully scanned
                break
            try:
                pg.evaluate("window.scrollBy(0, 1100)"); pg.wait_for_timeout(500)
            except Exception:
                break
        return "absent"
    except Exception:
        return "unknown"


def add_service(pg, list_url, res, dry_run=False, add_url=None):
    """Stage 4: add one missing resource via the Add Service form. In dry_run it
    FILLS the form and pauses for inspection but never submits (safe test)."""
    svc, cats = CATEGORY_TO_SERVICE.get(res["category"], ("RESOURCES", ["ALL"]))
    try:
        # Only add when we POSITIVELY confirm the listing is absent (no duplicates).
        status = listing_status(pg, list_url, res)
        if status == "exists":
            return (res["name"], res["category"], "SKIPPED: already in ShelterApp")
        if status == "unknown":
            return (res["name"], res["category"], "SKIPPED: could not verify (safety)")
        # Ensure a contact method: ShelterApp entries must have a phone or email.
        # Free sources (esp. OSM libraries) often lack one; try Google only if a
        # key is set (paid, off by default). Still none -> skip rather than add a
        # contactless listing. Not marked handled, so it's retried later.
        if not (res.get("phone") or "").strip() and not (res.get("email") or "").strip():
            gp = rs.google_phone(res.get("name", ""), res.get("address1", ""),
                                 res.get("city", ""), res.get("state", ""))
            if gp:
                res["phone"] = gp
                print(f"    phone: sourced from Google Places ({gp})")
            else:
                return (res["name"], res["category"], "SKIPPED: no phone or email in free data")
        if not _open_add_form(pg, list_url, add_url):
            return (res["name"], res["category"], "FAILED: could not open Add Service form")

        # text fields via the proven JS setter (React-safe, case-insensitive labels)
        vals = dict(res); vals["state"] = to_state_abbr(res.get("state"))
        mapping = {lbl: vals.get(k, "") for k, lbl in ADD_FORM_LABELS.items() if vals.get(k)}
        wrote = set_fields_js(pg, mapping)
        # DESCRIPTION is REQUIRED ("Please enter Service Description"); fill by placeholder
        desc = res.get("description") or CATEGORY_TO_DETAILS.get(res["category"], "Community service")
        if set_by_placeholder_js(pg, DESCRIPTION_PLACEHOLDER, desc):
            wrote["DESCRIPTION"] = True
        _select_country(pg, res.get("country") or "United States")

        # Real hours if our data has parseable opening_hours (OSM); else try
        # Google Places (fills hours the free sources lack — esp. FQHCs); else
        # fall back to contact-for-hours.
        sched = parse_opening_hours(res.get("schedule", ""))
        if not sched:
            g = rs.google_hours(res.get("name", ""), res.get("address1", ""),
                                res.get("city", ""), res.get("state", ""))
            if g:
                sched = g
                print(f"    hours: sourced from Google Places ({len(g)} day(s))")
        # Checkboxes FIRST: ticking a Service type makes the SERVICE DETAILS field
        # appear (it is conditionally rendered — confirmed via inspect_addform.py).
        want = [svc] + list(cats)
        if not sched:
            want.append("PLEASE CONTACT THIS SERVICE FOR HOURS")
        ticked = check_labels_js(pg, want)
        pg.wait_for_timeout(900)  # let SERVICE DETAILS render
        if sched:
            n = _enter_schedule(pg, sched)
            print(f"    schedule: entered {n}/{len(sched)} day(s) from opening_hours")

        # now fill SERVICE DETAILS (by placeholder; its label doesn't resolve)
        details = res.get("service_details") or CATEGORY_TO_DETAILS.get(res["category"], "")
        if details and set_by_placeholder_js(pg, SERVICE_DETAILS_PLACEHOLDER, details):
            wrote["SERVICE DETAILS"] = True

        filled = sorted(k for k, v in wrote.items() if v)
        boxes = sorted(k for k, v in ticked.items() if v)
        print(f"    fields set: {filled}")
        print(f"    boxes ticked: {boxes}")
        if dry_run:
            # readback: which checkboxes are ACTUALLY checked right now (post all steps)?
            try:
                live = pg.evaluate("""() => Array.from(document.querySelectorAll("input[type='checkbox']"))
                    .filter(i => i.checked)
                    .map(i => { const l = i.closest('label'); return (l ? l.textContent.trim() : i.name).slice(0,40); })""")
                print(f"    >>> ACTUALLY CHECKED at submit time: {live}")
            except Exception:
                pass
            print(f"    FILLED (NOT submitted): {res['name']}")
            try:
                input("    >>> Inspect the form in the browser, then press ENTER "
                      "(will NOT submit)... ")
            except Exception:
                pass
            return (res["name"], res["category"], f"FILLED not submitted (set {len(filled)} fields)")

        # submit via JS (finds the bottom 'Add Service' button; immune to the
        # form's scroll issue), falling back to a Playwright click
        clicked = submit_add_form_js(pg)
        if not clicked:
            try:
                btn = pg.get_by_role("button", name="Add Service").last
                btn.scroll_into_view_if_needed(timeout=3000); btn.click(timeout=4000)
                clicked = True
            except Exception:
                pass
        if not clicked:
            return (res["name"], res["category"], "FILLED but could not click submit")
        pg.wait_for_timeout(2500)
        # detect required-field validation errors (submit silently blocked)
        try:
            err = pg.locator("text=/please enter|required|is required/i").first
            if err.is_visible(timeout=1500):
                return (res["name"], res["category"],
                        f"NOT submitted — validation: {err.inner_text(timeout=500)[:60]}")
        except Exception:
            pass
        return (res["name"], res["category"], "SUBMITTED")
    except Exception as e:
        return (res["name"], res["category"], f"FAILED: {e}")


# ------------------------------------------------------------------- reporting
def export_report(audit_rows, gaps, added, area, out_path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    hf = Font(bold=True, color="FFFFFF")
    hfill = PatternFill("solid", fgColor="2F5496")
    sev_fill = {"HIGH": PatternFill("solid", fgColor="FFC7CE"),
                "MEDIUM": PatternFill("solid", fgColor="FFEB9C"),
                "LOW": PatternFill("solid", fgColor="FFF2CC"),
                "UNVERIFIED": PatternFill("solid", fgColor="F4CCCC"),
                "OK": PatternFill("solid", fgColor="C6EFCE")}

    ws = wb.active; ws.title = "Audit Findings"
    hdr = ["#", "Verdict", "Name", "Address", "City", "State", "ZIP",
           "Found in open data as", "Findings (review / fix by hand)"]
    for c, h in enumerate(hdr, 1):
        cell = ws.cell(row=1, column=c, value=h); cell.font = hf; cell.fill = hfill
    for i, r in enumerate(audit_rows, 2):
        it, a = r["item"], r["audit"]
        fnd = "\n".join(f"[{s}] {m}" for s, m in a["findings"]) or "verified"
        row = [i-1, a["verdict"], it.get("name", ""), it.get("address1", ""),
               it.get("city", ""), it.get("state", ""), it.get("zip", ""),
               a.get("match") or "", fnd]
        for c, v in enumerate(row, 1):
            cell = ws.cell(row=i, column=c, value=v)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if c == 2:
                cell.fill = sev_fill.get(a["verdict"], PatternFill()); cell.font = Font(bold=True)
    for c, w in enumerate([5, 11, 30, 26, 14, 6, 8, 30, 55], 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A2"

    ws2 = wb.create_sheet("Missing (Added)")
    hdr2 = ["#", "Category", "Name", "Address", "City", "State", "ZIP",
            "Phone", "Website", "Hours", "Input Status"]
    for c, h in enumerate(hdr2, 1):
        cell = ws2.cell(row=1, column=c, value=h); cell.font = hf; cell.fill = hfill
    status_by_name = {n: st for (n, _c, st) in added}
    for i, g in enumerate(gaps, 2):
        row = [i-1, g["category"], g["name"], g["address1"], g["city"], g["state"],
               g["zip"], g["phone"], g["website"], g["schedule"],
               status_by_name.get(g["name"], "not attempted")]
        for c, v in enumerate(row, 1):
            ws2.cell(row=i, column=c, value=v).alignment = Alignment(vertical="top", wrap_text=True)
    for c, w in enumerate([5, 18, 30, 26, 14, 6, 8, 16, 26, 22, 18], 1):
        ws2.column_dimensions[get_column_letter(c)].width = w
    ws2.freeze_panes = "A2"

    ws3 = wb.create_sheet("Summary")
    vc = lambda v: sum(1 for r in audit_rows if r["audit"]["verdict"] == v)
    sub = sum(1 for (_n, _c, st) in added if st == "SUBMITTED")
    for row in [["ShelterApp Pipeline Report"],
                [f"Area (gap search): {area}"],
                [f"Generated: {datetime.now():%Y-%m-%d %H:%M}"], [""],
                ["Listings audited", len(audit_rows)],
                ["  HIGH — address unresolved or mismatch (review first)", vc("HIGH")],
                ["  MEDIUM — org found but address didn't resolve", vc("MEDIUM")],
                ["  LOW — not in open data (coverage gap; may be fine)", vc("LOW")],
                ["  OK — verified, address consistent", vc("OK")],
                ["Missing resources found", len(gaps)],
                ["  Submitted to ShelterApp", sub]]:
        ws3.append(row)
    ws3["A1"].font = Font(bold=True, size=14); ws3.column_dimensions["A"].width = 38

    wb.save(out_path)
    print(f"\n[5/5] REPORT saved: {out_path}")


# ---------------------------------------------------- phase 2: apply fixes
# Edit form labels for the fields we correct (Address + Phone, per spec).
FIX_LABELS = {"address1": "ADDRESS 1", "city": "CITY", "state": "STATE",
              "zip": "ZIP", "phone": "PHONE"}


_SET_FIELDS_JS = r"""
(want) => {
    function getLabelText(inp) {
        let t = inp.getAttribute('aria-label');
        if (t) return t.trim();
        if (inp.id) {
            const lbl = document.querySelector('label[for="' + inp.id + '"]');
            if (lbl) return lbl.textContent.trim();
        }
        const parentLbl = inp.closest('label');
        if (parentLbl) {
            return Array.from(parentLbl.childNodes)
                .filter(n => n !== inp && !n.contains(inp))
                .map(n => n.textContent.trim()).filter(Boolean).join(' ');
        }
        let el = inp;
        for (let depth = 0; depth < 4; depth++) {
            const parent = el.parentElement;
            if (!parent) break;
            const sibs = Array.from(parent.childNodes);
            const idx = sibs.indexOf(el);
            for (let i = idx - 1; i >= 0; i--) {
                const sib = sibs[i];
                if (sib.nodeType === 1) {
                    const txt = sib.textContent.trim();
                    if (txt && txt.length < 100 && !sib.querySelector('input, textarea, select'))
                        return txt;
                } else if (sib.nodeType === 3) {
                    const txt = sib.textContent.trim();
                    if (txt) return txt;
                }
            }
            el = parent;
        }
        return inp.getAttribute('placeholder') || inp.getAttribute('name') || '';
    }
    // React-controlled inputs ignore a plain value assignment; use the native
    // setter + dispatch input/change so React's state updates.
    function setVal(inp, value) {
        const proto = inp.tagName === 'TEXTAREA'
            ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        desc.set.call(inp, value);
        inp.dispatchEvent(new Event('input', { bubbles: true }));
        inp.dispatchEvent(new Event('change', { bubbles: true }));
    }
    const skip = ['hidden','submit','button','image','reset','checkbox','radio'];
    const result = {};
    const inputs = Array.from(document.querySelectorAll('input, textarea'))
        .filter(i => !skip.includes(i.type));
    // exact label match first, then substring, so distinct keys win their input
    for (const pass of ['exact', 'substr']) {
        for (const key in want) {
            if (result[key]) continue;
            for (const inp of inputs) {
                const label = getLabelText(inp).toUpperCase();
                const hit = pass === 'exact' ? label === key : label.includes(key);
                if (hit) { setVal(inp, want[key]); result[key] = true; break; }
            }
        }
    }
    return result;
}
"""


def set_fields_js(pg, mapping):
    """Set multiple Edit-form fields by label (case-insensitive, React-safe).
    mapping: {LABEL_UPPER: value}. Returns {LABEL_UPPER: True} for ones written."""
    try:
        return pg.evaluate(_SET_FIELDS_JS, {k: str(v) for k, v in mapping.items()})
    except Exception:
        return {}


_CHECK_LABELS_JS = r"""
(wantUpper) => {
    function labelFor(inp) {
        const lbl = inp.closest('label');
        if (lbl) return lbl.textContent.trim();
        const p = inp.parentElement;
        return (p ? p.textContent : "").trim();
    }
    const res = {};
    document.querySelectorAll("input[type='checkbox']").forEach(inp => {
        const t = labelFor(inp).toUpperCase();
        for (const w of wantUpper) {
            if (t === w) {                       // EXACT only (so MEN != WOMEN)
                if (!inp.checked) inp.click();   // click drives React/MUI onChange
                res[w] = true;
            }
        }
    });
    return res;
}
"""


def check_labels_js(pg, labels):
    """Tick custom (MUI) checkboxes by exact label text. Returns {LABEL: True} set."""
    try:
        return pg.evaluate(_CHECK_LABELS_JS, [l.upper() for l in labels])
    except Exception:
        return {}


_SET_BY_PLACEHOLDER_JS = r"""
([phUpper, value]) => {
    function setVal(inp, v) {
        const proto = inp.tagName === 'TEXTAREA'
            ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(inp, v);
        inp.dispatchEvent(new Event('input', { bubbles: true }));
        inp.dispatchEvent(new Event('change', { bubbles: true }));
    }
    for (const inp of document.querySelectorAll('input, textarea')) {
        const ph = (inp.getAttribute('placeholder') || '').toUpperCase();
        if (ph.includes(phUpper)) { setVal(inp, value); return true; }
    }
    return false;
}
"""


def set_by_placeholder_js(pg, placeholder_substr, value):
    """Fill a field located by a substring of its placeholder (React-safe)."""
    try:
        return bool(pg.evaluate(_SET_BY_PLACEHOLDER_JS, [placeholder_substr.upper(), str(value)]))
    except Exception:
        return False


_SUBMIT_JS = r"""
() => {
    const btns = Array.from(document.querySelectorAll('button'))
        .filter(b => (b.textContent || '').trim().toLowerCase() === 'add service');
    const btn = btns[btns.length - 1];   // bottom-most (not the app-bar title)
    if (btn) { btn.scrollIntoView(); btn.click(); return true; }
    return false;
}
"""


def submit_add_form_js(pg):
    """Click the bottom 'Add Service' submit button via JS (immune to scroll issues)."""
    try:
        return bool(pg.evaluate(_SUBMIT_JS))
    except Exception:
        return False


def read_proposals(csv_path):
    """Read a proposals CSV. Returns approved rows grouped by listing name:
       {name: [(field, old, new, source), ...]}. Only rows whose 'apply'
       column is truthy (yes/y/true/1/approved) are included."""
    import csv
    groups = {}
    truthy = {"yes", "y", "true", "1", "approved", "apply"}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("apply") or "").strip().lower() not in truthy:
                continue
            field = (r.get("field") or "").strip()
            if field not in FIX_LABELS:
                continue
            groups.setdefault(r["name"].strip(), []).append(
                (field, r.get("old_value", ""), r.get("new_value", "").strip(),
                 r.get("source_url", "")))
    return groups


def run_apply(args):
    """Phase 2: open each listing's Edit form and write approved corrections."""
    Path("audit_results").mkdir(exist_ok=True)
    groups = read_proposals(args.apply_fixes)
    total = sum(len(v) for v in groups.values())
    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"APPLY FIXES ({mode}) — {total} change(s) across {len(groups)} listing(s)")
    if not groups:
        print("Nothing approved (set the 'apply' column to yes). Exiting."); return

    results = []
    with sync_playwright() as p:
        ud = str(Path.home() / ".shelterapp-audit-browser")
        ctx = p.chromium.launch_persistent_context(
            ud, headless=False, slow_mo=80,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            pg.goto(args.url or URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"Note: {e}")
        print("\n  Log in, get to Manage Services, then press ENTER.")
        input(">>> Press ENTER after login... ")
        ap = ctx.pages; pg = ap[-1] if len(ap) > 1 else pg
        pg.wait_for_timeout(1500)
        list_url = pg.url

        for name, changes in groups.items():
            print(f"\n  {name}")
            if not open_edit_for(pg, list_url, name):
                print("    SKIP: could not open Edit form")
                for f, o, n, s in changes:
                    results.append((name, f, o, n, "SKIP: not found"))
                continue
            to_write = [(f, o, n, s) for (f, o, n, s) in changes if n]
            wrote = []
            if args.dry_run:
                for field, old, new, src in to_write:
                    print(f"    DRY RUN: {field}: '{old}' -> '{new}'")
                    results.append((name, field, old, new, "DRY RUN"))
            else:
                mapping = {FIX_LABELS[f]: n for (f, o, n, s) in to_write}
                done = set_fields_js(pg, mapping)   # one JS pass sets all fields
                for field, old, new, src in to_write:
                    ok = done.get(FIX_LABELS[field], False)
                    status = "WROTE" if ok else "WRITE FAILED"
                    print(f"    {status}: {field}: '{old}' -> '{new}'")
                    results.append((name, field, old, new, status))
                    if ok:
                        wrote.append(field)
            if wrote and not args.dry_run:
                try:
                    pg.get_by_role("button", name="Update Service").click(timeout=4000)
                    pg.wait_for_timeout(2000); print("    SAVED (Update Service)")
                except Exception:
                    try:
                        pg.locator("text=Update Service").first.click(timeout=4000)
                        pg.wait_for_timeout(2000); print("    SAVED (Update Service)")
                    except Exception:
                        print("    WARNING: could not click Update Service")
        print("\nClosing browser..."); ctx.close()

    # write a results log
    import csv
    out = Path("audit_results") / f"applied_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["name", "field", "old", "new", "status"])
        w.writerows(results)
    print(f"Results: {out}")


def read_hours_proposals(csv_path):
    """Read proposals_hours.csv -> [(name, entries)] for rows approved (apply=yes).
    entries is the [(DAY,start,end), ...] list to enter on the Edit form."""
    import csv
    truthy = {"yes", "y", "true", "1", "approved", "apply"}
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("apply") or "").strip().lower() not in truthy:
                continue
            entries = _deser_sched(r.get("schedule", ""))
            if r.get("name") and entries:
                rows.append((r["name"].strip(), entries, (r.get("address1") or "").strip(),
                             (r.get("zip") or "").strip()))
    return rows


def run_apply_hours(args):
    """Upgrade listings from contact-for-hours to real hours: for each approved row,
    open its Edit form, un-tick 'Please contact this service for hours', enter the
    schedule, and save. Reads proposals_hours.csv (from the audit)."""
    Path("audit_results").mkdir(exist_ok=True)
    rows = read_hours_proposals(args.apply_hours)
    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"APPLY HOURS ({mode}) — {len(rows)} listing(s) to upgrade")
    if not rows:
        print("Nothing approved (set the 'apply' column to yes). Exiting."); return

    results = []
    with sync_playwright() as p:
        ud = str(Path.home() / ".shelterapp-audit-browser")
        ctx = p.chromium.launch_persistent_context(
            ud, headless=False, slow_mo=80,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            pg.goto(args.url or URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"Note: {e}")
        print("\n  Log in, get to Manage Services, then press ENTER.")
        input(">>> Press ENTER after login... ")
        ap = ctx.pages; pg = ap[-1] if len(ap) > 1 else pg
        pg.wait_for_timeout(1500)
        list_url = pg.url

        for name, entries, addr, zip_ in rows:
            print(f"\n  {name}  {addr}  ({len(entries)} schedule row(s))")
            if args.dry_run:
                for d, s, e in entries:
                    print(f"    DRY RUN: {d} {s}-{e}")
                results.append((name, len(entries), 0, "DRY RUN"))
                continue
            if not open_edit_for(pg, list_url, name, addr or None, zip_):
                print("    SKIP: could not open Edit form (name + address)")
                results.append((name, len(entries), 0, "SKIP: not found"))
                continue
            uncheck_labels_js(pg, ["PLEASE CONTACT THIS SERVICE FOR HOURS"])
            pg.wait_for_timeout(500)
            n = _enter_schedule(pg, entries)
            print(f"    entered {n}/{len(entries)} schedule row(s)")
            if n:
                try:
                    pg.get_by_role("button", name="Update Service").click(timeout=4000)
                    pg.wait_for_timeout(2000); print("    SAVED (Update Service)")
                    results.append((name, len(entries), n, "SAVED"))
                except Exception:
                    try:
                        pg.locator("text=Update Service").first.click(timeout=4000)
                        pg.wait_for_timeout(2000); print("    SAVED (Update Service)")
                        results.append((name, len(entries), n, "SAVED"))
                    except Exception:
                        print("    WARNING: could not click Update Service")
                        results.append((name, len(entries), n, "WRITE OK / SAVE FAILED"))
            else:
                results.append((name, len(entries), 0, "NO ROWS ENTERED"))
        print("\nClosing browser..."); ctx.close()

    import csv
    out = Path("audit_results") / f"applied_hours_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["name", "rows_proposed", "rows_entered", "status"])
        w.writerows(results)
    print(f"Results: {out}")


# ------------------------------------------------------------------------ main
def run(args):
    Path("audit_results").mkdir(exist_ok=True)

    if args.apply_fixes:           # phase 2: write approved corrections
        run_apply(args)
        return

    if args.apply_hours:           # upgrade contact-for-hours -> real hours
        run_apply_hours(args)
        return

    if args.fqhc_state:            # statewide FQHC scan (HRSA, one query, no radius)
        st = args.fqhc_state.upper()
        print(f"Fetching ALL FQHCs in {st} from HRSA...")
        sources = rs.query_fqhc_by_state(st)
        geo = (None, None, f"{st} — all FQHCs (HRSA)")
        print(f"  {len(sources)} FQHC sites in {st}")
    else:
        print(f"Fetching open-data resources near {args.area!r} (radius {args.radius}mi)...")
        cats = [args.category] if args.category else None
        geo, sources = rs.find_resources(args.area, args.radius, cats)
        print(f"  geocoded: {geo[2]}\n  {len(sources)} open-data resources found")

    with sync_playwright() as p:
        print("\nLaunching browser...  *** DO NOT CLOSE the window ***")
        ud = str(Path.home() / ".shelterapp-audit-browser")
        ctx = p.chromium.launch_persistent_context(
            ud, headless=False, slow_mo=80,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            pg.goto(args.url or URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"Note: {e}")
        print("\n" + "=" * 55)
        print("  Log in, get to Manage Services, then press ENTER.")
        print("=" * 55)
        input("\n>>> Press ENTER after login... ")
        ap = ctx.pages; pg = ap[-1] if len(ap) > 1 else pg
        pg.wait_for_timeout(1500)
        list_url = pg.url

        # 1. inventory (resume-aware: accumulates across runs in a master file)
        import json as _json
        master_path = Path("audit_results") / "inventory_master.json"
        master = load_master_dict(master_path)
        if master and not args.recheck:
            # Re-scrolling the whole national list every run is slow; reuse the
            # cached inventory. (The per-resource duplicate guard in add_service
            # handles anything added since.) Use --recheck to force a fresh scan.
            print(f"\n[1/5] INVENTORY — using {len(master)} cached listings "
                  f"(--recheck to re-scan the live list)")
        else:
            master = collect_inventory(pg, list_url, master, max_new=args.max or 99999,
                                       recheck=args.recheck)
            master_path.write_text(_json.dumps(list(master.values()), indent=1), encoding="utf-8")
            print(f"      master saved: {master_path}")

        inventory = list(master.values())
        if args.state:
            inventory = [d for d in inventory
                         if d.get("state", "").upper() == args.state.upper()]
            print(f"      (state filter {args.state}: {len(inventory)} listing(s))")

        # Flow: SCAN (find missing) -> ADD (input missing) -> AUDIT (flag errors).
        import time as _t

        # 2. SCAN — resources that should exist in the area but are missing
        print("\n[2/5] SCAN — finding missing services in the area...")
        gaps = ma.find_gaps(inventory, sources)
        for g in gaps:
            print(f"      + [{g['category']}] {g['name'][:50]}")
        print(f"      {len(gaps)} missing resources")

        # 3. ADD — input the missing services (skips any already in ShelterApp)
        added = []
        if args.no_input or not gaps:
            print("\n[3/5] ADD — skipped" + ("" if not gaps else " (--no-input)"))
        else:
            mode = "DRY RUN" if args.dry_run else "SUBMIT"
            print(f"\n[3/5] ADD — adding {len(gaps)} missing resources ({mode})...")
            for g in gaps[: (args.add_max if args.add_max is not None else len(gaps))]:
                # quality gate: only auto-add complete records (name+address+city+state)
                if not (g.get("name") and g.get("address1") and g.get("city") and g.get("state")):
                    added.append((g["name"], g["category"], "SKIPPED: incomplete record"))
                    print(f"      SKIPPED (incomplete): {g['name'][:45]}")
                    continue
                if args.dry_run:   # bulk preview: just list, don't open/fill each form
                    added.append((g["name"], g["category"], "WOULD ADD"))
                    print(f"      WOULD ADD: {g['name'][:45]}")
                    continue
                res = add_service(pg, list_url, g, add_url=args.add_url)
                added.append(res)
                print(f"      {res[2]}: {res[0][:45]}")

        # 4. AUDIT — verify existing listings and flag errors to fix
        #    (--skip-audit does scan+add only)
        audit_rows = []
        hours_proposals = []   # listings we could upgrade contact-for-hours -> real hours
        if args.skip_audit:
            print("\n[4/5] AUDIT — skipped (--skip-audit)")
        else:
            print("\n[4/5] AUDIT — verifying each listing and flagging errors...")
            shared = ma.shared_address_map(inventory)
            if shared:
                worst = max(shared.values())
                print(f"      placeholder check: {len(shared)} shared address(es); "
                      f"one is used by {worst} listings")
            print("      (geocoding address + org name via Nominatim; cached + backoff)")
            for it in inventory:
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
                    _t.sleep(1.1)  # Nominatim politeness (cache means repeats are free)
                except Exception as e:
                    print(f"      [WARN] geocode failed for {it.get('name','')[:30]}: {e}")
                a = ma.verify_listing(it, addr_geo, org_geo, org_disp, addr_share_count=share_n)
                # HOURS UPGRADE — listing shows contact-for-hours/blank, but the
                # matched open-data source actually carries parseable day/time hours.
                if not _listing_has_hours(it):
                    src, _sc = ma.best_match(it, sources)
                    sched = parse_opening_hours(src.get("schedule", "")) if src else []
                    if sched:
                        a["findings"].append(("LOW",
                            f"HOURS AVAILABLE: source has hours '{src.get('schedule','')}' "
                            f"— listing shows contact-for-hours/blank (can upgrade)"))
                        if a["verdict"] == "OK":
                            a["verdict"] = "LOW"
                        hours_proposals.append((it.get("name", ""),
                            it.get("address1", ""), it.get("city", ""), it.get("state", ""),
                            src.get("schedule", ""), _ser_sched(sched)))
                audit_rows.append({"item": it, "audit": a})
                print(f"      [{a['verdict']:<10}] {it.get('name','')[:45]}")

        # 5. report
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = (args.area.split(",")[0].strip() if args.area
               else f"FQHC_{args.fqhc_state.upper()}")
        out = f"audit_results/pipeline_{tag.replace(' ', '_')}_{ts}.xlsx"
        export_report(audit_rows, gaps, added, geo[2], out)

        if hours_proposals:
            import csv as _csv
            hp = f"audit_results/proposals_hours.csv"
            with open(hp, "w", newline="", encoding="utf-8") as f:
                w = _csv.writer(f)
                w.writerow(["name", "address1", "city", "state",
                            "source_hours", "schedule", "apply"])
                for row in hours_proposals:
                    w.writerow(list(row) + [""])
            print(f"\n      {len(hours_proposals)} listing(s) can upgrade contact-for-hours "
                  f"-> real hours: {hp}")
            print(f"      Review, set apply=yes, then: py pipeline.py --apply-hours {hp}")

        print("\n" + "=" * 55)
        print(f"DONE — {len(gaps)} missing found, "
              f"{sum(1 for a in added if a[2]=='SUBMITTED')} added, "
              f"{len(audit_rows)} audited")
        print("Closing browser..."); ctx.close()


def _utf8_console():
    """Force stdout/stderr to UTF-8 so em-dashes / box characters don't render as
    mojibake ('�') on legacy Windows code pages. No-op where already UTF-8."""
    import sys
    for s in (sys.__stdout__, sys.__stderr__):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


class _Tee:
    """Mirror stdout/stderr to both the console and a log file."""
    def __init__(self, *streams):
        self.streams = streams
    def write(self, s):
        for st in self.streams:
            try:
                st.write(s); st.flush()
            except Exception:
                pass
    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


if __name__ == "__main__":
    pr = argparse.ArgumentParser()
    pr.add_argument("--area", default=None, help='e.g. "Page, Arizona" or a ZIP (audit/gap mode)')
    pr.add_argument("--radius", type=float, default=25, help="search radius (miles)")
    pr.add_argument("--category", choices=rs.ALL_CATEGORIES, default=None,
                    help="limit open-data lookup to one category")
    pr.add_argument("--state", default=None, help="only audit listings in this state")
    pr.add_argument("--max", type=int, default=None,
                    help="max NEW listings to read this run (batch size; default 50)")
    pr.add_argument("--recheck", action="store_true",
                    help="re-read every listing, ignoring the cached master inventory")
    pr.add_argument("--add-max", type=int, default=None, help="cap number of adds")
    pr.add_argument("--add-url", default="https://shelter.app/add_service",
                    help="direct URL of the Add Service form")
    pr.add_argument("--no-input", action="store_true", help="skip stage 4 (audit+gap only)")
    pr.add_argument("--skip-audit", action="store_true",
                    help="scan for missing services only: inventory -> gap -> add (no slow audit)")
    pr.add_argument("--dry-run", action="store_true", help="preview writes without saving")
    pr.add_argument("--apply-fixes", default=None, metavar="CSV",
                    help="phase 2: apply approved corrections from a proposals CSV")
    pr.add_argument("--apply-hours", default=None, metavar="CSV",
                    help="upgrade contact-for-hours -> real hours from proposals_hours.csv")
    pr.add_argument("--fqhc-state", default=None, metavar="ST",
                    help="scan ALL FQHCs in a state via HRSA (e.g. AZ); no radius needed")
    pr.add_argument("--url", default=None)
    args = pr.parse_args()
    if not args.area and not args.apply_fixes and not args.apply_hours and not args.fqhc_state:
        pr.error("provide --area, --fqhc-state <ST>, --apply-fixes <csv>, or --apply-hours <csv>")

    # Log everything (incl. tracebacks) so failures are never lost.
    import sys, traceback
    _utf8_console()
    Path("audit_results").mkdir(exist_ok=True)
    logpath = Path("audit_results") / "last_run.log"
    logf = open(logpath, "w", encoding="utf-8")
    logf.write(f"command: pipeline.py {' '.join(sys.argv[1:])}\n\n"); logf.flush()
    sys.stdout = _Tee(sys.__stdout__, logf)
    sys.stderr = _Tee(sys.__stderr__, logf)
    try:
        run(args)
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        print(f"\n[!] CRASHED — full details saved to {logpath}")
    finally:
        logf.flush()
        try:
            input("\nPress ENTER to close this window... ")
        except Exception:
            pass
