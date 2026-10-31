# AIBot

AI Bot to add and validate services in ShelterApp, and to help people find them.

**Status: test version (October 2026).** A "Find help now" assistant: a person says what they need and where, in their own words and language, and gets matching ShelterApp listings, official disaster alerts for their state, and crisis lines when they may be in danger.

This repo has two parts, both built by Amanda J. Richer:

1. **Mandy** (the root folder): the "Find help now" assistant described below.
2. **The resource tool** (`resource-tool/`): finds resources missing from ShelterApp (health centers, resource centers, shelters, libraries) from free public data, adds them with strong duplicate protection, and audits existing listings for bad or placeholder data. This is the "add and validate services" part. Python; start with `resource-tool/README.md`, and run its offline checks with `py run.py --selftest` inside that folder.

They work together: the resource tool keeps a checked copy of ShelterApp's listings in `resource-tool/audit_results/inventory_master.json`, and Mandy reads that file automatically when it's there. That folder holds working data and is never committed.

## Promises this code keeps

- **No personal data.** No sign-in, no names, no phone numbers. Conversations are never saved or logged; the chat lives only in the person's browser tab.
- **Facts come from ShelterApp, never from the AI.** The model only (1) turns a message into a search, (2) checks it for signs of danger, and (3) writes a short, kind sentence. Names, addresses, phones, hours and alerts are filled in by code.
- **Crisis lines are fixed code.** 911, 988 and the domestic violence hotline stay on screen at all times, and show again first if a message suggests danger.
- **Free to run.** Works with a free local model (Ollama) or any OpenAI-compatible endpoint, including free hosted tiers.

## Try it

Needs Node 22.18 or newer (runs TypeScript directly, no build step) and, for the AI part, [Ollama](https://ollama.com) with a small model (`ollama pull gemma4:e2b`). Without a model it falls back to simple keyword matching.

```bash
cp .env.example .env
npm start
```

Then open http://localhost:8787. Run the automated checks (safety phrases, no false alarms, safe links, location handling, abuse handling) with `npm test`; they run with the AI model switched off, so they're fast and repeatable. Without real listing data it uses three fake demo listings in `data/sample-services.json`. Point `LISTINGS_PATH` at a ShelterApp listings export to use real data.

## Code map

| File | What it does |
|---|---|
| `src/bot.ts` | Conversation logic: message to search, search to reply |
| `src/listings.ts` | Loads and searches listings (nearest zip, then area, then city) |
| `src/disasters.ts` | Disaster mode: FEMA declarations, severe weather alerts, and open emergency shelters (pet-friendly first) by state |
| `src/safety.ts` | Crisis detection and crisis lines |
| `src/llm.ts` | Client for any OpenAI-compatible model |
| `src/matching.ts` | Duplicate detection for future "suggest a listing" (ported from the audit tool) |
| `src/server.ts` | Local web server; stores nothing |
| `public/index.html` | The chat screen |

Why a small free model, and why so much is fixed text: see "Why we built it this way" in `DESIGN.md`. See `CONVERSATION-GUIDE.md` for how the helper talks (trauma-informed care, empathy, de-escalation, location for people who are moved around) and `DESIGN.md` for the roadmap.
