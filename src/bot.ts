// The conversation logic, following CONVERSATION-GUIDE.md. The model does three
// narrow jobs: (1) understand what someone typed, (2) check for signs of danger, and
// (3) write a few warm sentences. Listings, phone numbers, hours, alerts and crisis
// lines are filled in by code, so the model can't invent them.
import { chat, type ChatMessage } from './llm.ts';
import { searchListings, type Listing, type SearchQuery } from './listings.ts';
import { alertsFor, openShelters, DISASTER_HELP, type Alert } from './disasters.ts';
import { crisisKind, crisisBox, isSpanishCrisis, safetyMessage, type CrisisKind, type CrisisLine } from './safety.ts';
import { placeToArea, coordsToArea } from './geo.ts';
import { specialResources, aboutImmigration, aboutPets, type SpecialResource } from './special.ts';

// Not personal data: just how many times we've asked about location, echoed back
// by the browser so the server stays stateless.
export interface ConvoState { locationAsks: number }

export interface Context {
  state?: ConvoState;
  hour?: number;                       // the person's local hour, for "open now" ordering
  coords?: { lat: number; lon: number }; // only if they tapped "Use my location"
}

export interface BotReply {
  reply: string;
  results: Listing[];
  alerts: Alert[];
  crisis: CrisisLine[];
  crisisTitle?: string;                // the crisis box heading, when it isn't in English
  resources?: SpecialResource[];      // hand-checked help, e.g. for people targeted by ICE
  note?: string;                       // the dignity note, shown apart from Mandy's words
  choices?: Choice[];                  // buttons: a link to a person, or a message to send
  state: ConvoState;
}

export interface Choice { label: string; detail?: string; url?: string; send?: string }

const INTENTS = ['need_help', 'venting', 'wants_to_talk', 'abuse', 'off_topic'];

const DIGNITY_NOTE = 'ShelterApp believes in treating all beings with dignity and respect.';

// After someone hints at not wanting to live: people to talk to, or practical help.
const TALK_CHOICES: Choice[] = [
  { label: 'Talk with someone now', detail: 'Call or text 988, or chat online', url: 'https://988lifeline.org/chat/' },
  { label: 'Talk with a peer', detail: 'A warmline, run by people who have been through hard times too', url: 'https://warmline.org/' },
  { label: 'Find shelter, food, or other help', send: 'I need help finding resources' },
];

// The same moment in Spanish. The peer warmline joins once its Spanish help is confirmed.
const TALK_CHOICES_ES: Choice[] = [
  { label: 'Hablar con alguien ahora', detail: 'Marca 988 y presiona 2, o envía la palabra AYUDA al 988', url: 'https://988lifeline.org/interpretation-services/servicios-en-espanol/' },
  { label: 'Buscar refugio, comida u otra ayuda', send: 'Necesito ayuda para encontrar recursos' },
];

const TYPES =['food', 'shelter', 'health', 'resources', 'work'];
const WHO = ['men', 'women', 'kids', 'seniors', 'disabled', 'families', 'lgbt'];
const FEELINGS = ['upset', 'angry', 'scared', 'hopeless', 'overwhelmed', 'tired'];

const EXTRACT_PROMPT = `You read a conversation with a person who may be experiencing homelessness. Reply with JSON only:
{"type": one of ${JSON.stringify(TYPES)} or null,
 "zip": 5-digit US zip or null, "city": city name or null, "state": 2-letter US state or null,
 "place": a street, cross street, or nearby place they mentioned (like "corner store on MLK" or "5th and Pine"), or null,
 "who": one of ${JSON.stringify(WHO)} or null (only if they say it),
 "keywords": short extra needs like "pets" or "showers", or null,
 "shared_situation": true if they said they are homeless, unhoused, have no place to go, got kicked out or evicted, are sleeping outside or in a car, or don't know what to do; else false,
 "feeling": one of ${JSON.stringify(FEELINGS)} if their LAST message shows it, else null,
 "immigration": true if they or someone they love is being targeted, detained, or deported by ICE or immigration agents, or they need immigration legal help; else false,
 "danger": "suicide" if their LAST message hints at wanting to die, not wanting to live, or hurting themselves (even indirectly, like "might as well be dead" or "what's the point"); "danger" if they may be abused, attacked, threatened, or unsafe; else null,
 "intent": one of ${JSON.stringify(INTENTS)} for their LAST message: "need_help" if they want a service or answered a question about one; "venting" if they are upset or angry about their situation (even with swearing); "wants_to_talk" if they want someone to talk to or keep sharing feelings without asking for a service; "abuse" ONLY if they insult, threaten, harass, or send sexual or hateful messages aimed at the helper; "off_topic" if they ask for things unrelated to finding help (homework, jokes, poems, tricks),
 "language": REQUIRED, the language of the person's LAST message, like "English" or "Spanish"}
Only set "type" when they ask for something: saying they are homeless, by itself, does NOT set a type.
A place to sleep, a bed, or somewhere to stay means "shelter". Eating or meals means "food". Doctor, clinic, meds means "health".
Jobs mean "work". Showers, laundry, phones, ID, mail, clothes mean "resources".
Use the whole conversation: they may give their location in one message and their need in another.
Never guess a location they did not give.`;

const SAFETY_PROMPT = `You check one message sent to a helper for people who may be experiencing homelessness. Decide if the person may be at risk. Reply with JSON only: {"risk": "suicide" or "danger" or "none"}

"suicide": they may want to die or hurt themselves, even if they only hint at it. This includes feeling like a burden, saying others would be better off, having no reason to go on, saying goodbye, giving things away, or mentioning a plan or the means to do it.
"danger": another person may be hurting, threatening, following, trapping, or sexually exploiting them, or they say they do not feel safe, even if they only hint at it.
"none": everything else. Being homeless, evicted, or kicked out is "none". So is needing a place tonight, alone or with family. So is being tired, hungry, cold, sick, sad, or out of medicine. So is having things stolen, being moved along by police, or being angry about how they have been treated.

Any language. If you truly cannot tell between "none" and a risk, choose the risk.`;

// Asked on its own, because the small model often skips "danger" when it is one field
// among many. It leans toward a false alarm, which costs one gentle line and never blocks
// help, over a miss. Tested on made-up messages only: see "Safety" in CONVERSATION-GUIDE.md.
async function safetyCheck(text: string): Promise<CrisisKind> {
  try {
    const raw = await chat([{ role: 'system', content: SAFETY_PROMPT }, { role: 'user', content: text }], { json: true, maxTokens: 30 });
    return (raw.match(/suicide|danger/i)?.[0].toLowerCase() ?? null) as CrisisKind;
  } catch { return null; }
}

const REPLY_PROMPT = `You are Mandy, ShelterApp's automated helper (not a person) for people who may be experiencing homelessness.
Speak like a kind, steady outreach worker: plain words, short sentences, no jargon.
Rules:
- Do only what "What to say" asks. Do NOT ask any question: the app adds any question after your words.
- Write at most THREE short sentences.
- Never offer to talk, chat, or "figure things out together": you only help find resources.
- Never say whether places are open, full, or available: you don't know, and the app shows the listings.
- Never repeat their words back like a robot ("You are homeless."). Say it the way a caring person would.
- Never say "I understand exactly how you feel", never pity, never use cheerful filler, never promise things will be okay.
- Never say "calm down" or comment on how someone talks.
- Never write any service name, address, phone number, hours, or other facts: the app shows those below your words.
- Never ask for a name, phone number, ID, or why they are homeless. Never lecture or say "you should".
- Say "shelter", never "facility" or "placement". Never say "the homeless" or "clients".`;

function keywordSearch(text: string) {
  const t = text.toLowerCase();
  const map: [RegExp, string][] = [
    // Whole words only: "Seattle" contains "eat", "Bedford" contains "bed".
    [/\b(sleep\w*|beds?|shelters?|stay|tonight)\b/, 'shelter'], [/\b(food|eat\w*|meals?|hungry|pantry|pantries)\b/, 'food'],
    [/\b(doctors?|clinics?|health|medic\w*|dentist)\b/, 'health'], [/\b(jobs?|work\w*)\b/, 'work'],
    [/\b(shower\w*|laundry|clothes|phone|mail|id)\b/, 'resources'],
  ];
  return {
    type: map.find(([re]) => re.test(t))?.[1],
    zip: t.match(/\b\d{5}\b/)?.[0],
    shared_situation: /homeless|unhoused|no place|nowhere|kicked out|evicted|my car|don'?t know what to do/.test(t),
  };
}

function clean(raw: any) {
  const pick = (v: unknown, allowed?: string[]) => {
    const s = typeof v === 'string' ? v.trim().toLowerCase() : '';
    if (!s || s === 'null') return undefined;
    return allowed && !allowed.includes(s) ? undefined : s;
  };
  const state = String(raw?.state ?? '').trim().toUpperCase();
  return {
    type: pick(raw?.type, TYPES),
    zip: String(raw?.zip ?? '').match(/\d{5}/)?.[0],
    city: pick(raw?.city),
    state: /^[A-Z]{2}$/.test(state) ? state : undefined,
    place: pick(raw?.place),
    who: pick(raw?.who, WHO),
    keywords: pick(raw?.keywords),
    shared_situation: raw?.shared_situation === true,
    feeling: pick(raw?.feeling, FEELINGS),
    intent: pick(raw?.intent, INTENTS),
    immigration: raw?.immigration === true,
    danger: pick(raw?.danger, ['suicide', 'danger']) as 'suicide' | 'danger' | undefined,
    language: typeof raw?.language === 'string' ? raw.language : undefined,
  };
}

// Works out the state from a zip or city. A city name in more than one state
// ("Portland": Oregon or Maine) is returned as `choices`, so Mandy asks instead of guessing.
function inferState(all: Listing[], q: SearchQuery): { state?: string; choices: string[] } {
  if (q.state) return { state: q.state, choices: [] };
  const counts = new Map<string, number>();
  for (const l of all) {
    const hit = (q.zip && l.zip.slice(0, 3) === q.zip.slice(0, 3)) || (q.city && l.city.toLowerCase() === q.city);
    if (hit) counts.set(l.state, (counts.get(l.state) ?? 0) + 1);
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  // Ignore a state with only a stray listing or two (often a data-entry mistake).
  const ranked = sorted.filter(([, n]) => n >= Math.max(2, sorted[0][1] * 0.1)).map(([s]) => s);
  if (!q.zip && ranked.length > 1) return { choices: ranked.slice(0, 3) };
  return { state: ranked[0], choices: [] };
}

// City names that appear in the listings, longest first, built once per listing set.
const cityCache = new WeakMap<Listing[], string[]>();
function knownCities(all: Listing[]): string[] {
  let cities = cityCache.get(all);
  if (!cities) {
    cities = [...new Set(all.map(l => l.city.trim().toLowerCase()).filter(c => c.length >= 4))].sort((a, b) => b.length - a.length);
    cityCache.set(all, cities);
  }
  return cities;
}

// "food in Portland" or a reply that is just "tacoma": find a city the listings know.
// Requires "in/near/at/around/from" before it (or the whole message), so "mobile clinic"
// doesn't become Mobile, Alabama.
function findPlaceInText(all: Listing[], text: string): { city?: string } {
  const t = text.toLowerCase().replace(/[.,!?]/g, ' ').replace(/\s+/g, ' ').trim();
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  for (const c of knownCities(all)) {
    if (!t.includes(c)) continue;
    if (new RegExp(`^${esc(c)}(?: [a-z]{2,})?$|\\b(?:in|near|at|around|from) ${esc(c)}\\b`).test(t)) return { city: c };
  }
  return {};
}

function findStateInText(text: string): string | undefined {
  for (const [code, name] of Object.entries(STATE_NAMES)) {
    // "Washington Ave" or "Virginia St" is a street, not a state.
    if (name.length > 2 && new RegExp(`\\b${name}\\b(?! (?:st|street|ave|avenue|blvd|rd|road|way|dr|park|square)\\b)`, 'i').test(text)) return code;
  }
  const code = text.match(/[A-Za-z]+,\s*([A-Z]{2})\b/)?.[1];   // "Portland, OR"
  return code && STATE_NAMES[code] ? code : undefined;
}

const STATE_NAMES: Record<string, string> = {
  AL: 'Alabama', AK: 'Alaska', AZ: 'Arizona', AR: 'Arkansas', CA: 'California', CO: 'Colorado', CT: 'Connecticut',
  DE: 'Delaware', DC: 'DC', FL: 'Florida', GA: 'Georgia', HI: 'Hawaii', ID: 'Idaho', IL: 'Illinois', IN: 'Indiana',
  IA: 'Iowa', KS: 'Kansas', KY: 'Kentucky', LA: 'Louisiana', ME: 'Maine', MD: 'Maryland', MA: 'Massachusetts',
  MI: 'Michigan', MN: 'Minnesota', MS: 'Mississippi', MO: 'Missouri', MT: 'Montana', NE: 'Nebraska', NV: 'Nevada',
  NH: 'New Hampshire', NJ: 'New Jersey', NM: 'New Mexico', NY: 'New York', NC: 'North Carolina', ND: 'North Dakota',
  OH: 'Ohio', OK: 'Oklahoma', OR: 'Oregon', PA: 'Pennsylvania', RI: 'Rhode Island', SC: 'South Carolina',
  SD: 'South Dakota', TN: 'Tennessee', TX: 'Texas', UT: 'Utah', VT: 'Vermont', VA: 'Virginia', WA: 'Washington',
  WV: 'West Virginia', WI: 'Wisconsin', WY: 'Wyoming',
};

async function translateTo(lang: string, text: string): Promise<string> {
  if (!text || !lang) return text;
  try {
    return await chat([{ role: 'user', content: `Translate into ${lang}. Keep the meaning exactly and keep it simple. Output only the translation.

${text}` }], { maxTokens: 160 }) || text;
  } catch { return text; }   // English rather than nothing
}

// Backstop for abuse aimed at Mandy, in case the model misses it.
const ABUSE = /\b(?:(?:fuck|screw) (?:you|off)|you(?:'?re| are) (?:so )?(?:stupid|useless|dumb|an idiot|worthless|a bitch)|(?:stupid|dumb|useless) (?:bot|ai|robot|app))\b/i;

const NEED_WORDS: Record<string, string> = { shelter: 'shelter', food: 'food', health: 'health care', resources: 'showers and other help', work: 'work' };

export async function respond(all: Listing[], history: ChatMessage[], ctx: Context = {}): Promise<BotReply> {
  const last = history.filter(m => m.role === 'user').at(-1)?.content ?? '';
  const convo = history.slice(-8).map(m => `${m.role === 'user' ? 'Person' : 'Helper'}: ${m.content}`).join('\n');
  const state: ConvoState = { locationAsks: Math.max(0, Math.min(10, Number(ctx.state?.locationAsks) || 0)) };

  // 1. Understand the message.
  let q: ReturnType<typeof clean>;
  let modelUp = true;
  try {
    q = clean(JSON.parse(await chat([{ role: 'system', content: EXTRACT_PROMPT }, { role: 'user', content: convo }], { json: true })));
  } catch {
    modelUp = false;
    q = { ...clean({}), ...keywordSearch(history.filter(m => m.role === 'user').map(m => m.content).join(' ')) };
  }
  // The small model sometimes misses the obvious, so plain code fills the gaps.
  q.type ??= keywordSearch(last).type;
  if (!q.zip && !q.city) Object.assign(q, findPlaceInText(all, last));
  if (!q.state) q.state = findStateInText(last);

  // Safety: fixed patterns first, then the model's safety question, then its danger field.
  const kind = crisisKind(last) ?? (modelUp && last ? await safetyCheck(last) : null) ?? q.danger ?? null;
  const lang0 = q.language && !/english/i.test(q.language) ? q.language : '';
  // Crisis words in Spanish are fixed text, never the model's translation.
  const spanish = Boolean(kind) && (/^es\b|spanish|espa[nñ]ol/i.test(lang0) || isSpanishCrisis(last));
  const box = crisisBox(kind, spanish);
  const safety = safetyMessage(kind, spanish);
  const safetyWords = spanish ? safety : await translateTo(lang0, safety);
  const allUser = history.filter(m => m.role === 'user').map(m => m.content).join(' ');
  const immigration = aboutImmigration(last) || (last && q.immigration) || (aboutImmigration(allUser) && !q.type);

  // Human behavior as it is: some messages aren't looking for a service. Safety always
  // comes first, and someone asking for help is never treated as abuse.
  if (!kind && !q.type && !immigration) {
    const intent = ABUSE.test(last) ? 'abuse' : q.intent;
    if (intent === 'abuse') {
      return { reply: await translateTo(lang0, "I'm here if you need help finding shelter, food, or other resources."),
        note: await translateTo(lang0, DIGNITY_NOTE), results: [], alerts: [], crisis: [], state };
    }
    if (intent === 'off_topic') {
      return { reply: await translateTo(lang0, "I'm only able to help find resources like shelter, food, and health care. What can I help you find?"),
        results: [], alerts: [], crisis: [], state };
    }
    if (intent === 'wants_to_talk' && !q.shared_situation) {
      return { reply: await translateTo(lang0, "I'm an automated helper, so I'm not the best one to talk with. There are real people ready to listen right now, or I can help you find resources."),
        choices: TALK_CHOICES, results: [], alerts: [], crisis: [], state };
    }
  }

  // Small models miss this sometimes, so code catches the common ways people say it too.
  if (/homeless|unhoused|no ?where to (go|stay|sleep)|no place to (go|stay|sleep)|kicked out|evicted|sleeping (outside|outdoors|in my car|on the street)|lost my (home|house|apartment|place)|don'?t know what to do/i.test(last)) {
    q.shared_situation = true;
  }
  // Someone who only says "I'm homeless" hasn't asked for anything yet: ask, don't assume.
  const asked = history.filter(m => m.role === 'user').map(m => m.content).join(' ');
  if (q.shared_situation && (!q.language || /english/i.test(q.language))
      && !/shelter|bed|sleep|stay|food|eat|hungry|meal|doctor|clinic|health|shower|laundry|job|work/i.test(asked)) {
    q.type = undefined;
  }
  // A storm, fire, or flood coming: they need a safe place, so look for emergency shelters.
  const emergency = /evacuat\w*|hurricane|tropical storm|storm surge|flood\w*|wildfire|fire evac|tornado|hurac[aá]n|inundaci\w*|evacuar/i.test(allUser);
  if (emergency && !q.type) q.type = 'shelter';

  // 2. Work out a general area: device location, then zip, then a nearby place, then city.
  if (ctx.coords) {
    const area = await coordsToArea(ctx.coords.lat, ctx.coords.lon);
    if (area) Object.assign(q, { zip: area.zip ?? q.zip, city: area.city ?? q.city, state: area.state ?? q.state });
  } else if (!q.zip && q.place && (q.city || q.state)) {
    // Only look up a place once we know the city, or "a corner store" could match anywhere.
    const area = await placeToArea(q.place, q.city, q.state);
    if (area) Object.assign(q, { zip: area.zip ?? q.zip, city: q.city ?? area.city, state: q.state ?? area.state });
  }
  const where = inferState(all, q);
  if (where.choices.length && q.city) {
    // Same city name in several states: ask which one, rather than send someone to the wrong state.
    const city = q.city.replace(/\b\w/g, c => c.toUpperCase());
    const options = where.choices.map(s => `${city}, ${STATE_NAMES[s] ?? s}`);
    const ask = `Which state is that? For example, ${options.slice(0, -1).join(', ')} or ${options.at(-1)}.`;
    return { reply: [safetyWords, await translateTo(lang0, ask)].filter(Boolean).join(' '), results: [], alerts: [], ...box, state };
  }
  q.state = where.state;
  const hasPlace = Boolean(q.zip || q.city);
  const night = ctx.hour !== undefined && (ctx.hour >= 17 || ctx.hour < 7);

  // People targeted or detained by immigration enforcement, and their pets: hand-checked
  // national and state resources, plus any local ShelterApp listings that help.
  if (immigration) {
    const pets = aboutPets(allUser);
    const resources = specialResources(pets ? ['immigration', 'pets'] : ['immigration'], q.state);
    const localRe = pets ? /immigra|refugee|asylum|legal|pet|animal|veterinar/i : /immigra|refugee|asylum|legal/i;
    const local = hasPlace ? all.filter(l => localRe.test(`${l.name} ${l.details}`)
      && ((q.zip && l.zip.slice(0, 3) === q.zip.slice(0, 3)) || (q.city && l.city.toLowerCase() === q.city))).slice(0, 5) : [];
    const parts = [
      "I'm so sorry. Being targeted, or having someone you love taken, is frightening. Here are organizations that can help. I don't save anything you tell me.",
      pets ? 'If a pet was left behind, local animal shelters or humane societies may be able to help with temporary care.' : '',
      hasPlace ? '' : "If you tell me your city or state, I can show local help too.",
    ].filter(Boolean).join(' ');
    return { reply: [safetyWords, await translateTo(lang0, parts)].filter(Boolean).join(' '), resources, results: local, alerts: [], ...box, state };
  }

  // 3. Search (facts come only from here).
  const results = hasPlace && q.type ? searchListings(all, { ...q, night }) : [];
  const alerts = hasPlace ? await alertsFor(q.state) : [];
  // Emergency shelters when there's a disaster: FEMA's live list (pet-friendly first if they
  // have pets), and always the Red Cross map and 211, since counties often report late.
  const disaster = hasPlace && q.type === 'shelter' && (emergency || alerts.length > 0);
  const emergencyShelters = disaster ? await openShelters(q.state, aboutPets(allUser)) : [];
  const disasterResources = disaster ? [...emergencyShelters, ...DISASTER_HELP] : undefined;

  // 4. Decide what to say, following the conversation guide. The model writes only the
  //    empathy line; every fact, next step, question and safety line is fixed text, so
  //    the wording is always the one people with frontline expertise chose.
  const angry = q.feeling === 'angry' || q.intent === 'venting';
  const empathy = kind === 'suicide' ? ''   // the fixed safety message already speaks to them
    : angry
      ? 'Calmly name their frustration without judging it, for example that being turned away again and again is exhausting.'
      : q.shared_situation || q.feeling
        ? 'In your own caring words, acknowledge what they shared, then say that it is really hard. For example: "Thank you for telling me. Not having a place to go is really hard."'
        : '';
  const need = q.type ? NEED_WORDS[q.type] : '';
  let statement = '';
  let question = '';
  let askedLocation = false;
  if (!q.type) {
    question = 'Are you looking for shelter, or is there something else you need first, like food?';
  } else if (!hasPlace && state.locationAsks >= 2) {
    statement = `That's okay. Whenever you can tell me a city, I can look. You can also call or text 211 any time for help finding ${need}.`;
  } else if (!hasPlace) {
    state.locationAsks++;
    askedLocation = true;
    question = state.locationAsks === 1
      ? "What city are you in? If you know a street, a cross street, or a place nearby, like a store or bus stop, that helps too. I don't save it."
      : `Is there a street name or a place you can see nearby, like a store, library, church, or bus stop? You can also tap "Use my location". It's never saved.`;
  } else if (results.length) {
    statement = `Here are places for ${need} near you. Please call ahead, since hours and space can change.`;
    if (alerts.length) statement += ' There are also official emergency alerts for your state above.';
    if (!q.zip) question = "If you tell me a street or a place nearby, I can show what's closest.";
  } else if (disaster) {
    statement = `I don't see a regular shelter listing near there.`;
  } else {
    statement = `I couldn't find ${need} listings near there yet. You can call or text 211, which helps anywhere in the US.`;
  }
  if (disaster) {
    statement += emergencyShelters.length
      ? (aboutPets(allUser) && emergencyShelters.some(r => /Accepts pets/.test(r.what))
        ? ' Emergency shelters are open in your state. The ones that take pets are listed first.'
        : ' Emergency shelters are open in your state, listed below.')
      : " I don't see emergency shelters posted for your state yet. Counties often post them late, so the Red Cross map and 211 below will have the newest list, including ones that take pets.";
    statement = statement.trim();
  }
  let choices: Choice[] | undefined;
  if (kind === 'suicide') {
    question = 'Would you like to talk with someone, or would you like help finding shelter, food, or something else? Either is okay.';
    choices = spanish ? TALK_CHOICES_ES : TALK_CHOICES;
    if (askedLocation) state.locationAsks--;   // the talk-or-help question replaced the location question
  }

  // A short, warm bridge before a location question, so it never feels like a form.
  let bridge = '';
  if (q.type && !hasPlace && !empathy && askedLocation && state.locationAsks === 1) {
    bridge = q.type === 'food' && /hungry|starving|haven'?t eaten|no food/i.test(last)
      ? "I'm sorry you're hungry. I can help you find food."
      : `I can help you find ${need}.`;
  }

  // 5. Write it: the model's empathy line (if any), with a fixed fallback.
  const lang = q.language && !/english/i.test(q.language) ? q.language : '';
  let caring = '';
  if (modelUp && empathy) {
    try {
      caring = await chat([
        { role: 'system', content: REPLY_PROMPT },
        { role: 'user', content: `Conversation:
${convo}

What to say: ${empathy} One or two sentences only.
Reply in the same language as the person's last message, which was: "${last.slice(0, 300)}"` },
      ], { maxTokens: 100 });
    } catch { modelUp = false; }
  }
  if (!caring && empathy) {
    caring = angry ? "It sounds like you've been turned away a lot. That's exhausting." : 'Thank you for telling me. That sounds really hard.';
  }
  const translate = async (text: string) => {
    if (!text || !lang || !modelUp) return text;
    try {
      return await chat([{ role: 'user', content: `Translate into ${lang}. Keep the meaning exactly and keep it simple. Output only the translation.\n\n${text}` }], { maxTokens: 160 }) || text;
    } catch { return text; }   // English rather than nothing
  };
  // Order: safety first, then the caring words, then the one question.
  // The small model sometimes answers in English anyway, so non-English replies are translated as a whole.
  const reply = [safetyWords, await translate(caring.trim()), await translate([statement, bridge, question].filter(Boolean).join(' '))].filter(Boolean).join(' ');
  return { reply, results, alerts, ...box, choices, resources: disasterResources, state };
}
