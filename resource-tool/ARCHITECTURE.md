# Architecture

Developer-facing overview of the ShelterApp Resource Tool. For setup and everyday
use, see [README.md](README.md).

## TL;DR

Pure Python 3.11+ (stdlib) + two deps: **Playwright** (Chromium automation of
shelter.app) and **openpyxl** (xlsx reports). No framework, no database — state
lives as JSON/CSV files under `audit_results/`. One entry point: `run.py`.

```
public data sources ──▶ match against cached inventory ──▶ add missing (browser) ──▶ audit
   (HRSA, OSM, Google)          (match_audit)                 (pipeline)          (pipeline)
```

## Modules

| File | Responsibility |
|---|---|
| **`run.py`** | CLI + orchestrator. Walks the homelessness-relevant `PLAN` (fqhc → resource_center → emergency_shelter → library), state by state, resume-aware. Modes: *(default)* find+add, `--status`, `--plan`, `--audit`, `--fill-hours`, `--fill-contacts` (Google, off without a key), `--fill-hours-web` (free), `--refresh`, `--selftest`. |
| **`resource_sources.py`** | Data layer (no framework). HRSA FQHCs by state, OSM/Overpass by radius or by state, Nominatim geocode/zip, and Google Places hours. `_get()` retries 429/5xx/timeout; results cached in `audit_results/geocode_cache.json`. |
| **`match_audit.py`** | Pure, network-free matching/verification: `norm_name`, `name_sim`, `best_match`, `find_gaps` (token+ZIP blocking), `verify_listing`, `shared_address_map`. Ships `_selftest()`. |
| **`pipeline.py`** | The browser engine (Playwright) + reporting. Inventory scrape (`parse_card`/`get_cards_js`), the **duplicate guard** (`listing_status`), Add-Service form filling (`set_fields_js`, `check_labels_js`, schedule widget), `add_service`, the audit loop, xlsx export, and the `--apply-fixes` / `--apply-hours` writers. Also `parse_opening_hours`, `_Tee`, `_utf8_console`. |
| **`audit_v2_fixed.py`** | Legacy browser helpers still imported by `pipeline` (`read_all_fields`, `get_edit_buttons`). |
| **`web_hours.py`** | Free hours from each listing's own website (`run.py --fill-hours-web`). Layer 1: schema.org JSON-LD/microdata; only auto-approved when tied to the listing's address; rejected if the page says 24/7 but the data doesn't. Layer 2 (opt-in `--ai`): Ollama reads plain text, every day/time must appear on the page; review-only (spot check found it often picks the wrong section). Polite: robots.txt, 1 req/s, cache `audit_results/web_hours_cache.json`. Also writes `broken_websites.csv`. |
| `batch_fqhc.py`, `batch_osm_state.py`, `batch_osm.py` | Per-category rollout drivers. `run.py` supersedes these for the main flow but they remain usable and share the same progress files. |
| `fix_targets.py`, `fix_zips.py`, `propose_hours.py`, `triage.py`, `find_duplicates.py`, `fqhc_scope.py`, `add_manual.py` | Standalone maintenance utilities. |
| `inspect_*.py`, `test_add.py` | Debug/inspection helpers for the live DOM. |

## Data flow

1. **Sources** → normalized resource dicts `{name, category, address1, city, state, zip, phone, website, schedule}` (`resource_sources.query_*`).
2. **Inventory** → existing ShelterApp listings, scraped once and cached in `audit_results/inventory_master.json`.
3. **Gap** → `match_audit.find_gaps(inventory, sources)` returns sources with no confident match.
4. **Add** → `pipeline.add_service(gap)`:
   - `listing_status()` does a **live** search of Manage Services; adds only on a positive `absent` (never on `unknown`).
   - completeness gate: name + address + city + state.
   - hours: parsed from the source's `opening_hours`, else Google Places, else "contact for hours".
5. **Audit** → `verify_listing(listing, addr_geo, org_geo)` → verdict (OK / LOW / MEDIUM / HIGH) → xlsx.

## State & resume (`audit_results/`)

| File | Purpose |
|---|---|
| `inventory_master.json` | Cached listings, one per name + street + ZIP (`pipeline.inv_key`; name alone collapsed same-named listings). Refresh with `py run.py --refresh`. |
| `fqhc_progress.json`, `osm_state_progress_<cat>.json` | `{done: [states], handled: [names]}` — how resume + chunking work. |
| `geocode_cache.json` | Nominatim geocodes **and** Google-hours results (`ghours:` keys). |
| `*_summary.csv` | Per-run history. `run.log` / `last_run.log` — full logs. |

## Invariants (don't break these)

- **No duplicates**: every add is gated by a live `listing_status` search; `unknown` → skip (fail-safe).
- **Complete records only** and **never deletes** (deletions are always human, in-app).
- **Rate limits**: `_get` backoff on 429/5xx/timeout; Nominatim 1 req/1.1s enforced inside `_get` (cache hits never wait); geocode cache flushed every 25 writes + at exit; Google capped (`GOOGLE_MAX_LOOKUPS`, default 5000) + cached.

## Configuration / secrets

- **Google key**: `google_places_key.txt` (first non-comment line) **or** env `GOOGLE_PLACES_API_KEY`. The tool runs without it (no auto-hours).
- Env `GOOGLE_MAX_LOOKUPS` caps Google calls per run.
- Secrets and generated state are `.gitignore`d.

## Testing

```
py run.py --selftest      # offline: matcher, gap, verify, hours converter, opening-hours parser
```
Exit code 0 on pass, 1 on failure — safe to run in CI (no browser, network, or key needed).

## Extending

- **New OSM category**: add to `OSM_CATEGORIES` (resource_sources), `CATEGORY_TO_SERVICE`/`CATEGORY_TO_DETAILS` (pipeline), and `PLAN` (run.py).
- **New data source**: add a `query_*` in `resource_sources` returning the normalized dict, then reference it from `run.py:sources_for()`.

## Platform notes

Windows is the primary target (`setup.bat`, the `py` launcher). On macOS/Linux use
`setup.sh` and `python3` in place of `py`. Everything else is cross-platform
(pathlib, urllib, Playwright).
