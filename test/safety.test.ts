import { test } from 'node:test';
import assert from 'node:assert/strict';
import { crisisKind, safetyMessage } from '../src/safety.ts';

test('catches direct and indirect suicidal statements', () => {
  for (const t of [
    'I might as well be dead', 'i want to end it all', 'I want to die', 'better off dead',
    "what's the point anymore", "I don't want to wake up", 'thinking about suicide', 'I od\'d last week',
    "I can't go on", 'I keep hurting myself',
  ]) assert.equal(crisisKind(t), 'suicide', t);
});

test('catches danger and abuse from others', () => {
  for (const t of ['my boyfriend beats me', 'she hit me', 'I am not safe here', 'my ex keeps following me'])
    assert.equal(crisisKind(t), 'danger', t);
});

test('no false alarms on ordinary words', () => {
  for (const t of [
    'I am coding all night', 'spend it all on food', 'grape juice please', 'the therapist said hi',
    'I am so tired, I need somewhere to sleep', 'the weekend is over', 'heart attack risk clinic',
  ]) assert.equal(crisisKind(t), null, t);
});

test('crisis messages lead with care and 988', () => {
  assert.match(safetyMessage('suicide'), /You matter\..*988/);
  assert.match(safetyMessage('danger'), /deserve to be safe/);
  assert.equal(safetyMessage(null), '');
});
