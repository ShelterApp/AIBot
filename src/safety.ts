// Crisis safety is fixed code first: if a message suggests danger, the crisis lines
// always show, whatever the model does. The model's own danger check (in bot.ts)
// is a second net for phrasings and languages these patterns miss. No net catches
// everything, so the page also keeps these numbers on screen at all times.

// Phones type a curly apostrophe and people often skip accents ("pego" for "pegó"), so
// messages are matched in plain form, and the Spanish patterns below have no accents.
const plain = (text: string) => text.normalize('NFD').replace(/\p{M}/gu, '').replace(/[’‘ʼ`]/g, "'");

// Every phrase must match whole words, so "coding" or "spend it" never trigger it.
const words = (parts: string[]) => new RegExp(`\\b(?:${parts.join('|')})\\b`, 'i');

// People someone may be running from, for phrases like "afraid of my ex".
const PERSON = '(?:him|her|them|my (?:ex|husband|wife|boyfriend|girlfriend|partner|dad|father|mom|mother|stepdad|stepfather|stepmom|pimp))';
const PERSONA = '(?:el|ella|ellos|mi (?:ex|esposo|esposa|marido|mujer|novio|novia|pareja|papa|padre|mama|madre|padrastro|madrastra))';
const FEAR = '(?:scared|afraid|terrified|frightened)';

const SUICIDE = words([
  'suicid\\w*', 'kill my ?self', 'end (?:it|it all|my life|things)', 'want to die', 'wanna die',
  '(?:might|may) as well (?:be )?dead', 'better off dead', 'rather be dead', 'wish (?:i|i\'?m|i was|i were) dead',
  '(?:don\'?t|do not) want to (?:live|be alive|be here|wake up)', 'no (?:reason|point) (?:to|in) (?:live|living|going on)',
  'not worth living', 'what\'?s the point(?: anymore| of (?:it|anything|living|trying|going on)|(?=\\W*$))',
  'can\'?t go on', 'give up on (?:life|everything)', 'no way out', 'disappear forever',
  'self[- ]?harm', 'hurt(?:ing)? myself', 'cut(?:ting)? myself',
  'overdos\\w*', 'o\\.?d\\.?\'?(?:d|ed|ing)', 'od\'?d',
  // Warning signs from 988lifeline.org: no reason to live, being a burden, unbearable pain.
  'nothing (?:left )?to live for', 'no reason to (?:go on|keep going|be here|stay alive)',
  '(?:i\'?m|i am|being|feel like)(?: just| such)? a burden', '(?:better|easier)(?: off)? without me',
  '(?:no ?one|nobody) (?:would|will|\'?d) (?:notice|care) if i', '(?:no ?one|nobody) (?:would|will|\'?d) miss me',
  'tired of (?:living|being alive|life)', 'done with (?:life|living)', 'no point in (?:anything|any of (?:it|this))',
  '(?:gave|giving) away (?:all )?my (?:stuff|things|belongings)', 'won\'?t be around',
  'can\'?t (?:take|do|stand|bear) (?:it|this)(?: any ?more| any longer| no more)',
  'want(?:s)? (?:it all|everything) to (?:stop|end|be over)', '(?:stop|end) forever',
  '(?:sleep|bed) and (?:never|not) wake up', '(?:hope|wish) i (?:never|don\'?t|wouldn\'?t) wake up',
  'wish i (?:was|were|had) never (?:been )?born', 'wish i\'?d never been born', 'wish i (?:could|would) (?:just )?disappear',
  'this is (?:my )?goodbye', 'goodbye forever',
  // A plan, or the means for one.
  '(?:saved|saving|stash\\w*|stockpil\\w*|hoard\\w*)(?: up)?(?: all)?(?: of)? my (?:pills|meds|medications?)',
  '(?:pills|meds|medications?) (?:saved|stashed|stockpiled)',
  '(?:took|swallowed) (?:too many|a bunch of|a handful of|a whole bottle of)(?: my| the)? (?:pills|meds|medications?)',
  'hang(?:ing)? myself', 'shoot(?:ing)? myself', 'drown(?:ing)? myself', 'slit(?:ting)? my wrists?',
  'jump(?:ing)? (?:off|from) (?:a|the|this) (?:bridge|building|roof|overpass)',
  '(?:step|walk|jump)(?:ing)? in front of (?:a|the) (?:train|bus|truck|car)',
]);

const SUICIDE_ES = words([
  'suicid(?:a|as|arme|arse|io)', '(?:quiero|quisiera|deseo) morir(?:me)?', 'me quiero morir',
  '(?:quiero|voy a) matarme', 'me (?:quiero|voy a) matar', 'quitarme la vida', '(?:acabar|terminar) con (?:mi vida|todo)',
  'no quiero (?:vivir(?! (?:aqui|alli|alla|ahi|en|con|cerca))|seguir viviendo|seguir(?=\\W*$)|estar aqui|despertar)',
  'no (?:tengo|hay) (?:razon|razones|motivo|motivos|ganas) (?:para|de) (?:vivir|seguir)',
  'no (?:vale|tiene) (?:la pena|sentido) (?:vivir|seguir)', 'la vida no tiene sentido', 'ya no tiene caso',
  'no (?:le )?encuentro sentido', '(?:dormir|dormirme) y no despertar', 'ojala no (?:amanezca|despierte)',
  '(?:mejor|prefiero estar|quisiera estar|ojala estuviera) muert[oa]',
  'no puedo mas(?! tarde)', 'no aguanto mas', 'ya no aguanto(?=\\W*$)', 'no puedo seguir (?:asi|adelante|viviendo)',
  'hacerme dano', 'lastimarme', 'cortarme (?:las venas|los brazos|las munecas)', 'sobredosis',
  'nadie (?:me )?(?:notaria|extranaria|se daria cuenta)', 'soy una carga', 'mejor sin mi',
  'desaparecer para siempre', 'no (?:hay|tengo) salida',
]);

// Someone else's threat. Checked before a bare "kill me", which reads as wanting to die.
const THREAT = words([
  '(?:will|\'ll|would|going to|gonna|wants? to|trying to|tried to|threatened to|said|says|could|might)\\b[^.!?]{0,30}\\b(?:kill|murder|shoot|stab) me',
  'wants? me dead',
]);

const THREAT_ES = words([
  'me (?:quiere|quieren|va a|van a|iba a|iban a|amenazo con) (?:matar|pegar|golpear|lastimar)',
  '(?:quiere|quieren|va a|van a|iba a|iban a) (?:matarme|pegarme|golpearme|lastimarme)', 'me (?:matara|mataria|mataran)',
]);

const DANGER = words([
  'abus\\w*', 'beat(?:s|ing)? me', 'hit(?:s|ting)? me', 'domestic violence', 'not safe', 'unsafe', 'in danger',
  'attack(?:ed|ing|s)? me', 'rap(?:e|ed|ing)', 'traffick\\w*', 'follow(?:ing|ed|s) me', 'threaten\\w*',
  'hurt(?:ing)? me', '(?:he|she|they) hurts? me', 'kick(?:s|ed|ing)? me(?! out)', 'jumped me',
  '(?:punch|slapp?|chok|stabb?|grabb?|grop|touch)(?:s|es|e|ed|ing)? me', 'assault\\w*', 'stalk\\w*', 'molest\\w*', 'strangl\\w*',
  '(?:pulled|has|had) a (?:gun|knife) on me', 'shoot(?:ing)? at me', 'shot at me', 'pimp',
  'won\'?t let me (?:leave|go|out)', 'locked me (?:in|up)', 'feel(?:ing)? trapped',
  '(?:don\'?t|do not) feel safe', 'hands around my neck',
  `${FEAR} of ${PERSON}`, `${FEAR} to go (?:back|home)`, `${FEAR} for my (?:life|safety)`,
  `${FEAR}(?: that)? (?:he|she|they)(?:'ll| will| is going to| is gonna| might)? (?:hurt|find|kill|come)`,
  '(?:found|found out|finds|knows) where i (?:sleep|live|stay|am|work)',
  `(?:hiding|running|ran away|getting away|escaping|fleeing|fled) from ${PERSON}`,
]);

const DANGER_ES = words([
  'me (?:pega|pego|pegan|pegaron|golpea|golpeo|golpean|golpearon|lastima|lastimo|lastiman|lastimaron|viola|violo|violaron|ataca|atacan|ataco|atacaron|acosa|acosan|acoso|ahorca|ahorco|manosea|manoseo|persigue|persiguen)',
  '(?:alguien|un hombre|un tipo|una persona|el|ella) me agarro', 'me (?:esta|estan) siguiendo', 'me amenaz\\w*',
  'me dio una paliza', 'me (?:tiene|tienen) amenazad[oa]', 'tengo miedo de que me (?:encuentre|encuentren|mate|maten|pegue|peguen)',
  'maltrat\\w*', 'violencia (?:domestica|familiar|intrafamiliar|de genero)', 'abus(?:a|an|o|aron|ando|ar|ador\\w*|iv[oa])',
  'violad[oa]', 'trata de personas', 'en peligro', 'corro peligro', 'no (?:estoy|me siento) (?:a salvo|segur[oa])',
  '(?:le|les) tengo miedo', `tengo miedo de ${PERSONA}`, '(?:miedo|terror) de (?:volver|regresar)',
  '(?:sabe|saben|encontro|encontraron|descubrio) donde (?:duermo|vivo|estoy|me quedo|trabajo)',
  'no me (?:deja|dejan) (?:salir|ir|irme)', 'me (?:tiene|tienen) encerrad[oa]', 'me encerr(?:o|aron)',
  `(?:huyendo|escapando|escondiendome) de ${PERSONA}`, `me escondo de ${PERSONA}`,
]);

export type CrisisKind = 'suicide' | 'danger' | null;

export interface CrisisLine { name: string; how: string }

export const CRISIS_LINES: CrisisLine[] = [
  { name: '988 Suicide and Crisis Lifeline', how: 'Call or text 988' },
  { name: 'Emergency', how: 'Call 911' },
  { name: 'National Domestic Violence Hotline', how: 'Call 1-800-799-7233 or text START to 88788' },
];

// How to reach 988 in Spanish, from 988lifeline.org/interpretation-services/servicios-en-espanol (checked 2026-10-31).
export const CRISIS_LINES_ES: CrisisLine[] = [
  { name: 'Línea 988 de prevención del suicidio y crisis', how: 'Marca 988 y presiona 2, o envía la palabra AYUDA al 988' },
  { name: 'Emergencias', how: 'Llama al 911' },
  { name: 'Línea Nacional de Violencia Doméstica', how: 'Llama al 1-800-799-7233 o envía START por mensaje de texto al 88788' },
];

export function crisisKind(text: string): CrisisKind {
  const t = plain(text);
  if (SUICIDE.test(t) || SUICIDE_ES.test(t)) return 'suicide';
  if (THREAT.test(t) || THREAT_ES.test(t)) return 'danger';
  if (/\bkill me\b/i.test(t)) return 'suicide';
  return DANGER.test(t) || DANGER_ES.test(t) ? 'danger' : null;
}

// The Spanish word list knows the language even when the model leaves it out or is down.
export const isSpanishCrisis = (text: string) => [SUICIDE_ES, THREAT_ES, DANGER_ES].some(re => re.test(plain(text)));

export function crisisBox(kind: CrisisKind, spanish = false): { crisis: CrisisLine[]; crisisTitle?: string } {
  if (!kind) return { crisis: [] };
  return spanish ? { crisis: CRISIS_LINES_ES, crisisTitle: 'Ayuda gratis, las 24 horas del día:' } : { crisis: CRISIS_LINES };
}

// The first words Mandy says in a crisis. Fixed text, chosen by people, not the model.
export function safetyMessage(kind: CrisisKind, spanish = false): string {
  if (kind === 'suicide') {
    return spanish
      ? 'Me alegra mucho que me lo hayas dicho. Importas. Si estás pensando en quitarte la vida, puedes marcar 988 y presionar 2, o enviar la palabra AYUDA al 988, ahora mismo. Es gratis y está abierto las 24 horas del día.'
      : "I'm really glad you told me. You matter. If you're thinking about ending your life, you can call or text 988 right now. It's free and open 24 hours a day.";
  }
  if (kind === 'danger') {
    return spanish
      ? 'Mereces estar a salvo. Las líneas de ayuda de arriba son gratis y están abiertas las 24 horas del día.'
      : 'You deserve to be safe. The crisis lines above are free and open 24 hours a day.';
  }
  return '';
}
