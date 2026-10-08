// Emergency shelters from FEMA's Open Shelters feed: only open ones, pet-friendly first
// when the person has pets, and never a pet promise FEMA didn't make.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { toShelters, DISASTER_HELP } from '../src/disasters.ts';

const rec = (name: string, status: string, pets: string | null) => ({ attributes: {
  shelter_name: name, address: '201 E Gregory St', city: 'Pensacola', state: 'FL', zip: '32502',
  shelter_status: status, pet_accommodations_code: pets } });

test('only open shelters are shown', () => {
  const list = toShelters([rec('Bay Center', 'OPEN', null), rec('Old School', 'CLOSED', null)]);
  assert.deepEqual(list.map(r => r.name), ['Bay Center']);
  assert.match(list[0].what, /201 E Gregory St, Pensacola, FL 32502/);
});

test('pet-friendly shelters come first when the person has pets', () => {
  const list = toShelters([rec('No Pets Hall', 'OPEN', 'NONE'), rec('Pet Center', 'OPEN', 'PETS_OK')], true);
  assert.equal(list[0].name, 'Pet Center');
  assert.match(list[0].what, /Accepts pets/);
  assert.doesNotMatch(list[1].what, /Accepts pets/);
});

test('the Red Cross map and 211 are always there as a fallback', () => {
  assert.ok(DISASTER_HELP.some(r => r.url?.includes('redcross.org')));
  assert.ok(DISASTER_HELP.some(r => r.phone === '211'));
});
