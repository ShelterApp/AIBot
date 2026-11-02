// Mandy never refers people to 211. This checks everything she can say or show.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';

test('nothing Mandy can say or show mentions 211', () => {
  const files = [...readdirSync('src').map(f => `src/${f}`), ...readdirSync('data').map(f => `data/${f}`), 'public/index.html'];
  for (const f of files) assert.doesNotMatch(readFileSync(f, 'utf8'), /\b211\b/, f);
});
