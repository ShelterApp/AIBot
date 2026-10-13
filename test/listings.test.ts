import { test } from 'node:test';
import assert from 'node:assert/strict';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { loadListings, searchListings, safeUrl, tidyHours } from '../src/listings.ts';

const dir = mkdtempSync(join(tmpdir(), 'aibot-'));
const file = join(dir, 'listings.json');
writeFileSync(file, JSON.stringify([
  { name: 'Day Shelter', address1: '1 A St', city: 'Seattle', state: 'WA', zip: '98101', phone: '(206) 555-1234',
    website: 'www.example.org', services: 'SHELTER', categories: 'ALL', schedule: 'MONDAY08:00 AM - 05:00 PM; TUESDAY08:00 AM - 05:00 PM', approval: 'Approved', added_by: 'staff@example.org' },
  { name: 'Night Shelter', address1: '2 B St', city: 'Seattle', state: 'WA', zip: '98122', phone: '(000) 000-0000',
    website: 'javascript:alert(1)', services: 'SHELTER', categories: 'WOMEN', schedule: 'Open 24/7', approval: 'Approved' },
  { name: 'Unapproved Pantry', address1: '3 C St', city: 'Seattle', state: 'WA', zip: '98101', services: 'FOOD', approval: 'Pending' },
  { name: 'Tacoma Pantry', address1: '.', city: 'Tacoma', state: 'WA', zip: '98402', services: 'FOOD', categories: 'ALL' },
]));
const all = loadListings(file);

test('loads only approved listings and drops staff fields and placeholders', () => {
  assert.equal(all.length, 3);
  assert.ok(!all.some(l => l.name === 'Unapproved Pantry'));
  assert.ok(!JSON.stringify(all).includes('staff@example.org'));
  assert.equal(all.find(l => l.name === 'Night Shelter')!.phone, '');
  assert.equal(all.find(l => l.name === 'Tacoma Pantry')!.address, '');
});

test('only safe web links survive', () => {
  assert.equal(safeUrl('www.example.org'), 'https://www.example.org');
  assert.equal(safeUrl('https://ok.org/x'), 'https://ok.org/x');
  assert.equal(safeUrl('javascript:alert(1)'), '');
  assert.equal(safeUrl('data:text/html,hi'), '');
  assert.equal(all.find(l => l.name === 'Night Shelter')!.website, '');
});

test('hours are tidied', () => {
  assert.equal(tidyHours('MONDAY08:00 AM - 05:00 PM; TUESDAY08:00 AM - 05:00 PM'), 'Mon-Tue 8:00 AM - 5:00 PM');
  assert.equal(tidyHours('Open 24/7'), 'Open 24/7');
});

test('search: nearest first, never far away, 24/7 first at night', () => {
  assert.equal(searchListings(all, { type: 'shelter', zip: '98101' })[0].name, 'Day Shelter');
  assert.equal(searchListings(all, { type: 'shelter', zip: '98101', night: true })[0].name, 'Night Shelter');
  assert.deepEqual(searchListings(all, { type: 'shelter', zip: '33101' }), []);
  assert.equal(searchListings(all, { type: 'shelter', city: 'seattle', who: 'men' }).length, 1); // women-only excluded
});

test('addresses with a suite keep their state, and street numbers never become ZIPs', () => {
  const f = join(dir, 'suite.json');
  writeFileSync(f, JSON.stringify([
    { name: 'Peer Clinic', address_full: '1520 Bellevue Ave, Suite 100, Seattle, WA, 98122', address1: '1520 Bellevue Ave', city: 'Suite 100', state: null, zip: '98122', services: 'HEALTH', approval: 'Approved' },
    { name: 'Kirkland Place', address_full: '10604 NE 38th Pl, Kirkland, WA, 98033', address1: '10604 NE 38th Pl', city: 'Kirkland', state: 'WA', zip: '10604', services: 'FOOD', approval: 'Approved' },
  ]));
  const [a, b] = loadListings(f);
  assert.deepEqual([a.address, a.city, a.state, a.zip], ['1520 Bellevue Ave, Suite 100', 'Seattle', 'WA', '98122']);
  assert.equal(b.zip, '98033');
});
