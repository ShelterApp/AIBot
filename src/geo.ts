// Turns "the corner store on MLK, Tacoma" or a rough device location into a zip
// code, using OpenStreetMap's free Nominatim service. Only the place words (or
// coordinates rounded to about 1 km) are sent; nothing about the person.
// Nominatim allows about 1 request per second; production should self-host or
// use another free geocoder.

export interface Area { zip?: string; city?: string; state?: string }

const STATES: Record<string, string> = {
  Alabama: 'AL', Alaska: 'AK', Arizona: 'AZ', Arkansas: 'AR', California: 'CA', Colorado: 'CO', Connecticut: 'CT',
  Delaware: 'DE', 'District of Columbia': 'DC', Florida: 'FL', Georgia: 'GA', Hawaii: 'HI', Idaho: 'ID', Illinois: 'IL',
  Indiana: 'IN', Iowa: 'IA', Kansas: 'KS', Kentucky: 'KY', Louisiana: 'LA', Maine: 'ME', Maryland: 'MD',
  Massachusetts: 'MA', Michigan: 'MI', Minnesota: 'MN', Mississippi: 'MS', Missouri: 'MO', Montana: 'MT',
  Nebraska: 'NE', Nevada: 'NV', 'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
  'North Carolina': 'NC', 'North Dakota': 'ND', Ohio: 'OH', Oklahoma: 'OK', Oregon: 'OR', Pennsylvania: 'PA',
  'Rhode Island': 'RI', 'South Carolina': 'SC', 'South Dakota': 'SD', Tennessee: 'TN', Texas: 'TX', Utah: 'UT',
  Vermont: 'VT', Virginia: 'VA', Washington: 'WA', 'West Virginia': 'WV', Wisconsin: 'WI', Wyoming: 'WY',
};

const cache = new Map<string, Area | null>();
let last = 0;

async function nominatim(path: string): Promise<any> {
  const wait = last + 1100 - Date.now();
  if (wait > 0) await new Promise(r => setTimeout(r, wait));
  last = Date.now();
  const r = await fetch(`https://nominatim.openstreetmap.org/${path}`, {
    headers: { 'User-Agent': 'ShelterApp-AIBot/0.1 (github.com/ShelterApp/AIBot)' },
    signal: AbortSignal.timeout(8000),
  });
  if (!r.ok) throw new Error(`geocoder HTTP ${r.status}`);
  return r.json();
}

function toArea(a: any): Area | null {
  if (!a) return null;
  const zip = String(a.postcode ?? '').match(/\d{5}/)?.[0];
  const city = a.city ?? a.town ?? a.village ?? a.hamlet;
  const state = STATES[a.state] ?? undefined;
  return zip || city ? { zip, city: city?.toLowerCase(), state } : null;
}

// "5th and Pine" + "seattle" -> { zip: "98101", city: "seattle", state: "WA" }
export async function placeToArea(place: string, city?: string, state?: string): Promise<Area | null> {
  const q = [place.replace(/\band\b|&/gi, ' & '), city, state].filter(Boolean).join(', ');
  const key = 'q:' + q.toLowerCase();
  if (cache.has(key)) return cache.get(key)!;
  try {
    const rows = await nominatim('search?' + new URLSearchParams({ q, format: 'jsonv2', addressdetails: '1', limit: '1', countrycodes: 'us' }));
    const area = toArea(rows?.[0]?.address);
    cache.set(key, area);
    return area;
  } catch {
    return null;
  }
}

// Device location, rounded to 2 decimals (about 1 km) before it leaves the server.
export async function coordsToArea(lat: number, lon: number): Promise<Area | null> {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  const la = lat.toFixed(2), lo = lon.toFixed(2), key = `c:${la},${lo}`;
  if (cache.has(key)) return cache.get(key)!;
  try {
    const row = await nominatim('reverse?' + new URLSearchParams({ lat: la, lon: lo, format: 'jsonv2', zoom: '14', addressdetails: '1' }));
    const area = toArea(row?.address);
    cache.set(key, area);
    return area;
  } catch {
    return null;
  }
}
