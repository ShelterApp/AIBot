# AIBot: design and roadmap

**Status:** draft from Manda (Amanda J. Richer) for Raj and the ShelterApp team. Raj and the team can change anything.

## Who it serves and the rules it keeps

The end user is a person experiencing homelessness, often on a prepaid phone, often in a hurry, sometimes in danger. The bot:

1. **Collects no personal data.** No accounts, names, phone numbers or saved chats. Location only as a zip or city, never stored. No analytics that read conversations (the old Dashbot integration must not come back). The AI provider must not train on or keep what people type.
2. **Never invents facts.** Listings, hours and phone numbers come from ShelterApp's database; the AI only understands the question and words the answer.
3. **Puts safety first.** Crisis lines are shown by fixed code, not left to the AI.
4. **Speaks plainly,** in the person's language, without forms or questions about their history.

## Why we built it this way: cost and efficiency

ShelterApp has no budget for AI. Mandy has to run all day, every day, for anyone who needs her, without a bill that grows with every conversation. Manda learned this firsthand: running her own tools on paid AI used up tokens fast, so she moved her daily tools to a free model on her own computer. Mandy follows the same thinking.

- **A small, free model, not a big paid one.** Paid AI charges for every word it reads and writes. A few cents per conversation sounds small, but thousands of conversations a day becomes real money that ShelterApp doesn't have. A small model (Gemma 4 E2B, run free with Ollama) costs nothing per message.
- **The AI does as little as possible.** It does two small jobs: understand the message, and write one caring sentence when someone shares something hard. Everything else is plain code, which is free, instant, and always the same.
- **Fixed text for anything that matters.** Questions, next steps, crisis lines and the dignity note are written once by people and reused. That costs nothing, can't go wrong, and keeps the wording Manda chose.
- **Facts come from the database, not the AI.** Listings, hours and phone numbers are looked up, not generated. That's cheaper and it's the only way to be accurate.
- **Fewer AI calls per message.** Safety checks, abuse and off-topic replies, location lookups, disaster alerts, and translations for English speakers all skip the AI.
- **Free public data only.** Disaster alerts come from FEMA and the National Weather Service; places are found with OpenStreetMap. No paid keys anywhere.
- **No data stored.** Nothing to store means no database to pay for, nothing to protect, and nothing that could be handed over about the people who use Mandy.
- **Easy to upgrade later.** The model is chosen by three settings in `.env`. If funding comes, ShelterApp can switch to a larger hosted model (including free tiers like Cloudflare Workers AI) without changing any code.

The trade-off, honestly: a small model is less polished. It sometimes picks the wrong category or writes a clumsy sentence. That's why the important parts never depend on it.

## Roadmap

**Version 1 (test version built):** "Find help now" in plain words and many languages; disaster mode (FEMA and National Weather Service alerts); crisis lines. Uses the existing thumbs up/down and feedback for quality. An optional "Was it open?" one-tap is a team decision.

**Version 2:** providers update by text ("12 beds tonight"; the API already has available beds); anyone can suggest or fix a listing, checked for address and duplicates, then approved by an admin through the existing approve-services step.

**Version 3:** let other AI assistants (ChatGPT and others) answer from ShelterApp's verified listings through an open standard (MCP and open data export); text messaging for people without data plans, set up so phone numbers are never stored.

## What the test version taught us

- **Data freshness is the real work.** Several night shelters in the listings show "Mon-Fri 8 AM to 5 PM," which looks like placeholder hours from old imports.
- **Other data errors found while testing:** phone numbers of "(000) 000-0000", an address of ".", and a Seattle listing whose zip field holds the street number ("13470"). Mandy hides the first two; the data itself needs fixing.
- **Coverage gaps show up fast.** In the local snapshot used for testing, Portland, Oregon had no food listings. The bot says so honestly and points to 211.
- **Emergency shelters (added Oct 8, during Hurricane Isaias).** When someone talks about evacuating, or asks for shelter while their state has official alerts, Mandy shows open emergency shelters from FEMA's free Open Shelters feed, with pet-friendly ones first if they mention a pet. Counties often report to FEMA late (on Oct 8 the feed listed no Gulf Coast shelters while several were open), so Mandy says so plainly and always adds the Red Cross shelter map, 211, and the Disaster Distress Helpline. Next step: county emergency management pages by county.
- **Disaster alerts are statewide for now.** A flood watch on the Florida coast showed for Tallahassee. Next step: match alerts to the person's county.

## Questions for Raj

1. Where is the live API, and can the bot have a read-only key for listings and a non-admin account for suggestions?
2. Should the bot live as a chat screen inside the app, the website, or both?
3. Is the team happy with TypeScript (it matches shelter-api and shelter-web)?

---

## Credit

**Vision and design: Amanda J. Richer (Manda).** Mandy's name and first words, the no-personal-data rule, trauma-informed care, the empathy and de-escalation approach, the dignity note, immigration and pet help, using each community's own words, and the choice of a free small model are her decisions, from her frontline expertise.

**Code and documents: drafted by Eolas** (an AI assistant, underneath Claude), working under Manda's direction, October 6 to 7, 2026.
