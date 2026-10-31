// Conversations over several turns, using a stand-in model that answers the way the real
// small model did, and no outside services, so these stay fast and repeatable.
import { test, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';

let understood: Record<string, unknown> = {};
let caring = '';
let landing: Record<string, string> | null = null;
const lookups: string[] = [];

const fake = createServer(async (req, res) => {
  let body = '';
  for await (const chunk of req) body += chunk;
  const content = body.includes('Decide if the person may be at risk') ? '{"risk":"none"}'
    : body.includes('You read a conversation') ? JSON.stringify(understood)
      : body.includes('You are Mandy') ? caring
        : 'traducido';
  res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ choices: [{ message: { content } }] }));
});
await new Promise<void>(done => fake.listen(0, '127.0.0.1', done));
process.env.LLM_BASE_URL = `http://127.0.0.1:${(fake.address() as { port: number }).port}/v1`;

const realFetch = globalThis.fetch;
globalThis.fetch = (async (url: any, init: any) => {
  const u = String(url);
  if (u.includes('nominatim')) { lookups.push(decodeURIComponent(u)); return new Response(JSON.stringify(landing ? [{ address: landing }] : [])); }
  if (u.includes('weather.gov') || u.includes('fema.gov')) return new Response('{}');
  return realFetch(url, init);
}) as typeof fetch;

const { respond } = await import('../src/bot.ts');
after(() => fake.close());
beforeEach(() => { caring = ''; landing = null; lookups.length = 0; });

const L = (name: string, city: string, state: string, zip: string, types: string[]) =>
  ({ name, address: '1 Main St', city, state, zip, phone: '', website: '', types, who: ['ALL'], details: '', hours: 'Call for hours' });
const all = [
  L('Tacoma Shelter', 'Tacoma', 'WA', '98405', ['SHELTER']), L('Hilltop Shelter', 'Tacoma', 'WA', '98402', ['SHELTER']),
  L('Tacoma Pantry', 'Tacoma', 'WA', '98405', ['FOOD']), L('Portland ME Shelter', 'Portland', 'ME', '04101', ['SHELTER']),
  L('Portland ME Beds', 'Portland', 'ME', '04102', ['SHELTER']), L('Portland OR Shelter', 'Portland', 'OR', '97201', ['SHELTER']),
  L('Portland OR Beds', 'Portland', 'OR', '97202', ['SHELTER']),
];

// Plays turns the way the page does: the whole history, plus the state it was handed back.
async function play(turns: [string, Record<string, unknown>][]) {
  const history: { role: 'user' | 'assistant'; content: string }[] = [];
  let state: any = { locationAsks: 0 };
  const replies = [];
  for (const [text, model] of turns) {
    understood = model;
    history.push({ role: 'user', content: text });
    const r = await respond(all, history, { state });
    state = r.state;
    history.push({ role: 'assistant', content: r.reply });
    replies.push(r);
  }
  return replies;
}

test('remembers what they asked for: the conversation that first showed this', async () => {
  caring = 'Thank you for telling me. Finding shelter is really hard. I can help you find shelter.';
  const r = await play([
    ['O abrigo', { type: 'shelter', language: 'spanish' }],
    ['I am scared and have nowhere to go', { type: 'shelter', intent: 'need_help', language: 'English' }],
    ['I need housing', { type: 'shelter', intent: 'need_help', language: 'English' }],
    ['All I know is Main and 10ath', { type: 'shelter', shared_situation: true, intent: 'need_help', language: 'English' }],
  ]);
  for (const turn of r.slice(1)) assert.doesNotMatch(turn.reply, /Are you looking for shelter/, turn.reply);
  assert.equal(r[3].state.need, 'shelter');
  assert.match(r[3].reply, /Whenever you can tell me a city/);
  assert.doesNotMatch(r[3].reply, /Thank you for telling me|really hard/);
});

test('someone who only says they are homeless is still asked, not assumed', async () => {
  const [r] = await play([['I am homeless', { type: 'shelter', shared_situation: true, language: 'English' }]]);
  assert.match(r.reply, /Are you looking for shelter/);
  assert.equal(r.state.need, undefined);
});

test('a new request replaces the old one', async () => {
  const r = await play([['I need shelter', { type: 'shelter' }], ['actually I need food in Tacoma', { type: 'food', city: 'Tacoma' }]]);
  assert.equal(r[1].state.need, 'food');
  assert.equal(r[1].results[0]?.name, 'Tacoma Pantry');
});

test('"housing" counts as asking for shelter, even if the model misses it', async () => {
  const [r] = await play([['I need housing', {}]]);
  assert.match(r.reply, /What city are you in\?/);
});

test('a city said earlier still counts when the model leaves it out', async () => {
  const r = await play([['I am in Tacoma', { language: 'English' }], ['I need food', { type: 'food' }]]);
  assert.equal(r[1].results[0]?.name, 'Tacoma Pantry');
});

test('the model\'s own offer never clashes with the next step', async () => {
  caring = 'Thank you for telling me. Not having a place to go is really hard. I can help you find shelter.';
  const [r] = await play([['I am homeless', { shared_situation: true, language: 'English' }]]);
  assert.match(r.reply, /^Thank you for telling me\. Not having a place to go is really hard\. Are you looking for shelter/);
});

test('if the model only wrote an offer, the fixed caring line is used', async () => {
  caring = 'We can look for shelter options for you.';
  const [r] = await play([['I am homeless', { shared_situation: true, language: 'English' }]]);
  assert.match(r.reply, /^Thank you for telling me\. That sounds really hard\./);
});

test('a street lookup that lands in another city is ignored', async () => {
  landing = { postcode: '74127', city: 'Tulsa', state: 'Oklahoma' };
  const [r] = await play([['Main and 10th', { type: 'shelter', city: 'Tacoma', place: 'Main and 10th' }]]);
  assert.match(lookups[0], /tacoma.*WA/i);
  assert.ok(r.results.length > 0 && r.results.every(l => l.state === 'WA'));
});

test('a street lookup in the right city shows what is closest first', async () => {
  landing = { postcode: '98402', city: 'Tacoma', state: 'Washington' };
  const [r] = await play([['Pine and 5th', { type: 'shelter', city: 'Tacoma', place: 'Pine and 5th' }]]);
  assert.equal(r.results[0]?.name, 'Hilltop Shelter');
});

test('a city in two states asks which one before looking up a street', async () => {
  const [r] = await play([['Main and 10th', { type: 'shelter', city: 'Portland', place: 'Oak and 3rd' }]]);
  assert.equal(lookups.length, 0);
  assert.match(r.reply, /Which state is that\?/);
});
