import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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

test('catches warning signs said without the obvious words', () => {
  for (const t of [
    'Nobody would notice if I was gone', 'I have all my pills saved up and I am done',
    'I just want everything to stop forever', 'everyone would be better off without me',
    'I am a burden to everyone', 'I have nothing to live for', "I can't take it anymore",
    'I am tired of living', "what's the point", 'I want to go to sleep and never wake up',
    'thinking about jumping off the bridge', 'i cant do this no more', 'there is no point in anything',
    'i gave away all my stuff', 'i wont be around much longer',
  ]) assert.equal(crisisKind(t), 'suicide', t);
});

test('catches danger said without the obvious words', () => {
  for (const t of [
    'My ex found where I sleep and I am scared', 'Someone grabbed me at the shelter last night',
    'I am afraid of my husband', 'he choked me', 'I am hiding from my ex', 'he wont let me leave',
    'he kicked me in the ribs', 'i dont feel safe', 'he put his hands around my neck',
  ]) assert.equal(crisisKind(t), 'danger', t);
});

test('someone threatening to kill you is danger, not suicide', () => {
  for (const t of ['He said he will kill me if I go back', "she's going to kill me", "he'll kill me", 'they want to kill me', 'Mi esposo me va a matar'])
    assert.equal(crisisKind(t), 'danger', t);
  for (const t of ['just kill me', 'somebody kill me']) assert.equal(crisisKind(t), 'suicide', t);
});

test('catches Spanish, with or without accents', () => {
  for (const t of [
    'No quiero vivir más', 'no quiero vivir mas', 'Quiero morirme', 'Me quiero matar', 'ya no aguanto más',
    'Soy una carga para mi familia', 'nadie me extrañaría', 'estarían mejor sin mí', 'no le encuentro sentido a la vida',
    'quisiera dormirme y no despertar', 'ojalá no amanezca mañana', 'ya no tiene caso seguir',
  ]) assert.equal(crisisKind(t), 'suicide', t);
  for (const t of [
    'Mi esposo me pegó y tengo miedo', 'mi novio me pega', 'me amenazó', 'violencia doméstica', 'no estoy segura aquí',
    'él sabe dónde duermo', 'me quiere matar', 'alguien me agarró anoche', 'mi pareja me dio una paliza',
    'mi marido me tiene amenazada', 'tengo miedo de que me encuentre',
  ]) assert.equal(crisisKind(t), 'danger', t);
});

test('a phone keyboard apostrophe does not hide a crisis', () => {
  assert.equal(crisisKind('I don’t want to live'), 'suicide');
  assert.equal(crisisKind('I can’t go on'), 'suicide');
});

test('no false alarms on ordinary words', () => {
  for (const t of [
    'I am coding all night', 'spend it all on food', 'grape juice please', 'the therapist said hi',
    'I am so tired, I need somewhere to sleep', 'the weekend is over', 'heart attack risk clinic',
    'they kicked me out', 'I got kicked out of the shelter', 'I lost all my meds', 'I need to take all my meds',
    'nobody will help me', 'my friend found me a bed', 'my tooth hurts', "I can't take this bus",
    'I wish I was born rich', 'I am afraid they will turn me away',
    'quiero cortarme el pelo', 'me tocó dormir afuera', 'me agarró la lluvia', 'no puedo más tarde',
    'me sigue doliendo la muela', 'no quiero vivir aquí', 'hay amenaza de tormenta',
  ]) assert.equal(crisisKind(t), null, t);
});

test('crisis messages lead with care and 988', () => {
  assert.match(safetyMessage('suicide'), /You matter\..*988/);
  assert.match(safetyMessage('danger'), /deserve to be safe/);
  assert.equal(safetyMessage(null), '');
});

test('fixed Spanish crisis wording gives the Spanish way to reach 988', () => {
  assert.match(safetyMessage('suicide', true), /^Me alegra mucho que me lo hayas dicho\. Importas\..*988.*presionar 2.*AYUDA/);
  assert.match(safetyMessage('danger', true), /^Mereces estar a salvo\./);
  assert.equal(safetyMessage(null, true), '');
});

test('crisis numbers are always on the page, not only when detection fires', () => {
  const page = readFileSync('public/index.html', 'utf8').split('<script>')[0];
  for (const href of ['tel:988', 'sms:988', 'tel:911', 'tel:18007997233']) assert.ok(page.includes(`href="${href}"`), href);
  assert.match(page, /presiona 2/);
});
