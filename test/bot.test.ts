// Conversation tests with the AI model switched off, so they're fast and repeatable.
// They check the parts that must never depend on the model: safety, fixed wording,
// abuse handling, and never guessing the wrong state.
import { test } from 'node:test';
import assert from 'node:assert/strict';

process.env.LLM_BASE_URL = 'http://127.0.0.1:9/v1';   // nothing listens here: the model is "down"
const { respond } = await import('../src/bot.ts');

const L = (name: string, city: string, state: string, zip: string, types = ['FOOD']) =>
  ({ name, address: '1 Main St', city, state, zip, phone: '', website: '', types, who: ['ALL'], details: '', hours: 'Call for hours' });
const all = [
  L('Portland ME Pantry', 'Portland', 'ME', '04101'), L('Portland ME Kitchen', 'Portland', 'ME', '04102'),
  L('Portland OR Pantry', 'Portland', 'OR', '97201'), L('Portland OR Meals', 'Portland', 'OR', '97202'),
  L('Seattle Pantry', 'Seattle', 'WA', '98101'),
];
const user = (content: string) => [{ role: 'user' as const, content }];

test('crisis lines always show, even with the model down', async () => {
  const r = await respond(all, user('I am so tired and nowhere is open. I might as well be dead'));
  assert.equal(r.crisis.length, 3);
  assert.match(r.reply, /^I'm really glad you told me\. You matter\./);
  assert.ok(r.choices?.some(c => c.url?.includes('988')));
});

test('abuse gets the dignity note and an offer of help', async () => {
  const r = await respond(all, user('fuck you, you stupid bot'));
  assert.equal(r.note, 'ShelterApp believes in treating all beings with dignity and respect.');
  assert.match(r.reply, /help finding shelter, food, or other resources/);
});

test('hunger gets care before the location question, never "where are you"', async () => {
  const r = await respond(all, user('I am hungry'));
  assert.match(r.reply, /^I'm sorry you're hungry\. I can help you find food\. What city are you in\?/);
  assert.doesNotMatch(r.reply, /where are you/i);
});

test('a zip code settles the state', async () => {
  const r = await respond(all, user('food in 97201'));
  assert.ok(r.results.length > 0 && r.results.every(l => l.state === 'OR'));
});
test('asks which state for a city in two states, instead of guessing', async () => {
  const r = await respond(all, user('I need food in Portland'));
  assert.match(r.reply, /Which state is that\? For example, Portland, (Maine|Oregon) or Portland, (Maine|Oregon)\./);
  assert.equal(r.results.length, 0);
});

test('a state name settles it, and a street named after a state does not', async () => {
  const r = await respond(all, user('I need food in Portland, Oregon'));
  assert.ok(r.results.length > 0 && r.results.every(l => l.state === 'OR'));
  const st = await respond(all, user('I need food in Portland near Washington St'));
  assert.match(st.reply, /Which state is that\?/);
});

test('a reply that is just a city name is understood', async () => {
  const r = await respond(all, [{ role: 'user', content: 'I am hungry' }, { role: 'assistant', content: 'What city are you in?' }, { role: 'user', content: 'seattle' }], { state: { locationAsks: 1 } });
  assert.equal(r.results[0]?.city, 'Seattle');
});

test('immigration help shows checked resources and no "you don\'t have to"', async () => {
  const r = await respond(all, user('ICE took my husband this morning'));
  assert.ok((r.resources ?? []).length >= 5);
  assert.doesNotMatch(r.reply, /don't have to/);
});

test('stays stateless and caps the location counter', async () => {
  const r = await respond(all, user('I need food'), { state: { locationAsks: 999 } });
  assert.ok(r.state.locationAsks <= 10);
});
