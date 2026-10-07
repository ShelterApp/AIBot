// Crisis safety is fixed code first: if a message suggests danger, the crisis lines
// always show, whatever the model does. The model's own danger check (in bot.ts)
// is a second net for phrasings and languages these patterns miss.

// Every phrase must match whole words, so "coding" or "spend it" never trigger it.
const words = (parts: string[]) => new RegExp(`\\b(?:${parts.join('|')})\\b`, 'i');

const SUICIDE = words([
  'suicid\\w*', 'kill (?:my ?self|me)', 'end (?:it|it all|my life|things)', 'want to die', 'wanna die',
  '(?:might|may) as well (?:be )?dead', 'better off dead', 'rather be dead', 'wish (?:i|i\'?m|i was|i were) dead',
  '(?:don\'?t|do not) want to (?:live|be alive|be here|wake up)', 'no (?:reason|point) (?:to|in) (?:live|living|going on)',
  'not worth living', 'what\'?s the point(?: anymore| of (?:it|anything|living|trying|going on))',
  'can\'?t go on', 'give up on (?:life|everything)', 'no way out', 'disappear forever',
  'self[- ]?harm', 'hurt(?:ing)? myself', 'cut(?:ting)? myself',
  'overdos\\w*', 'o\\.?d\\.?\'?(?:d|ed|ing)', 'od\'?d',
]);

const DANGER = words([
  'abus\\w*', 'beat(?:s|ing)? me', 'hit(?:s|ting)? me', 'domestic violence', 'not safe', 'unsafe', 'in danger',
  'attack(?:ed|ing|s)? me', 'rap(?:e|ed|ing)', 'traffick\\w*', 'follow(?:ing|ed|s) me', 'threaten\\w*',
]);

export type CrisisKind = 'suicide' | 'danger' | null;

export interface CrisisLine { name: string; how: string }

export const CRISIS_LINES: CrisisLine[] = [
  { name: '988 Suicide and Crisis Lifeline', how: 'Call or text 988' },
  { name: 'Emergency', how: 'Call 911' },
  { name: 'National Domestic Violence Hotline', how: 'Call 1-800-799-7233 or text START to 88788' },
];

export function crisisKind(text: string): CrisisKind {
  if (SUICIDE.test(text)) return 'suicide';
  if (DANGER.test(text)) return 'danger';
  return null;
}

// The first words Mandy says in a crisis. Fixed text, chosen by people, not the model.
export function safetyMessage(kind: CrisisKind): string {
  if (kind === 'suicide') return "I'm really glad you told me. You matter. If you're thinking about ending your life, you can call or text 988 right now. It's free and open 24 hours a day.";
  if (kind === 'danger') return 'You deserve to be safe. The crisis lines above are free and open 24 hours a day.';
  return '';
}
