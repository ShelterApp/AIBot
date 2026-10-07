// Local server for trying the assistant. Privacy: conversations are never written to
// disk or logs. The chat history lives only in the person's browser tab and is sent
// with each message, so the server keeps nothing between requests.
import { createServer } from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { loadListings } from './listings.ts';
import { respond } from './bot.ts';

if (existsSync('.env')) process.loadEnvFile('.env');

const PORT = Number(process.env.PORT ?? 8787);
// Real listings come from LISTINGS_PATH (set in .env); otherwise the fake demo listings.
const source = [process.env.LISTINGS_PATH, 'data/sample-services.json'].find(p => p && existsSync(p)) ?? 'data/sample-services.json';
const listings = loadListings(source);
console.log(`Loaded ${listings.length} listings from ${source}`);

const page = () => readFileSync('public/index.html');

createServer(async (req, res) => {
  if (req.method === 'GET' && (req.url === '/' || req.url === '/index.html')) {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }).end(page());
    return;
  }
  if (req.method === 'POST' && req.url === '/api/chat') {
    let body = '';
    for await (const chunk of req) {
      body += chunk;
      if (body.length > 50_000) { res.writeHead(413).end(); return; }
    }
    try {
      const parsed = JSON.parse(body);
      const { messages, state, hour, coords } = parsed && typeof parsed === 'object' ? parsed : {} as any;
      const history = (Array.isArray(messages) ? messages : [])
        .filter((m: any) => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string')
        .map((m: any) => ({ role: m.role, content: m.content.slice(0, 1000) }));
      const out = await respond(listings, history, {
        state,
        hour: Number.isInteger(hour) && hour >= 0 && hour < 24 ? hour : undefined,
        coords: coords && typeof coords === 'object' && Number.isFinite(coords.lat) && Number.isFinite(coords.lon) ? { lat: +coords.lat, lon: +coords.lon } : undefined,
      });
      res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(out));
    } catch (e) {
      console.error('chat error:', (e as Error).message);   // the error only, never the message text
      res.writeHead(500, { 'Content-Type': 'application/json' }).end(JSON.stringify({ error: 'Something went wrong. Please try again.' }));
    }
    return;
  }
  res.writeHead(404).end();
}).listen(PORT, () => console.log(`ShelterApp assistant: http://localhost:${PORT}`));
