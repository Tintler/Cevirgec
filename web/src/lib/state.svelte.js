import { call, on } from './api.js';

export const app = $state({
  view: 'translate',
  theme: 'dark',
  book: { epub: '', folder: '', project: '', title: '', author: '', cover: null, chars: 0 },
  chapters: [],
  status: { runtime: 'idle', phase: 'Başlatılmadı', current: '', last_saved: '', progress: { done: 0, total: 0, percent: 0 } },
  analysis: '',
  log: [],
  models: { state: 'probing', loaded: [], available: [], model: '', message: '' },
  review: null,
  // Translate screen view state, kept here so it survives switching tabs.
  translate: { tab: 'preview', selected: null, preAnalysis: false },
  // Reading tab: library books, the open book (null = library view) and reader preferences.
  library: [],
  reading: { book: null, section: 0, offset: 0, percent: 0 },
  reader: { size: 19, font: 'serif', leading: 1.75, width: 680, paper: 'theme' },
  // Read aloud: model status from tts.py, export progress and preferences.
  tts: { state: 'idle', message: '', device: null, exporting: false },
  ttsExport: null,
  ttsPrepare: null,   // {book, done, total} while sentences are being prepared
  ttsPrefs: { device: 'auto', speed: 1, voices: {}, options: {} },
  engines: [],
});

export const READER_DEFAULTS = { size: 19, font: 'serif', leading: 1.75, width: 680, paper: 'theme' };

export const toasts = $state([]);

let toastId = 0;

export function toast(message, kind = 'info') {
  const id = ++toastId;
  toasts.push({ id, message, kind });
  setTimeout(() => dismissToast(id), kind === 'error' ? 7000 : 4000);
}

// Looks the toast up by id: the array holds Svelte proxies, so the original object is never found by indexOf.
export function dismissToast(id) {
  const index = toasts.findIndex((t) => t.id === id);
  if (index >= 0) toasts.splice(index, 1);
}

export function applySnapshot(snapshot) {
  if (!snapshot) return;
  app.book = snapshot.book;
  app.chapters = snapshot.chapters;
  app.status = snapshot.status;
  app.analysis = snapshot.analysis;
  if (snapshot.model && !app.models.model) app.models.model = snapshot.model;
}

const LOG_LIMIT = 5000;

export async function init() {
  on('log', ({ line, last_saved }) => {
    app.log.push(line);
    if (app.log.length > LOG_LIMIT) app.log.splice(0, app.log.length - LOG_LIMIT);
    app.status.last_saved = last_saved;
  });
  on('status', (status) => (app.status = status));
  on('chapters', ({ chapters }) => (app.chapters = chapters));
  on('analysis', ({ text }) => (app.analysis = text));
  on('models', (data) => (app.models = { loaded: [], message: '', ...app.models, ...data }));
  on('review', (data) => (app.review = data));
  on('finished', ({ outcome, message, snapshot }) => {
    applySnapshot(snapshot);
    app.review = null;
    if (outcome === 'failed') {
      toast('Çeviri durdu: ' + message, 'error');
      app.translate.tab = 'console';
    }
    else toast(message, outcome === 'done' ? 'ok' : 'info');
  });
  on('library', ({ books, added, errors }) => {
    app.library = books;
    if (added?.length) {
      toast(added.length === 1 ? 'Kitap kitaplığa eklendi.' : `${added.length} kitap kitaplığa eklendi.`, 'ok');
      if (!app.reading.book) app.view = 'reading';
    }
    for (const message of errors ?? []) toast(message, 'error');
  });
  on('tts', (status) => {
    app.tts = status;
    // A voice may have been downloaded while loading: refresh the "downloaded" marks.
    if (status.state === 'ready') call('tts_engines').then((list) => { if (Array.isArray(list)) app.engines = list; });
  });
  on('tts_prepare', (data) => {
    if (!data.finished) { app.ttsPrepare = { book: data.book, done: data.done, total: data.total }; return; }
    app.ttsPrepare = null;
    if (data.error) toast('Hazırlama durdu: ' + data.error, 'error');
    else if (data.cancelled) toast(`Hazırlama durduruldu (${data.done} cümle hazır).`);
    else toast(`Ses hazırlandı (${data.done} cümle).`, 'ok');
  });
  on('tts_export', (data) => {
    if (!data.finished) { app.ttsExport = data; return; }
    app.ttsExport = null;
    if (data.error) toast('Ses kaydedilemedi: ' + data.error, 'error');
    else toast(`Ses kaydedildi (${data.count} cümle): ${data.path}`, 'ok');
  });
  const prefs = await call('ui_prefs');
  app.ttsPrefs = { device: 'auto', speed: 1, voices: {}, options: {}, ...(prefs?.tts ?? {}) };
  on('tts_engines', ({ engines, done, error }) => {
    app.engines = engines;
    if (done) toast(done, 'ok');
    if (error) toast(error, 'error');
  });
  app.tts = await call('tts_status');
  setTheme(prefs?.theme ?? 'dark', false);
  app.reader = { ...READER_DEFAULTS, ...(prefs?.reader ?? {}) };
  const books = await call('library_list');
  if (Array.isArray(books)) app.library = books;
  applySnapshot(await call('snapshot'));
  call('refresh_models');
}

export function setTheme(theme, save = true) {
  app.theme = theme;
  document.documentElement.dataset.theme = theme;
  if (save) call('save_ui_prefs', { theme });
}

// Calls a backend method and turns {error} results into a toast; returns null on error.
export async function act(method, ...args) {
  try {
    const result = await call(method, ...args);
    if (result && typeof result === 'object' && 'error' in result) {
      toast(result.error, 'error');
      return null;
    }
    return result;
  } catch (error) {
    toast(String(error?.message ?? error), 'error');
    return null;
  }
}

export function saveReaderPrefs() {
  call('save_ui_prefs', { reader: $state.snapshot(app.reader) });
}

export function saveTtsPrefs() {
  call('save_ui_prefs', { tts: $state.snapshot(app.ttsPrefs) });
}
