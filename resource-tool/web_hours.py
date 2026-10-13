"""
Free hours finder: read each listing's OWN website for its opening hours.
No paid APIs, no keys. Two layers:

  1. Structured data the site publishes for search engines (schema.org
     openingHours / openingHoursSpecification in JSON-LD or microdata).
     Reliable -> proposals marked apply=yes.
  2. Plain-text hours ("Mon-Fri 9am-5pm") read by the local Ollama model.
     Small models can invent things, so every day and time it returns must
     literally appear in the page text or the whole result is thrown out.
     Even then these rows are marked apply=check for a human spot-check.
     OFF by default: a 2026-10-05 spot check found most AI-read rows came from
     the wrong section of the page (lab hours, phone-line hours). The page
     check stops invented numbers but cannot tell which service they belong to.

Output: audit_results/proposals_hours_web.csv, same columns as
proposals_hours.csv, so the existing apply step works:
    py pipeline.py --apply-hours audit_results/proposals_hours_web.csv

Polite by design: honours robots.txt, one site at a time, ~1s between
requests, at most 2 pages per site, results cached so re-runs are cheap.
"""
import csv, json, re, time, urllib.parse, urllib.request, urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path

import pipeline as P

HERE = Path(__file__).resolve().parent
CACHE_PATH = HERE / "audit_results" / "web_hours_cache.json"
OUT = HERE / "audit_results" / "proposals_hours_web.csv"
BROKEN_OUT = HERE / "audit_results" / "broken_websites.csv"
UA = "ShelterAppHoursBot/1.0 (nonprofit resource directory; shelter.app)"
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "gemma4:e2b"
SKIP_DOMAINS = ("facebook.com", "instagram.com", "twitter.com", "x.com",
                "linktr.ee", "linkedin.com", "youtube.com", "tiktok.com")
DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
_ABBR = {d[:3].lower(): d for d in DAYS}
_ABBR.update({"tues": "TUESDAY", "wed": "WEDNESDAY", "thur": "THURSDAY",
              "thurs": "THURSDAY"})


# ------------------------------------------------------------------ cache
def _load_cache():
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(c):
    CACHE_PATH.parent.mkdir(exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(c), encoding="utf-8")
    tmp.replace(CACHE_PATH)


# ------------------------------------------------------------------ fetching
_ROBOTS, _LAST = {}, [0.0]


def _allowed(url):
    host = urllib.parse.urlsplit(url)
    base = f"{host.scheme}://{host.netloc}"
    if base not in _ROBOTS:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10) as r:
                rp.parse(r.read(200_000).decode("utf-8", "ignore").splitlines())
        except Exception:
            rp = None                      # no/unreadable robots.txt -> allowed
        _ROBOTS[base] = rp
    rp = _ROBOTS[base]
    return True if rp is None else rp.can_fetch(UA, url)


BROKEN_LINK = ("website domain does not exist", "website page not found (404/410)")
_LAST_ERR = [""]


def fetch(url):
    """-> (final_url, html) or None (reason in _LAST_ERR). Polite: robots.txt
    + ~1s spacing."""
    import socket, urllib.error
    host = urllib.parse.urlsplit(url).hostname or ""
    try:
        socket.getaddrinfo(host, 443)
    except socket.gaierror:
        _LAST_ERR[0] = BROKEN_LINK[0]
        return None
    if not _allowed(url):
        _LAST_ERR[0] = "site disallows bots (robots.txt)"
        return None
    gap = time.time() - _LAST[0]
    if gap < 1.0:
        time.sleep(1.0 - gap)
    _LAST[0] = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept": "text/html,*/*;q=0.5"})
        with urllib.request.urlopen(req, timeout=20) as r:
            if "html" not in (r.headers.get("Content-Type") or "html"):
                return None
            raw = r.read(2_000_000)
            cs = r.headers.get_content_charset() or "utf-8"
            try:
                return r.geturl(), raw.decode(cs)
            except (UnicodeDecodeError, LookupError):
                return r.geturl(), raw.decode("cp1252", "ignore")   # common mislabel
    except urllib.error.HTTPError as e:
        _LAST_ERR[0] = BROKEN_LINK[1] if e.code in (404, 410) else f"site refused (HTTP {e.code})"
    except Exception:
        _LAST_ERR[0] = "site unreachable (timeout/connection)"
    return None


def normalize_url(u):
    u = (u or "").strip()
    if not u or " " in u:
        return None
    if not re.match(r"https?://", u, re.I):
        u = "http://" + u.lstrip("/")
    host = urllib.parse.urlsplit(u).netloc.lower()
    if not host or any(host == d or host.endswith("." + d) for d in SKIP_DOMAINS):
        return None
    return u


# ------------------------------------------------------------------ parsing
class _Page(HTMLParser):
    """Visible text, JSON-LD blocks, and links from one HTML page."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.jsonld, self.links = [], [], []
        self._skip = 0; self._ld = None; self._a = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript"):
            if tag == "script" and "ld+json" in (a.get("type") or ""):
                self._ld = []
            else:
                self._skip += 1
        elif tag == "a" and a.get("href"):
            self._a = [a["href"], ""]
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "td"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._ld is not None:
            self.jsonld.append("".join(self._ld)); self._ld = None
        elif tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        elif tag == "a" and self._a:
            self.links.append(tuple(self._a)); self._a = None

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
        elif not self._skip:
            self.text.append(data)
            if self._a:
                self._a[1] += data


def parse_page(html):
    p = _Page()
    try:
        p.feed(html)
    except Exception:
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.text))
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text, p.jsonld, p.links


# ------------------------------------------------------------------ layer 1: structured
def _hhmm(t):
    m = re.match(r"(\d{1,2}):(\d{2})", t or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _spec_entries(spec):
    """schema.org OpeningHoursSpecification(s) -> [(DAY, start, end)]."""
    out = []
    for s in spec if isinstance(spec, list) else [spec]:
        if not isinstance(s, dict):
            continue
        o, c = _hhmm(s.get("opens")), _hhmm(s.get("closes"))
        days = s.get("dayOfWeek") or []
        days = days if isinstance(days, list) else [days]
        if not (o and c) or o == c:
            continue                       # "closed" days are often opens==closes
        for d in days:
            d = str(d).rstrip("/").split("/")[-1].upper()
            if d in DAYS:
                out.append((d, P._to_ampm(o), P._to_ampm(c)))
    return out


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _addr_matches(node, listing):
    """A JSON-LD node's address must agree with the listing (zip or street
    number) when it has one; multi-location orgs publish many hours blocks."""
    a = node.get("address")
    z = (listing.get("zip") or "")[:5]
    num = (re.match(r"\s*(\d+)", listing.get("address1") or "") or [None, None])[1]
    if isinstance(a, str) and a.strip():   # one-line address, e.g. "80 MLK Jr Dr, Winder, GA 30680"
        return bool((z and re.search(rf"\b{z}\b", a)) and (not num or re.match(rf"\s*{num}\b", a)))
    if not isinstance(a, dict):
        return None                        # no address on this node: unknown
    pz = str(a.get("postalCode") or "")[:5]
    st = str(a.get("streetAddress") or "")
    return bool((z and pz == z) or (num and re.match(rf"\s*{num}\b", st)))


def structured_hours(html, jsonld, listing, strict=False):
    """strict=True (multi-location org): only hours tied to this address."""
    found = []                             # (entries, address_match)
    for block in jsonld:
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        for node in _walk(data):
            ents = []
            if node.get("openingHoursSpecification"):
                ents = _spec_entries(node["openingHoursSpecification"])
            elif node.get("openingHours"):
                oh = node["openingHours"]
                ents = P.parse_opening_hours("; ".join(oh) if isinstance(oh, list) else str(oh))
            if ents:
                found.append((ents, _addr_matches(node, listing)))
    for m in re.finditer(r"itemprop=[\"']openingHours[\"'][^>]*content=[\"']([^\"']+)", html, re.I):
        ents = P.parse_opening_hours(m.group(1))
        if ents:
            found.append((ents, None))
    good = [e for e, ok in found if ok]
    _ADDR_MATCHED[0] = bool(good)
    if good:
        return good[0]
    if strict:
        return None
    unknown = [e for e, ok in found if ok is None]
    if len(found) == len(unknown) == 1:    # one block, no address to contradict it
        return unknown[0]
    return None                            # none, or ambiguous multi-location


_ADDR_MATCHED = [False]                    # did the last structured hit name this address?


def conflicts_with_page(entries, text):
    """Site templates often ship a default '9-5' that nobody updated. If the
    visible page says 24/7 but the data doesn't, trust neither: reject."""
    says_247 = re.search(r"\b24\s*/\s*7\b|\b24\s*hours\b|\b24-hour\b|\bopen 24\b", text, re.I)
    is_247 = bool(entries) and all(s == "12:00 AM" and e in ("11:59 PM", "12:00 AM")
                                   for _, s, e in entries)
    return bool(says_247 and not is_247)


def _structured_result(ents, text, url):
    if conflicts_with_page(ents, text):
        return None, "site data conflicts with page (page says 24/7)", url
    how = "address-matched" if _ADDR_MATCHED[0] else "no address on site data"
    return ents, f"Website (structured data, {how})", url


# ------------------------------------------------------------------ layer 2: AI read
_DAYWORD = r"(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*\.?"
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?(?![\d:])", re.I)


def hours_snippet(text, cap=2500):
    """Text windows around hours-like wording, or '' when the page has none."""
    hits = [m.start() for m in re.finditer(
        rf"\bhours\b|\bopen\b|\b{_DAYWORD}\s*(?:-|–|to|through|thru)\s*{_DAYWORD}|\b(?:monday|weekdays)\b",
        text, re.I)]
    if not hits or not _TIME.search(text):
        return ""
    spans = []
    for h in hits:
        a, b = max(0, h - 300), min(len(text), h + 500)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    snip = "\n...\n".join(text[a:b] for a, b in spans)[:cap]
    return snip if re.search(r"\d", snip) and re.search(_DAYWORD, snip, re.I) else ""


def _minutes_in(snip):
    """Every clock time the snippet could mean, as minutes after midnight."""
    out = set()
    if re.search(r"\bnoon\b", snip, re.I):
        out.add(12 * 60)
    if re.search(r"\bmidnight\b", snip, re.I):
        out.update({0, 24 * 60 - 1})
    for h, m, ap in _TIME.findall(snip):
        h, m = int(h), int(m or 0)
        if h > 24 or m > 59:
            continue
        ap = (ap or "").lower().replace(".", "").replace(" ", "")
        if ap == "am":
            out.add((0 if h == 12 else h) * 60 + m)
        elif ap == "pm":
            out.add((12 if h == 12 else h + 12) * 60 + m)
        else:
            out.update({h * 60 + m, ((h % 12) + 12) * 60 + m})
    return out


def _covered_days(snip):
    s = snip.lower()
    days = {d for a, d in _ABBR.items() if re.search(rf"\b{a}", s)}
    for a, b in re.findall(rf"\b({_DAYWORD})\s*(?:-|–|to|through|thru)\s*({_DAYWORD})", s):
        da, db = _ABBR.get(a.rstrip(".")[:3]), _ABBR.get(b.rstrip(".")[:3])
        if da and db:
            i, j = DAYS.index(da), DAYS.index(db)
            days.update(DAYS[i:j + 1] if i <= j else DAYS[i:] + DAYS[:j + 1])
    if re.search(r"\bweekdays\b|\bm\s*-\s*f\b", s):
        days.update(DAYS[:5])
    if re.search(r"\bdaily\b|\b7 days\b|\bevery day\b|\bseven days\b", s):
        days.update(DAYS)
    return days


def _to_min(t):
    """'9:00 AM' / '9am' / '5 p.m.' / 'noon' -> minutes after midnight, else None."""
    t = (t or "").strip().lower()
    if t == "noon":
        return 12 * 60
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)$", t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    ap = "PM" if m.group(3).startswith("p") else "AM"
    if h > 12 or mi > 59:
        return None
    return ((0 if h == 12 else h) + (12 if ap == "PM" else 0)) * 60 + mi


def verify(entries, snip):
    """Keep the AI result only if EVERY day and time it gave is on the page."""
    if not entries:
        return None
    mins, days = _minutes_in(snip), _covered_days(snip)
    out = []
    for e in entries:
        d = str(e.get("day", "")).upper()
        o, c = _to_min(e.get("open")), _to_min(e.get("close"))
        if d not in DAYS or d not in days or o is None or c is None:
            return None
        if o not in mins or c not in mins or c <= o:
            return None
        out.append((d, P._to_ampm(f"{o // 60}:{o % 60:02d}"),
                    P._to_ampm(f"{c // 60}:{c % 60:02d}")))
    return out


def ai_hours(snip, listing):
    prompt = (
        "Below is text from an organization's website. Extract its regular weekly "
        f"opening hours for the location at {listing.get('address1','')}, "
        f"{listing.get('city','')} {listing.get('state','')}.\n"
        "Rules: use ONLY what the text states. If the text lists several locations "
        "and you cannot tell which is this one, or hours are not stated, return an "
        "empty list. Times as 'H:MM AM' / 'H:MM PM'. One object per open day.\n"
        'Reply with JSON only: {"hours": [{"day": "MONDAY", "open": "9:00 AM", '
        '"close": "5:00 PM"}]}\n\nTEXT:\n' + snip)
    body = {"model": MODEL, "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [{"role": "user", "content": prompt}]}
    try:
        req = urllib.request.Request(OLLAMA, json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            msg = json.load(r)["message"]["content"]
        hrs = json.loads(msg).get("hours") or []
        return hrs if isinstance(hrs, list) else []
    except Exception:
        return None                        # Ollama down / bad JSON -> retry next run


# ------------------------------------------------------------------ per listing
def _subpage(base_url, links):
    """Same-site page most likely to hold hours (hours/visit/contact/location)."""
    host = urllib.parse.urlsplit(base_url).netloc
    best = None
    for href, label in links:
        u = urllib.parse.urljoin(base_url, href)
        if urllib.parse.urlsplit(u).netloc != host or u.split("#")[0] == base_url:
            continue
        hay = (href + " " + label).lower()
        for rank, word in enumerate(("hours", "visit", "location", "contact", "about")):
            if word in hay and (best is None or rank < best[0]):
                best = (rank, u.split("#")[0])
    return best[1] if best else None


def find_hours(listing, use_ai=True, strict=False):
    """-> (entries, source, url) or (None, reason, url)."""
    url = normalize_url(listing.get("website"))
    if not url:
        return None, "no usable website", ""
    pages, tried = [], url
    got = fetch(url)
    if not got:
        return None, _LAST_ERR[0], url
    pages.append(got)
    text, jsonld, links = parse_page(got[1])
    sub = _subpage(got[0], links)
    for final, html in pages:
        ents = structured_hours(html, jsonld, listing, strict)
        if ents:
            return _structured_result(ents, text, final)
    snip = hours_snippet(text)
    if not snip and sub:
        got2 = fetch(sub)
        if got2:
            t2, ld2, _ = parse_page(got2[1])
            ents = structured_hours(got2[1], ld2, listing, strict)
            if ents:
                return _structured_result(ents, t2, got2[0])
            snip, tried = hours_snippet(t2), got2[0]
    else:
        tried = got[0]
    if not snip:
        return None, "no hours on site", tried
    if not use_ai:
        return None, "hours text found (AI off)", tried
    raw = ai_hours(snip, listing)
    if raw is None:
        return None, "AI unavailable", tried
    if not raw:
        return None, "AI: no hours stated for this location", tried
    ents = verify(raw, snip)
    if not ents:
        return None, "AI result failed page check", tried
    return ents, "Website (AI-read, verified against page)", tried


# ------------------------------------------------------------------ driver
def run(master, states=None, cap=None, use_ai=False, has_hours=None):
    """Find hours for listings missing them; write proposals_hours_web.csv."""
    from collections import Counter
    has_hours = has_hours or P._listing_has_hours
    # Each location is its own listing. Orgs with several locations often share
    # one website, so for them only hours tied to THIS location's address count.
    name_count = Counter((d.get("name") or "").strip().lower() for d in master)
    site_count = Counter(normalize_url(d.get("website")) for d in master)
    multi = lambda d: (name_count[(d.get("name") or "").strip().lower()] > 1
                       or site_count[normalize_url(d.get("website"))] > 1)
    todo = [d for d in master
            if not has_hours(d) and (d.get("website") or "").strip()
            and (not states or (d.get("state") or "").upper() in states)]
    todo = todo[:cap] if cap is not None else todo
    print(f"{len(todo)} listing(s) missing hours with a website to check "
          f"({sum(1 for d in todo if multi(d))} belong to multi-location orgs: "
          f"only hours tied to their own address are used).")
    if use_ai:
        print(f"Plain-text hours are read by Ollama ({MODEL}) and checked against the page.")

    cache, rows, tally = _load_cache(), [], Counter()
    for i, d in enumerate(todo, 1):
        key = P.inv_key(d)
        hit = cache.get(key)
        if hit and not (hit["reason"] == "AI unavailable" and use_ai):
            ents = [tuple(e) for e in hit["entries"]] if hit["entries"] else None
            reason, url = hit["reason"], hit["url"]
        else:
            ents, reason, url = find_hours(d, use_ai, strict=multi(d))
            cache[key] = {"entries": ents, "reason": reason, "url": url,
                          "ts": time.strftime("%Y-%m-%d")}
            if i % 10 == 0:
                _save_cache(cache)
        if ents:
            structured = reason.startswith("Website (structured")
            rows.append([d.get("name", ""), d.get("address1", ""), d.get("city", ""),
                         d.get("state", ""), reason, P._ser_sched(ents),
                         "yes" if "address-matched" in reason else "check", url, d.get("zip", "")])
            tally[("matched" if "address-matched" in reason else "structured")
                  if structured else "ai"] += 1
            print(f"  [{i}/{len(todo)}] HOURS ({'site data' if structured else 'AI, verified'}): "
                  f"{d.get('name','')[:40]} - {len(ents)} day(s)")
        else:
            tally[reason] += 1
    _save_cache(cache)

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "address1", "city", "state", "source_hours",
                    "schedule", "apply", "source_url", "zip"])
        w.writerows(rows)
    print(f"\nFound hours for {len(rows)} of {len(todo)}:\n"
          f"  {tally['matched']:5}  site data tied to this address (apply=yes)\n"
          f"  {tally['structured']:5}  site data with no address (apply=check: review first)\n"
          f"  {tally['ai']:5}  AI-read, page-verified (apply=check: review first)")
    for k, v in tally.most_common():
        if k not in ("matched", "structured", "ai"):
            print(f"  {v:5}  {k}")
    broken = [d for d in todo if (cache.get(P.inv_key(d)) or {}).get("reason") in BROKEN_LINK]
    with open(BROKEN_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "address1", "city", "state", "website", "problem"])
        for d in broken:
            w.writerow([d.get("name", ""), d.get("address1", ""), d.get("city", ""),
                        d.get("state", ""), d.get("website", ""),
                        cache[P.inv_key(d)]["reason"]])
    print(f"\nProposals: {OUT}\nApply with:\n  py pipeline.py --apply-hours {OUT}")
    print(f"Broken website links ({len(broken)}), for the audit / manual fixing: {BROKEN_OUT}")
    return rows


# ------------------------------------------------------------------ self-test
def _selftest():
    lst = {"name": "X", "address1": "100 Main St", "zip": "85201"}
    ld = ['{"@type":"LocalBusiness","address":{"postalCode":"85201"},'
          '"openingHoursSpecification":[{"dayOfWeek":["Monday","Tuesday"],'
          '"opens":"09:00","closes":"17:00"}]}']
    assert structured_hours("", ld, lst) == [("MONDAY", "9:00 AM", "5:00 PM"),
                                              ("TUESDAY", "9:00 AM", "5:00 PM")]
    other = ['{"@type":"LocalBusiness","address":{"postalCode":"10001"},'
             '"openingHours":"Mo-Fr 08:00-16:00"}']
    assert structured_hours("", other, lst) is None          # different location
    snip = "Our Hours: Monday - Friday 9am to 4:30pm. Closed weekends."
    good = [{"day": "MONDAY", "open": "9:00 AM", "close": "4:30 PM"},
            {"day": "FRIDAY", "open": "9:00 AM", "close": "4:30 PM"}]
    assert verify(good, snip) == [("MONDAY", "9:00 AM", "4:30 PM"),
                                  ("FRIDAY", "9:00 AM", "4:30 PM")]
    assert verify([{"day": "SATURDAY", "open": "9:00 AM", "close": "4:30 PM"}], snip) is None
    assert verify([{"day": "MONDAY", "open": "8:00 AM", "close": "4:30 PM"}], snip) is None
    assert hours_snippet("Welcome to our library. We love books.") == ""
    assert normalize_url("www.example.org") == "http://www.example.org"
    assert normalize_url("https://facebook.com/x") is None
    return True


if __name__ == "__main__":
    print("web_hours self-test:", "PASS" if _selftest() else "FAIL")
