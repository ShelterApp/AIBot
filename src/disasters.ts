// Disaster mode: official alerts for the person's state, from free public feeds
// (FEMA and the National Weather Service). No key, no sign-up, nothing about the person is sent
// except the two-letter state code.

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

export async function alertsFor(state?: string): Promise<Alert[]> {
  if (!state || !/^[A-Z]{2}$/.test(state)) return [];
  const hit = cache.get(state);
  if (hit && Date.now() - hit.at < TEN_MIN) return hit.alerts;
  const results = await Promise.allSettled([weather(state), fema(state)]);
  const alerts = results.flatMap(r => (r.status === 'fulfilled' ? r.value : []));
  cache.set(state, { at: Date.now(), alerts });
  return alerts;
}
