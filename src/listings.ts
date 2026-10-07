// ShelterApp listings: load them and search them. All facts shown to a person
// come from here, never from the AI model.
import { readFileSync, existsSync } from 'node:fs';

export interface Listing {
  name: string;
  address: string;
  city: string;
  state: string;
  zip: string;
  phone: string;
  website: string;
  types: string[];      // FOOD, SHELTER, HEALTH, RESOURCES, WORK
  who: string[];        // MEN, WOMEN, KIDS, SENIORS, DISABLED, FAMILIES, LGBT, ALL
  details: string;
  hours: string;
}

export interface SearchQuery {
  type?: string;        // food | shelter | health | resources | work
  zip?: string;
  city?: string;
  state?: string;
  who?: string;         // men | women | kids | seniors | disabled | families | lgbt
  keywords?: string;    // e.g. "pets", "showers"
  night?: boolean;      // evening or night: show places open 24/7 first
}

// "MONDAY08:00 AM - 05:00 PM; TUESDAY08:00 AM - 05:00 PM" -> "Mon-Tue 8:00 AM - 5:00 PM"
export function tidyHours(s: string): string {
  const parts = s.split(';').map(p => p.trim()).filter(Boolean);
  const days = parts.map(p => p.match(/^(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)\s*(.*)$/i));
  if (!parts.length || days.some(d => !d)) return s.trim();
  const fmt = (t: string) => t.replace(/\b0(\d):/g, '$1:');
  const groups: { from: string; to: string; time: string }[] = [];
  for (const d of days as RegExpMatchArray[]) {
    const day = d[1][0] + d[1].slice(1, 3).toLowerCase(), time = fmt(d[2]);
    const g = groups.at(-1);
    if (g && g.time === time) g.to = day; else groups.push({ from: day, to: day, time });
  }
  return groups.map(g => `${g.from === g.to ? g.from : `${g.from}-${g.to}`} ${g.time}`).join('; ');
}

// Only real web links: "www.x.org" gets https://, and anything like "javascript:" is dropped.
export function safeUrl(s: string): string {
  const t = s.trim();
  if (/^https?:\/\/[^\s]+$/i.test(t)) return t;
  if (/^(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(\/\S*)?$/i.test(t)) return 'https://' + t;
  return '';
}

const split = (s: unknown) => String(s ?? '').split(',').map(x => x.trim().toUpperCase()).filter(Boolean);

// Reads either an audit-tool inventory export (real listings) or the small demo file.
// Only approved listings are used, and staff fields like `added_by` are dropped.
export function loadListings(path: string): Listing[] {
  if (!existsSync(path)) return [];
  const raw = JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>[];
  return raw
    .filter(r => !r.approval || r.approval === 'Approved')
    .map(r => ({
      name: String(r.name ?? ''),
      address: /[a-z0-9]/i.test(String(r.address1 ?? '')) ? String(r.address1) : '',   // drop "." placeholders
      city: String(r.city ?? ''),
      state: String(r.state ?? '').toUpperCase(),
      zip: String(r.zip ?? '').slice(0, 5),
      phone: /[1-9]/.test(String(r.phone ?? '')) ? String(r.phone) : '',               // drop (000) 000-0000
      website: safeUrl(String(r.website ?? '')),
      types: split(r.services ?? (r.type as string[] | undefined)?.join(',')),
      who: split(r.categories ?? (r.category as string[] | undefined)?.join(',')),
      details: String(r.service_details ?? r.serviceSummary ?? ''),
      hours: tidyHours(String(r.schedule ?? '')) || 'Call for hours',
    }))
    .filter(l => l.name && l.state);
}

// Ranks listings: same zip first, then same area (first 3 zip digits), then same city.
// Returns nothing rather than far-away results, so no one is sent across the state.
export function searchListings(all: Listing[], q: SearchQuery, limit = 5): Listing[] {
  const type = q.type?.toUpperCase();
  const who = q.who?.toUpperCase();
  const city = q.city?.trim().toLowerCase();
  const state = q.state?.trim().toUpperCase();
  const words = (q.keywords ?? '').toLowerCase().split(/\W+/).filter(w => w.length > 2);

  const scored: { l: Listing; score: number }[] = [];
  for (const l of all) {
    if (type && !l.types.includes(type)) continue;
    if (who && !(l.who.includes(who) || l.who.includes('ALL'))) continue;
    if (state && l.state !== state) continue;

    let score = 0;
    if (q.zip && l.zip === q.zip) score = 3;
    else if (q.zip && l.zip.slice(0, 3) === q.zip.slice(0, 3)) score = 2;
    else if (city && l.city.toLowerCase() === city) score = 2;
    if (score === 0) continue;

    const text = `${l.name} ${l.details}`.toLowerCase();
    score += words.filter(w => text.includes(w)).length * 0.5;
    // At night a place open 24/7 nearby beats a daytime-only place a few blocks closer.
    if (q.night && /24\s*\/\s*7|24 hours/i.test(l.hours)) score += 1.5;
    scored.push({ l, score });
  }
  return scored.sort((a, b) => b.score - a.score).slice(0, limit).map(s => s.l);
}
