<script>
  import Icon from '../lib/Icon.svelte';
  import Toggle from '../components/Toggle.svelte';
  import GlossaryModal from '../components/GlossaryModal.svelte';
  import AnalysisModal from '../components/AnalysisModal.svelte';
  import { app, act, applySnapshot, toast } from '../lib/state.svelte.js';
  import { call } from '../lib/api.js';
  import { confirm, notice } from '../lib/confirm.svelte.js';

  const ui = app.translate;
  let preview = $state(null);
  let glossary = $state([]);
  let glossaryModal = $state(null);   // {rows, title, onsave, oncancel}
  let analysisModal = $state(null);   // {chapters, resolve}
  let menu = $state(null);            // {x, y, filename}
  let consoleEl = $state();

  const runtime = $derived(app.status.runtime);
  const idle = $derived(runtime === 'idle');
  const hasBook = $derived(Boolean(app.book.epub || app.book.project));
  const progress = $derived(app.status.progress);

  // ---- selection
  async function chooseEpub() { const r = await act('choose_epub'); if (r) { applySnapshot(r); ui.selected = null; preview = null; } }
  async function chooseFolder() { const r = await act('choose_folder'); if (r) applySnapshot(r); }
  async function openProject() {
    const r = await act('open_project');
    if (r) { applySnapshot(r); ui.selected = null; preview = null; loadGlossary(); }
  }

  async function loadGlossary() {
    const r = app.book.project ? await call('get_glossary') : null;
    glossary = r?.glossary ?? [];
  }

  // ---- start / pause / resume
  async function control() {
    if (!idle) { app.status = await call('control'); return; }
    const prepared = await act('prepare_project');
    if (!prepared) return;
    if (prepared.glossary_required) {
      const ok = await new Promise((resolve) => {
        glossaryModal = {
          rows: prepared.glossary, title: 'Terim sözlüğü — çeviri öncesi',
          onsave: async (rows) => {
            const r = await call('confirm_glossary', rows);
            if (r?.error) return r.error;
            applySnapshot(r); glossaryModal = null; resolve(true);
          },
          oncancel: async () => { glossaryModal = null; applySnapshot(await call('cancel_prepare')); resolve(false); },
        };
      });
      if (!ok) return;
      loadGlossary();
    }
    const check = await call('model_check');
    if (check.block) { await notice('Model seçilmedi', check.block); return; }
    if (check.warn) await notice('LM Studio bağlantısı doğrulanamadı', check.warn);
    if (check.info) toast(check.info);
    let files = null;
    if (ui.preAnalysis) {
      const candidates = await act('analysis_candidates');
      if (!candidates) return;
      files = await new Promise((resolve) => (analysisModal = { chapters: candidates.chapters, resolve }));
      analysisModal = null;
      if (!files) return;
    }
    const status = await act('start', files);
    if (status) { app.status = status; ui.tab = files ? 'analysis' : 'status'; }
  }

  // ---- glossary editing
  async function editGlossary() {
    const r = await act('get_glossary');
    if (!r) return;
    glossaryModal = {
      rows: r.glossary, title: 'Terim sözlüğü',
      oncancel: () => (glossaryModal = null),
      onsave: async (rows) => {
        const result = await call('save_glossary', rows);
        if (result?.error) return result.error;
        glossaryModal = null;
        loadGlossary();
        if (!result.changed) { toast('Değişiklik yok; yeniden çeviri gerekmiyor.'); return; }
        if (!result.affected.length) {
          toast('Sözlük kaydedildi. Değişen terimler çevrilmiş bölümlerde geçmiyor; kalan bölümler yeni sözlükle çevrilecek.', 'ok');
          return;
        }
        const yes = await confirm({
          title: 'Sözlük güncellendi',
          message: `Değişen terimlerin geçtiği ${result.affected.length} çevrilmiş bölüm yeniden çevrilsin mi?\n` +
            'Bu bölümler sonraki bölümlerin bağlamı olmadan yeniden çevrilir; yarım kalmış bölümün kaydı korunur.',
          list: result.affected, ok: 'Yeniden çevir', cancel: 'Şimdi değil',
        });
        if (yes) { const s = await act('rerun', result.affected); if (s) { app.status = s; ui.tab = 'status'; } }
      },
    };
  }

  // ---- chapters
  function select(filename) {
    ui.selected = filename;
    ui.tab = 'preview';
  }

  // Reload the preview on selection and whenever chapter states change (a chapter finished).
  $effect(() => {
    app.chapters;
    const filename = ui.selected;
    if (filename) call('preview', filename).then((p) => { if (ui.selected === filename) preview = p; });
  });

  $effect(() => {
    app.book.project;
    loadGlossary();
  });

  function contextMenu(event, filename) {
    event.preventDefault();
    menu = { x: event.clientX, y: event.clientY, filename };
  }

  async function rerunChapter(filename) {
    menu = null;
    if (!idle) { toast('Çalışan çeviri tamamlanmadan bölüm yeniden çevrilemez.', 'error'); return; }
    if (!(await act('validate_rerun', filename))) return;
    const yes = await confirm({
      title: 'Bölümü yeniden çevir',
      message: `"${filename}" bölümü yeniden çevrilecek. Mevcut çeviri _python_translation/${filename}/recheck-prev-<tarih>.md olarak yedeklenir; ` +
        'sonraki bölümlerin bağlamı (özet/notlar) bu çeviriye verilmez. Bitince TAM-CEVIRI.md (ve kitap tamamsa EPUB) yeniden üretilir.',
      ok: 'Yeniden çevir', cancel: 'Vazgeç',
    });
    if (yes) { const s = await act('rerun', [filename]); if (s) { app.status = s; ui.tab = 'status'; } }
  }

  // ---- preview highlighting
  const escapeHtml = (text) => text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  function highlight(text, terms) {
    const html = escapeHtml(text);
    const list = terms.filter(Boolean).map((t) => escapeHtml(t)).sort((a, b) => b.length - a.length);
    if (!list.length) return html;
    const re = new RegExp(`(?<![\\p{L}\\p{N}])(${list.map(escapeRe).join('|')})`, 'giu');
    return html.replace(re, '<mark>$1</mark>');
  }

  const sourceHtml = $derived(preview ? highlight(preview.source, glossary.map((g) => g.source)) : '');
  const translationHtml = $derived(preview?.translated ? highlight(preview.translation, glossary.map((g) => g.target)) : '');

  // ---- console autoscroll
  $effect(() => {
    app.log.length;
    if (consoleEl && ui.tab === 'console') queueMicrotask(() => (consoleEl.scrollTop = consoleEl.scrollHeight));
  });


  const controlLabel = $derived({ idle: 'Çeviriyi başlat', running: 'Duraklat', pausing: 'Duraklatılıyor…', paused: 'Devam et', closing: 'Kapanıyor…' }[runtime] ?? runtime);
  const fmt = (n) => Number(n || 0).toLocaleString('tr-TR');
</script>

<svelte:window onclick={() => (menu = null)} />

<section class="screen">
  <div class="top">
    <h1>Çeviri</h1>
    <button class="btn" disabled={!idle} onclick={openProject} title="Daha önce oluşturulmuş {'{'}KitapAdı{'}'}-ceviri klasörünü açar ve kayıtlı kontrol noktalarından devam eder.">
      <Icon name="history" />Projeyi sürdür</button>
    <button class="btn" disabled={!idle} onclick={chooseEpub}><Icon name="file" />EPUB aç</button>
  </div>

  {#if !hasBook}
    <div class="card welcome">
      <div class="logo"><Icon name="languages" size={34} /></div>
      <h2>Çevrilecek kitabı seçin</h2>
      <p>DRM içermeyen bir EPUB açın ya da daha önce başlattığınız bir çeviri projesini sürdürün. Çeviri, LM Studio'da yüklü
        yerel modelle yapılır.</p>
      <div class="row">
        <button class="btn primary lg" onclick={chooseEpub}><Icon name="file" />EPUB aç</button>
        <button class="btn lg" onclick={openProject}><Icon name="history" />Projeyi sürdür</button>
      </div>
    </div>
  {:else}
    <div class="card book">
      <div class="cover">
        {#if app.book.cover}<img src={app.book.cover} alt="" />{:else}<span>{app.book.title}</span>{/if}
      </div>
      <div class="meta">
        <div class="t">{app.book.title || 'Adsız kitap'}</div>
        <div class="a">{[app.book.author, progress.total ? `${progress.total} bölüm` : '', app.book.chars ? `${fmt(app.book.chars)} karakter` : ''].filter(Boolean).join(' · ')}</div>
        <div class="paths">
          <button class="path" disabled={!idle} onclick={chooseEpub} title="Kaynak EPUB'u değiştir">
            <Icon name="file" size={14} /><span class="selectable">{app.book.epub || '—'}</span></button>
          <button class="path" disabled={!idle} onclick={chooseFolder} title="Çalışma klasörünü değiştir">
            <Icon name="folder" size={14} /><span class="selectable">{app.book.folder || 'Çalışma klasörü seçin'}</span></button>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><b>{progress.done} / {progress.total || '—'}</b><small>bölüm</small></div>
        <div class="stat"><b>%{progress.percent}</b><small>tamamlandı</small></div>
      </div>
    </div>

    <div class="work">
      <div class="card col">
        <div class="card-h">Bölümler <span class="sp"></span><small>{app.chapters.filter((c) => c.type === 'chapter').length || ''}</small></div>
        <div class="chapters">
          {#each app.chapters as item}
            {#if item.type === 'group'}
              <div class="group" style:padding-left="{10 + item.depth * 14}px">{item.label}</div>
            {:else}
              <button class="ch" class:sel={ui.selected === item.filename} style:padding-left="{10 + item.depth * 14}px"
                onclick={() => select(item.filename)} oncontextmenu={(e) => contextMenu(e, item.filename)} title={item.filename}>
                <span class="n">{item.title}</span><span class="badge {item.kind}">{item.label}</span>
              </button>
            {/if}
          {:else}
            <div class="empty">Bölümler çeviri başladığında EPUB'dan ayrılır.</div>
          {/each}
        </div>
      </div>

      <div class="card col">
        <div class="tabs">
          {#each [['preview', 'Kaynak / Türkçe'], ['status', 'Durum'], ['analysis', 'Ön analiz'], ['console', 'Konsol']] as [key, label]}
            <button class="tab" class:on={ui.tab === key} onclick={() => (ui.tab = key)}>{label}</button>
          {/each}
        </div>

        {#if ui.tab === 'preview'}
          {#if preview}
            <div class="pv">
              <div><div class="lbl"><span>Kaynak · İngilizce</span><span>{fmt(preview.source_chars)} karakter</span></div>
                <div class="txt selectable">{@html sourceHtml}</div></div>
              <div><div class="lbl"><span>Çeviri · Türkçe</span><span>{fmt(preview.translation_chars)} karakter</span></div>
                <div class="txt selectable tr">{#if preview.translated}{@html translationHtml}{:else}<span class="muted">Henüz çevrilmedi.</span>{/if}</div></div>
            </div>
          {:else}
            <div class="empty grow">Kaynak metni ve çeviriyi görmek için soldan bir bölüm seçin.<br />Bir bölümü yeniden çevirmek için sağ tıklayın.</div>
          {/if}
        {:else if ui.tab === 'status'}
          <div class="status">
            <div class="tile"><small>Aşama</small><b>{app.status.phase}</b></div>
            <div class="tile wide"><small>Geçerli adım</small><b>{app.status.current || '—'}</b></div>
            <div class="tile"><small>Tamamlanan bölüm</small><b>{progress.done} / {progress.total}</b></div>
            <div class="tile"><small>Son kayıt</small><b>{app.status.last_saved || '—'}</b></div>
            <div class="tile"><small>İlerleme</small><b>%{progress.percent}</b></div>
          </div>
        {:else if ui.tab === 'analysis'}
          <div class="mono selectable">{app.analysis || 'Ön analiz seçilirse sonuç burada gösterilir.'}</div>
        {:else}
          <div class="mono selectable" bind:this={consoleEl}>
            {#each app.log as line}<div class:warn={/UYARI|HATA|Traceback/i.test(line)} class:ok={/kaydedildi|tamamlandı/i.test(line)}>{line}</div>{/each}
            {#if !app.log.length}<span class="muted">Henüz kayıt yok.</span>{/if}
          </div>
        {/if}
      </div>
    </div>
  {/if}

  <div class="bar">
    <button class="btn" disabled={!idle || !app.book.project} onclick={editGlossary}><Icon name="dict" />Terim sözlüğü</button>
    <span title="İsteğe bağlıdır. Kitap çeviriden önce analiz edilir ve toplam işlem süresi iki kata kadar uzayabilir.">
      <Toggle bind:checked={ui.preAnalysis} disabled={!idle} label="Ön analiz" /></span>
    <div class="prog">
      <span>Son kayıt {app.status.last_saved || '—'}</span>
      <div class="track"><i style:width="{progress.percent}%"></i></div>
      <span>%{progress.percent}</span>
    </div>
    <button class="btn primary lg" disabled={!hasBook || runtime === 'pausing' || runtime === 'closing'} onclick={control}>
      <Icon name={runtime === 'running' ? 'pause' : 'play'} />{controlLabel}</button>
  </div>
</section>

{#if menu}
  <div class="menu" style:left="{menu.x}px" style:top="{menu.y}px">
    <button onclick={() => rerunChapter(menu.filename)}><Icon name="rotate" size={15} />Bu bölümü yeniden çevir</button>
  </div>
{/if}

{#if glossaryModal}
  <GlossaryModal rows={glossaryModal.rows} title={glossaryModal.title} onsave={glossaryModal.onsave} oncancel={glossaryModal.oncancel} />
{/if}
{#if analysisModal}
  <AnalysisModal chapters={analysisModal.chapters} onconfirm={(f) => analysisModal.resolve(f)} oncancel={() => analysisModal.resolve(null)} />
{/if}

<style>
  .screen { display: flex; flex-direction: column; gap: 16px; padding: 18px 28px 0; height: 100%; min-height: 0; }
  .top { display: flex; align-items: center; gap: 10px; }
  .top h1 { font-size: 28px; font-weight: 700; letter-spacing: -.02em; flex: 1; }

  .welcome { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 14px; padding: 40px; }
  .welcome .logo { width: 72px; height: 72px; border-radius: 20px; display: grid; place-items: center; background: var(--surface); color: var(--accent); }
  .welcome h2 { font-size: 22px; }
  .welcome p { color: var(--secondary); max-width: 460px; }
  .welcome .row { display: flex; gap: 10px; margin-top: 8px; }

  .book { display: flex; gap: 18px; align-items: center; padding: 16px; }
  .cover { width: 64px; height: 92px; border-radius: 6px; flex: none; overflow: hidden; background: linear-gradient(160deg, #7c3aed, #1e1b4b); display: flex; align-items: flex-end; padding: 6px; color: #fff; font: 600 9px/1.2 var(--sans); box-shadow: 0 6px 18px rgba(0, 0, 0, .35); }
  .cover img { width: calc(100% + 12px); height: calc(100% + 12px); margin: -6px; object-fit: cover; }
  .meta { flex: 1; min-width: 0; }
  .meta .t { font-size: 18px; font-weight: 700; }
  .meta .a { color: var(--secondary); }
  .paths { display: flex; gap: 6px 18px; margin-top: 6px; flex-wrap: wrap; }
  .path { display: inline-flex; gap: 6px; align-items: center; font-size: 12px; color: var(--dim); max-width: 100%; padding: 2px 6px; margin-left: -6px; border-radius: 6px; }
  .path span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .path:not(:disabled):hover { background: var(--surface-hover); color: var(--text); }
  .stats { display: flex; gap: 22px; padding: 0 8px; }
  .stat b { display: block; font-size: 20px; }
  .stat small { color: var(--dim); font-size: 12px; }

  .work { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(260px, 320px) 1fr; gap: 16px; }
  .col { min-height: 0; display: flex; flex-direction: column; }
  .card-h { display: flex; align-items: center; gap: 8px; padding: 14px 16px 10px; font-weight: 700; }
  .card-h .sp { flex: 1; }
  .card-h small { color: var(--dim); font-weight: 500; }
  .chapters { overflow: auto; padding: 0 8px 8px; }
  .group { font-size: 11px; font-weight: 700; color: var(--dim); text-transform: uppercase; letter-spacing: .05em; padding: 10px 10px 4px; }
  .ch { display: flex; align-items: center; gap: 10px; padding: 7px 10px; border-radius: 8px; width: 100%; text-align: left; }
  .ch:hover { background: var(--surface-hover); }
  .ch.sel { background: var(--surface-active); }
  .ch .n { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .tabs { display: flex; gap: 6px; padding: 12px 14px 0; }
  .tab { padding: 6px 14px; border-radius: 999px; font-weight: 600; font-size: 13px; color: var(--secondary); }
  .tab.on { background: var(--text); color: var(--window); }
  .tab:not(.on):hover { background: var(--surface-hover); }
  .grow { flex: 1; display: grid; place-content: center; }

  .pv { display: grid; grid-template-columns: 1fr 1fr; flex: 1; min-height: 0; gap: 1px; background: var(--outline); margin: 12px 14px 14px; border-radius: 10px; overflow: hidden; }
  .pv > div { background: var(--panel); display: flex; flex-direction: column; min-height: 0; }
  .lbl { display: flex; justify-content: space-between; font-size: 11px; color: var(--dim); padding: 10px 16px; border-bottom: 1px solid var(--outline); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
  .txt { padding: 14px 16px; overflow: auto; color: var(--secondary); line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
  .txt.tr { color: var(--text); }
  .txt :global(mark) { background: none; color: var(--accent); font-weight: 600; }
  .muted { color: var(--dim); }

  .status { flex: 1; padding: 18px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; align-content: start; }
  .tile { background: var(--surface); border-radius: 12px; padding: 14px; min-width: 0; }
  .tile.wide { grid-column: span 2; }
  .tile small { color: var(--dim); font-size: 12px; }
  .tile b { display: block; font-size: 16px; margin-top: 4px; overflow-wrap: anywhere; }

  .mono { flex: 1; margin: 12px 14px 14px; padding: 12px 14px; border-radius: 10px; background: var(--window); font: 12px/1.7 var(--mono); color: var(--secondary); overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; min-height: 0; }
  .mono .ok { color: var(--ok); }
  .mono .warn { color: var(--warn); }

  .bar { display: flex; align-items: center; gap: 16px; padding: 14px 28px; margin: 0 -28px; border-top: 1px solid var(--outline); background: var(--panel); }
  .prog { flex: 1; display: flex; align-items: center; gap: 12px; font-size: 12px; color: var(--dim); white-space: nowrap; }

  .menu { position: fixed; z-index: 40; background: var(--panel); border: 1px solid var(--outline); border-radius: 10px; padding: 4px; box-shadow: var(--shadow); }
  .menu button { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 7px; font-size: 13px; width: 100%; }
  .menu button:hover { background: var(--surface-hover); }
</style>
