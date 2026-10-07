// Duplicate detection, ported from the ShelterApp audit tool (C:\audit\match_audit.py),
// including its 2026-06-21 fix: token overlap divides by the LONGER name, so one shared
// common word ("library", "health") can't make two different places look the same.

const NAME_STOP = /\b(the|inc|llc|of|a|and|center|centre|services?|clinic|healthcare|health|community|public|county|city)\b/g;

export function normName(s = ''): string {
  return s.toLowerCase().replace(/&/g, ' ').replace(/[^a-z0-9 ]+/g, ' ')
    .replace(NAME_STOP, ' ').replace(/\s+/g, ' ').trim();
}

// Similarity in [0, 1]: the better of a character-level ratio and token overlap.
export function nameSim(a: string, b: string): number {
  const x = normName(a), y = normName(b);
  if (!x || !y) return 0;
  const ta = new Set(x.split(' ')), tb = new Set(y.split(' '));
  const shared = [...ta].filter(t => tb.has(t)).length;
  const overlap = shared / Math.max(ta.size, tb.size);
  return Math.max(seqRatio(x, y), overlap);
}

// Same idea as Python's difflib ratio: 2 * matches / total length, via longest common subsequence.
function seqRatio(a: string, b: string): number {
  const dp: number[] = new Array(b.length + 1).fill(0);
  for (let i = 1; i <= a.length; i++) {
    let prev = 0;
    for (let j = 1; j <= b.length; j++) {
      const tmp = dp[j];
      dp[j] = a[i - 1] === b[j - 1] ? prev + 1 : Math.max(dp[j], dp[j - 1]);
      prev = tmp;
    }
  }
  return (2 * dp[b.length]) / (a.length + b.length);
}

export interface Place { name: string; city?: string; zip?: string; [k: string]: unknown }

// Possible duplicates of `item`, best first. A weak name match only counts when the
// place is in the same zip or city; otherwise the names must be nearly identical.
export function findDuplicates<T extends Place>(item: Place, existing: T[], threshold = 0.62) {
  const zip = (item.zip ?? '').slice(0, 5);
  const city = (item.city ?? '').trim().toLowerCase();
  const hits: { service: T; score: number }[] = [];
  for (const s of existing) {
    let score = nameSim(item.name, s.name);
    if (score < threshold) continue;
    const sameZip = zip && (s.zip ?? '').slice(0, 5) === zip;
    const sameCity = city && (s.city ?? '').trim().toLowerCase() === city;
    if (sameZip || sameCity) score += 0.15;
    else if (score < 0.9) continue;
    hits.push({ service: s, score: Math.min(1, score) });
  }
  return hits.sort((p, q) => q.score - p.score);
}
