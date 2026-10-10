// Read-aloud player: asks the back end (EMA Lightning) for one sentence at a time, plays it with
// the Web Audio API and prefetches the next sentences so speech flows without gaps.
import { call } from './api.js';

const PREFETCH = 2;

export const player = $state({
  active: false,     // a reading session exists (playing or paused)
  playing: false,
  waiting: false,    // the current sentence is still being synthesised
  index: -1,
  total: 0,
  speed: 1,
});

let sentences = [];
let scope = { book: null, language: 'tr' };   // where rendered sentences are cached
let context = null;
let source = null;
let generation = 0;
const cache = new Map();
const hooks = { onsentence: () => {}, onend: () => {}, onerror: () => {} };

export function setHooks(next) {
  Object.assign(hooks, next);
}

export function setSentences(list, where = {}) {
  sentences = list;
  scope = { book: where.book ?? null, language: where.language || 'tr' };
  player.total = list.length;
  cache.clear();
}

function base64Buffer(text) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function fetchSentence(index) {
  const key = `${index}@${player.speed}`;
  if (!cache.has(key)) {
    const promise = call('tts_say', sentences[index], player.speed, scope.book, scope.language).then(async (result) => {
      if (!result || result.error) throw new Error(result?.error ?? 'Ses üretilemedi.');
      if (!result.audio) return null;
      return context.decodeAudioData(base64Buffer(result.audio));
    });
    promise.catch(() => cache.delete(key));
    cache.set(key, promise);
  }
  return cache.get(key);
}

function stopSource() {
  if (source) {
    source.onended = null;
    try { source.stop(); } catch { /* already stopped */ }
    source.disconnect();
    source = null;
  }
}

async function playIndex(index) {
  const token = ++generation;
  stopSource();
  if (index < 0) index = 0;
  if (index >= sentences.length) {
    player.playing = false;
    hooks.onend();
    return;
  }
  player.index = index;
  player.waiting = true;
  hooks.onsentence(index);
  let buffer;
  try {
    buffer = await fetchSentence(index);
  } catch (error) {
    if (token !== generation) return;
    player.waiting = false;
    pause();
    hooks.onerror(error.message);
    return;
  }
  if (token !== generation) return;
  player.waiting = false;
  for (let i = index + 1; i <= Math.min(sentences.length - 1, index + PREFETCH); i++) fetchSentence(i);
  if (!buffer) { playIndex(index + 1); return; }
  source = context.createBufferSource();
  source.buffer = buffer;
  source.connect(context.destination);
  source.onended = () => { if (token === generation && player.playing) playIndex(index + 1); };
  source.start();
}

export async function play(index = player.index) {
  context ??= new AudioContext();
  if (context.state === 'suspended') await context.resume();
  player.active = true;
  player.playing = true;
  await playIndex(index < 0 ? 0 : index);
}

export function pause() {
  player.playing = false;
  context?.suspend();
}

export async function resume() {
  if (!player.active) return play(0);
  player.playing = true;
  await context.resume();
  if (!source && !player.waiting) playIndex(player.index);
}

export function stop() {
  generation++;
  stopSource();
  context?.suspend();
  player.active = player.playing = player.waiting = false;
  player.index = -1;
  cache.clear();
}

export function step(delta) {
  if (!player.active) return;
  player.playing = true;
  context.resume();
  playIndex(Math.max(0, Math.min(sentences.length - 1, player.index + delta)));
}

export function setSpeed(speed) {
  player.speed = speed;
  // Already prefetched sentences used the old speed; the current one finishes as it is.
  for (const key of [...cache.keys()]) if (!key.endsWith(`@${speed}`)) cache.delete(key);
  if (player.active && player.index >= 0) {
    for (let i = player.index + 1; i <= Math.min(sentences.length - 1, player.index + PREFETCH); i++) fetchSentence(i);
  }
}
