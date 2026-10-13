// Silence added after each read-aloud sentence, by how it ends. Engines get one sentence at a time and
// put no silence at its end, so without this the next sentence starts right after the full stop.

export const PAUSE_KEYS = [
  ['sentence', 'Nokta'],
  ['question', 'Soru ve ünlem'],
  ['ellipsis', 'Üç nokta'],
  ['paragraph', 'Paragraf sonu'],
];

// Seconds at 1× speed.
export const PAUSE_PRESETS = {
  none: { label: 'Yok', sentence: 0, question: 0, ellipsis: 0, paragraph: 0 },
  short: { label: 'Kısa', sentence: 0.2, question: 0.25, ellipsis: 0.4, paragraph: 0.45 },
  normal: { label: 'Normal', sentence: 0.4, question: 0.5, ellipsis: 0.7, paragraph: 0.8 },
  long: { label: 'Uzun', sentence: 0.6, question: 0.75, ellipsis: 1, paragraph: 1.2 },
};

export const PAUSE_DEFAULTS = { preset: 'normal', ...PAUSE_PRESETS.normal, scale: true };

// Saved preferences → the values in effect (a named preset wins over stale custom values).
export function resolvePauses(saved) {
  const prefs = { ...PAUSE_DEFAULTS, ...(saved ?? {}) };
  const preset = PAUSE_PRESETS[prefs.preset];
  const values = preset ?? prefs;
  const result = { scale: prefs.scale !== false };
  for (const [key] of PAUSE_KEYS) result[key] = Math.max(0, Number(values[key]) || 0);
  return result;
}

// Seconds of silence after one sentence; `paragraphEnd` when it is the last one of its block.
export function pauseAfter(text, paragraphEnd, pauses, speed = 1) {
  const end = String(text).trimEnd().replace(/["'”’»)\]]+$/, '');
  let gap = /(…|\.\.\.)$/.test(end) ? pauses.ellipsis
    : /[?!]$/.test(end) ? pauses.question
    : pauses.sentence;
  if (paragraphEnd) gap = Math.max(gap, pauses.paragraph);
  return pauses.scale && speed > 0 ? gap / speed : gap;
}
