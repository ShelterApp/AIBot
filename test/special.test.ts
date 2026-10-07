import { test } from 'node:test';
import assert from 'node:assert/strict';
import { aboutImmigration, aboutPets, specialResources } from '../src/special.ts';
import { nameSim, findDuplicates } from '../src/matching.ts';

test('recognizes immigration enforcement in English and Spanish', () => {
  for (const t of ['ICE took my husband', 'ice agents came to our door', 'my neighbor got deported', 'La migra se llevó a mi hermano', 'I need an immigration lawyer'])
    assert.ok(aboutImmigration(t), t);
});

test('does not mistake ordinary words for immigration', () => {
  for (const t of ['is there ice cream at the food bank', 'icy roads tonight', 'can I pay with my Visa card'])
    assert.ok(!aboutImmigration(t), t);
});

test('pets and state resources', () => {
  assert.ok(aboutPets('her dog is alone'));
  const wa = specialResources(['immigration'], 'WA');
  assert.equal(wa[0].area, 'WA');                          // state help first
  assert.ok(wa.every(r => r.area === 'US' || r.area === 'WA'));
  assert.ok(wa.every(r => r.source && r.verified));        // every entry says where it came from
  assert.ok(!specialResources(['immigration'], 'TX').some(r => r.category === 'pets'));
});

test('duplicate matching ignores shared common words', () => {
  assert.ok(nameSim('Winslow Public Library', 'Community Library') < 0.9);
  assert.equal(findDuplicates({ name: 'Mary Place', zip: '98104' }, [{ name: "Mary's Place", zip: '98104' }]).length, 1);
});
