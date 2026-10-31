// The model's safety question, using a stand-in model so these stay fast and repeatable.
// They check the wiring only. How well the real model does is in CONVERSATION-GUIDE.md.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';

let risk = '{"risk":"none"}';
let understood = '{"language":"English"}';
const fake = createServer(async (req, res) => {
  let body = '';
  for await (const chunk of req) body += chunk;
  const content = body.includes('Decide if the person may be at risk') ? risk : understood;
  res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ choices: [{ message: { content } }] }));
});
await new Promise<void>(done => fake.listen(0, '127.0.0.1', done));
process.env.LLM_BASE_URL = `http://127.0.0.1:${(fake.address() as { port: number }).port}/v1`;
const { respond } = await import('../src/bot.ts');
after(() => fake.close());

const missedByWordList = 'a guy keeps coming into my tent at night';
const user = (content: string) => [{ role: 'user' as const, content }];

test('the safety question shows crisis lines the word list would miss', async () => {
  risk = '{"risk":"danger"}';
  const r = await respond([], user(missedByWordList));
  assert.equal(r.crisis.length, 3);
  assert.match(r.reply, /^You deserve to be safe\./);
});

test('a suicide answer gets the fixed message and the choice of who to talk to', async () => {
  risk = '{"risk":"suicide"}';
  const r = await respond([], user('tonight is my last night'));
  assert.match(r.reply, /^I'm really glad you told me\./);
  assert.ok(r.choices?.some(c => c.url?.includes('988')));
});

test('"none" and nonsense answers add nothing and break nothing', async () => {
  for (const answer of ['{"risk":"none"}', 'not json at all', '']) {
    risk = answer;
    assert.equal((await respond([], user('I need a shower'))).crisis.length, 0, answer);
  }
});

test('a false alarm still gets the help they asked for', async () => {
  risk = '{"risk":"danger"}';
  const seattle = { name: 'Seattle Pantry', address: '1 Main St', city: 'Seattle', state: 'WA', zip: '98101', phone: '', website: '', types: ['FOOD'], who: ['ALL'], details: '', hours: 'Call for hours' };
  const r = await respond([seattle], user('I need food in Seattle'));
  assert.equal(r.crisis.length, 3);
  assert.equal(r.results[0]?.name, 'Seattle Pantry');
});

test('Spanish from the model gets the fixed Spanish crisis words', async () => {
  risk = '{"risk":"danger"}';
  understood = '{"language":"Spanish"}';
  const r = await respond([], user('un señor entra de noche a mi carpa'));
  assert.match(r.reply, /^Mereces estar a salvo\./);
  assert.match(r.crisis[0].how, /presiona 2/);
  understood = '{"language":"English"}';
});
