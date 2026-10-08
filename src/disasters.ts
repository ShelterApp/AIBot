// Disaster mode: official alerts for the person's state, from free public feeds
// (FEMA and the National Weather Service). No key, no sign-up, nothing about the person is sent
// except the two-letter state code.

import type { SpecialResource } from './special.ts';

export interface Alert { title: string; detail: string; source: string; url: string }

const cache = new Map<string, { at: number; alerts: Alert[] }>();
const TEN_MIN = 10 * 60 * 1000;

async function getJson(url: string): Promise<any> {
  const r = await fetch(url, {
    headers: { 'User-Agent': 'ShelterApp-AIBot (github.com/ShelterApp/AIBot)', Accept: 'application/json' },
    signal: AbortSignal.timeout(8000),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function fema(state: string): Promise<Alert[]> {
  const since = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const url = 'https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries?' + new URLSearchParams({
    $filter: `state eq '${state}' and declarationDate ge '${since}'`,
    $select: 'disasterNumber,declarationTitle,incidentType,declarationDate',
    $orderby: 'declarationDate desc',
    $top: '20',
  });
  const rows = (await getJson(url)).DisasterDeclarationsSummaries ?? [];
  const seen = new Set<number>();
  return rows.filter((r: any) => !seen.has(r.disasterNumber) && seen.add(r.disasterNumber)).slice(0, 3)
    .map((r: any) => ({
      title: `FEMA disaster declared: ${r.declarationTitle}`,
      detail: `${r.incidentType}, declared ${String(r.declarationDate).slice(0, 10)}`,
      source: 'FEMA',
      url: `https://www.fema.gov/disaster/${r.disasterNumber}`,
    }));
}

async function weather(state: string): Promise<Alert[]> {
  const data = await getJson(`https://api.weather.gov/alerts/active?area=${state}&severity=Extreme,Severe`);
  const seen = new Set<string>();
  return (data.features ?? []).map((f: any) => f.properties)
    .filter((p: any) => !seen.has(p.event) && seen.add(p.event)).slice(0, 3)
    .map((p: any) => ({
      title: p.event,
      detail: String(p.areaDesc ?? '').split(';').slice(0, 4).join(';'),
      source: 'National Weather Service',
      url: 'https://www.weather.gov/',
    }));
}

// ------------------------------------------------------------- emergency shelters
// FEMA's Open Shelters feed (Red Cross and partners report into it). Free, no key, and it
// says which shelters take pets. Counties often report late, so when it's empty we say so
// and give the places that always know: the Red Cross shelter finder and 211.

const SHELTERS_URL = 'https://gis.fema.gov/arcgis/rest/services/NSS/OpenShelters/FeatureServer/0/query';
const shelterCache = new Map<string, { at: number; list: SpecialResource[] }>();

export const DISASTER_HELP: SpecialResource[] = [
  { category: 'disaster', area: 'US', name: 'Red Cross: find an open shelter',
    what: 'Map of emergency shelters open right now. Many counties list shelters here before anywhere else.',
    url: 'https://www.redcross.org/get-help/disaster-relief-and-recovery-services/find-an-open-shelter.html',
    source: 'redcross.org', verified: '2026-10-08' },
  { category: 'disaster', area: 'US', name: '211',
    what: 'Call or text 211 to ask which shelters near you are open, including ones that take pets.',
    phone: '211', url: 'https://www.211.org', source: '211.org', verified: '2026-10-08' },
  { category: 'disaster', area: 'US', name: 'Disaster Distress Helpline',
    what: 'Free, 24/7 support by call or text after a disaster, in many languages.',
    phone: '1-800-985-5990', source: 'SAMHSA', verified: '2026-10-08' },
];

// Turns FEMA records into cards. Pet-friendly shelters go first when the person has pets.
export function toShelters(features: any[], petsFirst = false): SpecialResource[] {
  const list = features.map(f => f.attributes ?? f)
    .filter(a => String(a.shelter_status ?? '').toUpperCase() === 'OPEN' && a.shelter_name)
    .map(a => {
      const pets = Boolean(a.pet_accommodations_code) && !/^(no|none|n)$/i.test(String(a.pet_accommodations_code));
      const place = [a.address, a.city, [a.state, a.zip].filter(Boolean).join(' ')].filter(Boolean).join(', ');
      return {
        category: 'disaster' as const, area: String(a.state ?? ''),
        name: String(a.shelter_name).replace(/\b\w/g, (c: string) => c.toUpperCase()),
        what: `Emergency shelter, open now. ${place}.${pets ? ' Accepts pets: bring a carrier, leash, food, and vaccine records.' : ''} Please call 211 to confirm before you go.`,
        source: 'FEMA Open Shelters', verified: 'live', pets,
      };
    });
  if (petsFirst) list.sort((x, y) => Number(y.pets) - Number(x.pets));
  return list.map(({ pets, ...r }) => r);
}

export async function openShelters(state?: string, petsFirst = false): Promise<SpecialResource[]> {
  if (!state || !/^[A-Z]{2}$/.test(state)) return [];
  const hit = shelterCache.get(state);
  let list = hit && Date.now() - hit.at < TEN_MIN ? hit.list : null;
  if (!list) {
    try {
      const data = await getJson(SHELTERS_URL + '?' + new URLSearchParams({
        where: `state='${state}' AND shelter_status='OPEN'`, outFields: '*', f: 'json', resultRecordCount: '50' }));
      list = toShelters(data.features ?? []);
      shelterCache.set(state, { at: Date.now(), list });
    } catch { list = []; }   // the fallback help still shows
  }
  if (!petsFirst) return list.slice(0, 6);
  return [...list].sort((x, y) => Number(/Accepts pets/.test(y.what)) - Number(/Accepts pets/.test(x.what))).slice(0, 6);
}

export async function alertsFor(state?: string): Promise<Alert[]> {
  if (!state || !/^[A-Z]{2}$/.test(state)) return [];
  const hit = cache.get(state);
  if (hit && Date.now() - hit.at < TEN_MIN) return hit.alerts;
  const results = await Promise.allSettled([weather(state), fema(state)]);
  const alerts = results.flatMap(r => (r.status === 'fulfilled' ? r.value : []));
  cache.set(state, { at: Date.now(), alerts });
  return alerts;
}
