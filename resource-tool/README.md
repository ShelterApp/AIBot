# ShelterApp Resource Tool

A tool that keeps [shelter.app](https://shelter.app) stocked with real, complete
resources for people experiencing homelessness. It:

1. **Finds** resources that should be listed but aren't (health centers, resource
   centers, shelters, libraries) from free public data,
2. **Adds** the missing ones — with real hours — with strong duplicate protection, and
3. **Audits** existing listings for bad or placeholder data.

Everything runs from **one command**: `py run.py`.

## The three steps at a glance

```
   ┌──────────── py run.py ─────────────────┐     ┌──── py run.py --audit ────┐
   │                                         │     │                            │
   │   1. FIND  ───▶   2. ADD (with hours)   │     │   3. AUDIT / verify        │
   │   what's missing   the missing ones     │     │   every listing is valid   │
   │   (free public data)                    │     │                            │
   └─────────────────────────────────────────┘     └────────────────────────────┘
        no duplicates · complete records only            flags bad/placeholder data
```

- **`py run.py`** does steps 1 + 2 together (this is the one you run most).
- **`py run.py --audit`** does step 3.
- They're split only because *adding* needs a browser login and *auditing* is a
  separate, slower pass — it's still one tool. (See the table below for every command.)

---

## What you need before you start

1. **A Windows PC.**
2. **Python 3.11+** — install from [python.org/downloads](https://www.python.org/downloads/).
   ⚠️ On the first install screen, **check the box "Add Python to PATH."**
3. **A ShelterApp account** that can reach **Manage Services** (you'll log in each time).
4. **Nothing paid.** The tool runs on free sources only. (Google lookups are optional and
   OFF unless someone deliberately adds a key; see the Google section at the end.)

---

## One-time setup (about 10 minutes)

1. Unzip this folder somewhere simple, e.g. `C:\shelterapp-tool`.
2. Double-click **`setup.bat`**. It installs everything the tool needs (this can take
   a few minutes the first time — it downloads a browser).
3. Leave **`google_places_key.txt`** as it is. Without a key, the tool makes no paid calls.

That's it. You're set up.

---

## Everyday use

Open a terminal **in this folder** (Shift + right-click the folder → "Open in Terminal"
or "Open PowerShell window here"), then:

| Command | What it does |
|---|---|
| `py run.py` | **The main one.** Finds + adds the next batch of missing resources (200 by default). Log in when the browser opens, press ENTER in the terminal, then leave it running. |
| `py run.py --status` | Shows where everything stands. No login needed. |
| `py run.py --audit` | Checks existing listings for bad/placeholder data → report in `audit_results\`. |
| `py run.py --fill-hours-web` | **Free.** Reads hours from each listing's own website (only hours the site ties to that exact address) → `proposals_hours_web.csv`. Also lists dead website links → `broken_websites.csv`. |
| `py run.py --fill-hours` / `--fill-contacts` | Google lookups. **Off** unless a key is added (paid service). |
| `py run.py --refresh` | Re-scrapes the full listing (read-only) so counts include recently-added ones. Slow (~1 hr+). |
| `py run.py --plan` | Preview what the next batch would add, without touching anything. |

**Applying proposals** (`--fill-hours-web`, or the Google ones if enabled): each writes a review file into
`audit_results\`. Contacts apply like any other correction:
`py pipeline.py --apply-fixes audit_results\proposals_phone.csv` (hours use `--apply-hours`).
Tip: run `--refresh` before `--fill-contacts` so it sees every listing, not just the cache.

**Running `py run.py`:**
1. A browser window opens. **Log into ShelterApp** and get to Manage Services.
2. Click back to the terminal and **press ENTER** (in the terminal, not the browser).
3. It adds the next batch on its own. Keep the window open; you can walk away.
4. Run it again anytime to continue — it always **picks up where it left off**.

To add fewer/more in one go: `py run.py --max 50`

---

## How it protects the data (read this — it's the important part)

- **No duplicates, ever.** Before adding anything, the tool does a *live* search of the
  whole ShelterApp site and only adds when it confirms the resource isn't already there.
- **Complete records only.** It won't add a resource missing a name, address, city, or state.
- **Real hours** only when a trustworthy free source has them; otherwise it marks "contact for hours."
  Wrong hours are worse than none, so the website reader rejects anything it can't tie to the
  listing's own address, or that contradicts the page (e.g. a crisis center whose page says 24/7).
- **Each location is its own listing.** Same-named locations (e.g. several "Salvation Army"
  sites) are told apart by street address and ZIP when reading and when editing.
- **Contact info required.** A new listing needs a phone or email; one with neither is skipped.
- **It never deletes anything.** Cleanup deletions are always done by a person, in the app.

---

## For a team: how to share the work

Everyone can run the tool, but a few notes so you don't step on each other:

- **Everyone logs into their own ShelterApp account.** The tool never stores or shares logins.
- **Split the work** so two people aren't adding the same thing at once. Easiest split is
  **by category or by state**, e.g.:
  - `py run.py --only fqhc --states CA` (health centers, California)
  - `py run.py --only library --states TX,OK` (libraries, Texas & Oklahoma)
  - See `py run.py --status` for what's already done.
- **Progress is tracked in the `audit_results\` folder.** If you want the whole team to
  share one progress history (so nobody redoes a finished state), keep this tool folder on
  a shared drive, or have one person periodically share their `audit_results\*_progress.json`
  files. If you skip that, each person's copy just tracks its own progress — still safe
  (the live duplicate check prevents any double-adds), just a little less efficient.
- **The Google key:** for a small team, one shared key is fine. Share it through a password
  manager or another secure channel — **not** inside this folder or over email. Or each
  person makes their own free key (see below).

---

## Optional: Google Places API key (PAID service, off by default; skip unless funded)

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and sign in.
2. Create a project (e.g. `shelterapp-hours`).
3. Turn on **billing** (a card is required to activate, but normal usage stays within
   Google's free monthly allowance — the tool also caps and caches lookups so it can't run away).
4. **APIs & Services → Library →** search **"Places API" → Enable**.
5. **APIs & Services → Credentials → Create credentials → API key.** Copy it.
6. **Restrict the key** to Maps/Places for safety (Edit key → API restrictions).
7. Paste it into `google_places_key.txt` (replacing the placeholder line).

---

## Troubleshooting

- **"py is not recognized"** → Python isn't installed or wasn't added to PATH. Reinstall
  Python and check "Add Python to PATH." (You can also try `python` instead of `py`.)
- **"No such file or directory"** → make sure your terminal is open *in this folder*.
- **The browser opens but nothing adds** → make sure you logged in and pressed ENTER in the
  *terminal*, and that you can see the Manage Services list.
- **"No Google Places key found"** → your key isn't in `google_places_key.txt` yet (or the
  file still has the placeholder line).
- **Something crashed** → the full log is saved in `audit_results\run.log`. Send that file
  to whoever is helping you.

---

## For developers

Two deps (`playwright`, `openpyxl`); everything else is stdlib. Design notes,
module map, data flow, and invariants are in **[ARCHITECTURE.md](ARCHITECTURE.md)**.

- **Run the tests** (offline — no browser, network, or key):
  ```
  py run.py --selftest        # python3 run.py --selftest on macOS/Linux
  ```
  Exit code 0 = pass, 1 = fail. Safe for CI.
- **Key via env var** instead of the file: set `GOOGLE_PLACES_API_KEY`.
  Cap Google usage with `GOOGLE_MAX_LOOKUPS` (default 5000).
- **Non-interactive runs**: add `--no-pause` so it won't wait for ENTER on exit.
- **macOS/Linux**: run `setup.sh` and use `python3` in place of `py`.
- **Secrets & state** (`google_places_key.txt`, `audit_results/`) are `.gitignore`d —
  the code is safe to put in a private repo; the key never is.

## What's in this folder (you don't need to touch these)

- `run.py` — the single entry point (find → add → audit).
- `pipeline.py`, `resource_sources.py`, `match_audit.py` — the engine.
- `batch_fqhc.py`, `batch_osm_state.py`, `batch_osm.py` — category rollout helpers.
- `audit_results\` — reports, progress, and logs.
- `google_places_key.txt` — **your secret key goes here** (never share it).
- `COMMANDS.txt` — a quick copy-paste cheat sheet.
