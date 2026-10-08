// Hand-checked resources for situations listings don't cover well, such as people
// targeted or detained by immigration enforcement, and their pets. Each entry records
// its source and the date it was verified; re-check them often, since hotlines change.
import { readFileSync, existsSync } from 'node:fs';

export interface SpecialResource {
  category: 'immigration' | 'pets' | 'disaster';
  area: string;          // "US" or a two-letter state
  name: string;
  what: string;
  phone?: string;
  email?: string;
  url?: string;
  source: string;
  verified: string;
}

const PATH = 'data/special-resources.json';
const all: SpecialResource[] = existsSync(PATH) ? JSON.parse(readFileSync(PATH, 'utf8')) : [];

// State resources first (they're closest), then national ones.
export function specialResources(categories: string[], state?: string): SpecialResource[] {
  const pick = all.filter(r => categories.includes(r.category) && (r.area === 'US' || r.area === state));
  return pick.sort((a, b) => Number(b.area === state) - Number(a.area === state));
}

// Recognizes immigration enforcement, carefully: "ICE" only in capitals or with agent words,
// so "ice cream" or "icy roads" don't count.
const IMMIGRATION = /\bICE\b|\bice (agents?|officers?|raids?|took|came|detained|arrested)|\b(by|from) ice\b|la migra|immigration (agents?|officers?|raids?|detention|court|lawyer|help)|deport\w*|detenid\w*|deportad\w*|inmigraci\w*|undocumented|asylum|green card/;

export function aboutImmigration(text: string): boolean {
  return IMMIGRATION.test(text) || /\bimmigra\w*|deport\w*|la migra/i.test(text);
}

export function aboutPets(text: string): boolean {
  return /\b(pets?|dogs?|cats?|puppy|kitten|perro|gato|mascota)\b/i.test(text);
}
