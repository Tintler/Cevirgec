// Development-only stand-in for bridge.py, used when the page runs in a plain browser.
export function createMock(emit) {
  const chapters = [
    'Prologue', 'Chapter 1', 'Chapter 2', 'Chapter 3', 'Interlude - Prefit Elmon', 'Chapter 4', 'Chapter 5',
    'Chapter 6', 'Chapter 7', 'Chapter 8', 'Chapter 9', 'Chapter 10', 'Chapter 11', 'Chapter 12',
  ].map((title, i) => ({
    type: 'chapter', filename: `${String(i + 1).padStart(3, '0')}-${title.toLowerCase().replace(/\W+/g, '-')}.md`, title,
    label: i < 6 ? 'Tamamlandı' : i === 6 ? 'Sıradaki' : 'Bekliyor', kind: i < 6 ? 'done' : i === 6 ? 'next' : 'waiting', depth: 0,
  }));
  let book = { epub: '', folder: '', project: '', title: '', author: '', cover: null, chars: 0 };
  let status = { runtime: 'idle', phase: 'Başlatılmadı', current: '', last_saved: '', progress: { done: 0, total: 0, percent: 0 } };
  let glossary = [{ source: 'Bard', target: 'Ozan' }, { source: 'Mage', target: 'Büyücü' }];
  let config = {
    base_url: 'http://127.0.0.1:1234/v1', model: 'gemma-4-26b-a4b-it-qat@q4_k_xl', chunk_chars: 6000, max_tokens: 8192,
    notes_max_tokens: 4096, temperature: 0.2, timeout_seconds: 1800, auto_retry_count: 2, token_estimation_enabled: true,
    fallback_context_length: 32768, context_safety_tokens: 2048, glossary_strict: false, completion_marker_enabled: true,
    completion_marker_exempt_chars: 400, epubcheck_enabled: false, epubcheck_path: '',
  };
  let timer = null;
  let prefs = { theme: 'dark' };
  const log = (message) => emit('log', { line: `[${new Date().toLocaleTimeString('tr-TR')}] ${message}`, last_saved: status.last_saved });
  const snapshot = () => ({ book, chapters: book.project ? chapters : [], status, analysis: '', model: config.model, loaded_models: [config.model] });
  const loadBook = () => {
    book = { epub: 'Z:\\LLM-Files\\kitap-cevir\\wandering-bard.epub', folder: 'Z:\\LLM-Files\\kitap-cevir',
      project: 'Z:\\LLM-Files\\kitap-cevir\\wandering-bard-ceviri', title: 'The Wandering Bard', author: 'Ellen Hart', cover: null, chars: 318400 };
    status = { ...status, phase: 'Mevcut proje hazır', progress: { done: 6, total: chapters.length, percent: 42 } };
  };

  return {
    snapshot: async () => snapshot(),
    ui_prefs: async () => prefs,
    save_ui_prefs: async (values) => (prefs = { ...prefs, ...values }),
    refresh_models: async () => {
      emit('models', { state: 'probing' });
      setTimeout(() => emit('models', { state: 'ok', model: config.model, loaded: [{ name: config.model, context: 32768 }] }), 500);
      return true;
    },
    choose_epub: async () => { loadBook(); book.project = ''; return snapshot(); },
    choose_folder: async () => snapshot(),
    open_project: async () => { loadBook(); return snapshot(); },
    preview: async (filename) => {
      const done = chapters.find((c) => c.filename === filename)?.kind === 'done';
      const source = 'The patrons had all gone home, but the inn wasn\'t empty. The Bard was counting coins on the stage.\n\n"Can you teach me to play?"';
      const translation = 'Müşterilerin hepsi evlerine dönmüştü ama han boş değildi. Ozan sahnede altınlarını sayıyordu.\n\n"Bana çalmayı öğretebilir misin?"';
      return { source, translation: done ? translation : '', translated: done, source_chars: source.length, translation_chars: done ? translation.length : 0 };
    },
    prepare_project: async () => {
      if (book.project) return { ok: true, glossary_required: false };
      loadBook();
      return { ok: true, glossary_required: true, glossary };
    },
    confirm_glossary: async (rows) => { glossary = rows; return snapshot(); },
    cancel_prepare: async () => { book.project = ''; return snapshot(); },
    model_check: async () => ({}),
    analysis_candidates: async () => ({ chapters: chapters.map((c) => ({ filename: c.filename, title: c.title, story: true, checked: true })) }),
    start: async () => {
      status = { ...status, runtime: 'running', phase: 'Çeviri çalışıyor' };
      let part = 0;
      timer = setInterval(() => {
        if (status.runtime !== 'running') return;
        part = (part % 9) + 1;
        status = { ...status, current: `${chapters[6].filename} — çeviri ${part}/9`, progress: { ...status.progress, percent: 42 + part } };
        chapters[6] = { ...chapters[6], label: 'Çevriliyor', kind: 'active' };
        emit('chapters', { chapters });
        emit('status', status);
        log(`Parça ${part}/9 kaydedildi`);
      }, 1500);
      return status;
    },
    control: async () => {
      status = { ...status, runtime: status.runtime === 'running' ? 'paused' : 'running', phase: status.runtime === 'running' ? 'Duraklatıldı' : 'Çeviri çalışıyor' };
      emit('status', status);
      return status;
    },
    rerun: async () => status,
    validate_rerun: async () => ({ ok: true }),
    get_glossary: async () => ({ glossary }),
    save_glossary: async (rows) => { glossary = rows; return { changed: true, affected: [chapters[1].filename] }; },
    submit_review: async () => true,
    get_settings: async () => ({ config, defaults: { ...config }, loaded_models: [config.model], locked: status.runtime === 'idle' ? [] : ['base_url', 'model'] }),
    save_settings: async (values) => { config = { ...config, ...values }; return { config }; },
  };
}
