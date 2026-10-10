<script>
  import { tick, untrack } from 'svelte';
  import Icon from '../lib/Icon.svelte';
  import { app, act, toast, saveReaderPrefs, saveTtsPrefs, READER_DEFAULTS } from '../lib/state.svelte.js';
  import { call } from '../lib/api.js';
  import { confirm, notice } from '../lib/confirm.svelte.js';
  import { segment } from '../lib/sentences.js';
  import { player, play, pause, resume, stop, step, setSpeed, setSentences, setHooks } from '../lib/player.svelte.js';

  let { onclose } = $props();

  const book = $derived(app.reading.book);
  let html = $state('');
  let loading = $state(false);
  let panel = $state('toc');          // 'toc' | 'bookmarks' | null
  let showAa = $state(false);
  let pageWrap = $state();
  let article = $state();
  let saveTimer = null;
  let sentences = [];
  let languageAccepted = null;   // book id whose non-Turkish language warning was accepted
  const SPEEDS = [0.8, 1, 1.25, 1.5, 1.75, 2];

  const PAPERS = {
    theme: null,
    dark: { bg: '#111114', ink: '#dcdce0' },
    sepia: { bg: '#f4ecd8', ink: '#3b2f1e' },
    light: { bg: '#fbfaf7', ink: '#26241f' },
  };
  const paper = $derived(PAPERS[app.reader.paper] ?? null);
  const section = $derived(app.reading.section);
  const sectionCount = $derived(book?.sections.length ?? 0);
  const sectionTitle = $derived(book?.sections[section]?.title || `Bölüm ${section + 1}`);
  const currentToc = $derived(book ? book.toc.reduce((found, entry, i) => (entry.section <= section ? i : found), -1) : -1);
  const bookmarkHere = $derived(book?.bookmarks.find((b) => b.section === section && Math.abs(b.offset - app.reading.offset) < 0.015) ?? null);

  async function load(number, { offset = 0, anchor = '', continueReading = false } = {}) {
    if (!book || number < 0 || number >= sectionCount) return;
    if (player.active && !continueReading) stop();
    savePosition(true);
    loading = true;
    const result = await act('reader_section', book.id, number);
    loading = false;
    if (!result) return;
    html = result.html;
    app.reading.section = number;
    app.reading.offset = offset;
    await tick();
    const images = [...pageWrap.querySelectorAll('img')];
    await Promise.all(images.map((img) => img.decode?.().catch(() => {})));
    const target = anchor ? pageWrap.querySelector(`[id="${CSS.escape(anchor)}"]`) : null;
    if (target) target.scrollIntoView({ block: 'start' });
    else pageWrap.scrollTop = offset * Math.max(0, pageWrap.scrollHeight - pageWrap.clientHeight);
    sentences = segment(article);
    setSentences(sentences);
    savePosition(true);
  }

  // ---- read aloud
  function highlight(index) {
    for (const el of article?.querySelectorAll('.s.now') ?? []) el.classList.remove('now');
    const spans = article?.querySelectorAll(`.s[data-i="${index}"]`) ?? [];
    for (const el of spans) el.classList.add('now');
    const first = spans[0];
    if (first) {
      const box = first.getBoundingClientRect(), view = pageWrap.getBoundingClientRect();
      if (box.top < view.top + 40 || box.bottom > view.bottom - 60) first.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }

  setHooks({
    onsentence: highlight,
    onerror: (message) => toast(message, 'error'),
    onend: async () => {
      if (section < sectionCount - 1) {
        await load(section + 1, { continueReading: true });
        if (player.active) play(0);
      } else {
        stop();
        highlight(-1);
        toast('Kitabın sonuna gelindi.');
      }
    },
  });

  $effect(() => () => { stop(); });   // leaving the reader ends the reading session

  function firstVisibleSentence() {
    const top = pageWrap.getBoundingClientRect().top;
    const span = [...article.querySelectorAll('.s')].find((el) => el.getBoundingClientRect().bottom > top + 8);
    return span ? Number(span.dataset.i) : 0;
  }

  async function ensureModel() {
    if (app.tts.state === 'missing') {
      await notice('Sesli okuma kurulu değil',
        'Sesli okuma için EMA Lightning ses modeli gerekir. Komut isteminde şunu çalıştırıp uygulamayı yeniden başlatın:\n\n' +
        'py -3 -m pip install ema-lightning\n\n' +
        "NVIDIA ekran kartıyla daha hızlı çalışması için önce PyTorch'un CUDA sürümünü kurun " +
        '(ayrıntılar KULLANIM.md içinde). İlk kullanımda model (~34 MB) internetten indirilir.');
      return false;
    }
    if (app.tts.state === 'ready') return true;
    if (app.tts.state !== 'loading') app.tts = await call('tts_load', app.ttsPrefs.device);
    while (app.tts.state === 'loading') await new Promise((r) => setTimeout(r, 200));
    if (app.tts.state !== 'ready') { toast(app.tts.message || 'Ses modeli yüklenemedi.', 'error'); return false; }
    return true;
  }

  async function languageOk() {
    const language = book.language || '';
    if (!language || language.startsWith('tr') || languageAccepted === book.id) return true;
    const yes = await confirm({
      title: 'Kitap Türkçe değil',
      message: `Bu kitabın dili "${language}" olarak işaretli. EMA Lightning yalnızca Türkçe okur; başka dillerdeki metin anlaşılmaz okunur. Yine de okunsun mu?`,
      ok: 'Yine de oku', cancel: 'Vazgeç',
    });
    if (yes) languageAccepted = book.id;
    return yes;
  }

  async function togglePlay() {
    if (player.playing) { pause(); return; }
    if (player.active) { resume(); return; }
    if (!sentences.length) { toast('Bu bölümde okunacak metin yok.'); return; }
    if (!(await languageOk()) || !(await ensureModel())) return;
    setSpeed(app.ttsPrefs.speed);
    play(firstVisibleSentence());
  }

  function cycleSpeed() {
    const next = SPEEDS[(SPEEDS.indexOf(app.ttsPrefs.speed) + 1) % SPEEDS.length] ?? 1;
    app.ttsPrefs.speed = next;
    setSpeed(next);
    saveTtsPrefs();
  }

  async function exportSection() {
    if (app.ttsExport) return;
    if (!sentences.length) { toast('Bu bölümde kaydedilecek metin yok.'); return; }
    if (!(await languageOk()) || !(await ensureModel())) return;
    const result = await act('tts_export', `${book.title} - ${sectionTitle}`, sentences, app.ttsPrefs.speed);
    if (result?.started) app.ttsExport = { done: 0, total: sentences.length };
  }

  function currentOffset() {
    const max = pageWrap ? pageWrap.scrollHeight - pageWrap.clientHeight : 0;
    return max > 0 ? Math.min(1, pageWrap.scrollTop / max) : 0;
  }

  function savePosition(now = false) {
    if (!book || !pageWrap || !html) return;
    clearTimeout(saveTimer);
    app.reading.offset = currentOffset();
    const run = async () => {
      const percent = await call('reader_save_position', book.id, app.reading.section, app.reading.offset);
      if (typeof percent === 'number') {
        app.reading.percent = percent;
        const entry = app.library.find((b) => b.id === book.id);
        if (entry) { entry.percent = percent; entry.position = { section: app.reading.section, offset: app.reading.offset }; }
      }
    };
    if (now) run(); else saveTimer = setTimeout(run, 500);
  }

  // Show the book where it was left (also when coming back from another tab).
  let openedId = null;
  $effect(() => {
    if (book && book.id !== openedId) {
      openedId = book.id;
      untrack(() => load(app.reading.section, { offset: app.reading.offset }));
    }
  });

  function onPageClick(event) {
    const link = event.target.closest('a');
    const sentence = event.target.closest('.s');
    if (!link && sentence && player.active) { play(Number(sentence.dataset.i)); return; }
    if (!link) return;
    event.preventDefault();
    if (link.dataset.ext) call('open_external', link.dataset.ext);
    else if (link.dataset.section !== undefined) {
      const number = Number(link.dataset.section);
      const anchor = link.dataset.anchor || '';
      if (number === section && anchor) pageWrap.querySelector(`[id="${CSS.escape(anchor)}"]`)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
      else load(number, { anchor });
    }
  }

  function visibleSnippet() {
    const top = pageWrap.getBoundingClientRect().top;
    const block = [...pageWrap.querySelectorAll('article p, article h1, article h2, article h3, article li')]
      .find((el) => el.getBoundingClientRect().bottom > top + 40 && el.textContent.trim());
    const text = (block?.textContent ?? '').replace(/\s+/g, ' ').trim();
    return text.length > 90 ? text.slice(0, 90) + '…' : text;
  }

  async function toggleBookmark() {
    if (!book) return;
    app.reading.offset = currentOffset();
    const result = bookmarkHere
      ? await act('reader_remove_bookmark', book.id, bookmarkHere.id)
      : await act('reader_add_bookmark', book.id, section, app.reading.offset, visibleSnippet() || sectionTitle);
    if (result) book.bookmarks = result;
  }

  async function removeBookmark(id) {
    const result = await act('reader_remove_bookmark', book.id, id);
    if (result) book.bookmarks = result;
  }

  function setPref(key, value) {
    app.reader[key] = value;
    saveReaderPrefs();
  }

  function keydown(event) {
    if (event.target.closest('input, textarea, select')) return;
    if (event.key === 'Escape') { showAa = false; }
    if (event.code === 'Space' && player.active && !event.target.closest('button')) { event.preventDefault(); togglePlay(); }
    if (event.altKey && event.key === 'ArrowRight') load(section + 1);
    if (event.altKey && event.key === 'ArrowLeft') load(section - 1);
  }

  function close() {
    savePosition(true);
    onclose();
  }

  const fonts = { serif: 'var(--serif)', sans: 'var(--sans)' };
</script>

<svelte:window onkeydown={keydown} onclick={(e) => { if (!e.target.closest('.aa, .aa-btn')) showAa = false; }} />

{#if book}
  <section class="reader">
    <div class="bar-top">
      <button class="icon-btn" title="Kitaplık" onclick={close}><Icon name="back" /></button>
      <button class="icon-btn" class:on={panel} title="İçindekiler ve yer imleri" onclick={() => (panel = panel ? null : 'toc')}><Icon name="list" /></button>
      <div class="ttl"><b>{book.title}</b><small>{sectionTitle}</small></div>
      <button class="icon-btn" class:on={bookmarkHere} title={bookmarkHere ? 'Yer imini kaldır' : 'Yer imi ekle'} onclick={toggleBookmark}>
        <Icon name={bookmarkHere ? 'bookmarkOn' : 'bookmark'} /></button>
      <button class="icon-btn aa-btn" class:on={showAa} title="Görünüm" onclick={() => (showAa = !showAa)}><Icon name="type" /></button>
    </div>

    {#if showAa}
      <div class="aa">
        <div class="r">Yazı boyutu
          <div class="seg">
            <button onclick={() => setPref('size', Math.max(14, app.reader.size - 1))}><Icon name="minus" size={14} /></button>
            <span class="val">{app.reader.size}</span>
            <button onclick={() => setPref('size', Math.min(32, app.reader.size + 1))}><Icon name="plus" size={14} /></button>
          </div>
        </div>
        <div class="r">Yazı tipi
          <div class="seg">
            <button class:on={app.reader.font === 'serif'} onclick={() => setPref('font', 'serif')}>Serif</button>
            <button class:on={app.reader.font === 'sans'} onclick={() => setPref('font', 'sans')}>Sans</button>
          </div>
        </div>
        <div class="r">Satır aralığı
          <div class="seg">
            {#each [[1.5, 'Sık'], [1.75, 'Normal'], [2.05, 'Geniş']] as [value, label]}
              <button class:on={app.reader.leading === value} onclick={() => setPref('leading', value)}>{label}</button>
            {/each}
          </div>
        </div>
        <div class="r">Sütun genişliği
          <div class="seg">
            {#each [[600, 'Dar'], [680, 'Orta'], [820, 'Geniş']] as [value, label]}
              <button class:on={app.reader.width === value} onclick={() => setPref('width', value)}>{label}</button>
            {/each}
          </div>
        </div>
        <div class="r">Sayfa
          <div class="papers">
            {#each Object.entries(PAPERS) as [key, value]}
              <button class="paper" class:on={app.reader.paper === key} title={{ theme: 'Tema', dark: 'Koyu', sepia: 'Sepya', light: 'Açık' }[key]}
                style:background={value ? value.bg : 'var(--panel)'} style:color={value ? value.ink : 'var(--text)'}
                onclick={() => setPref('paper', key)}>A</button>
            {/each}
          </div>
        </div>
        <button class="btn sm reset" onclick={() => { app.reader = { ...READER_DEFAULTS }; saveReaderPrefs(); }}>Varsayılana dön</button>
      </div>
    {/if}

    <div class="body">
      {#if panel}
        <nav class="side">
          <div class="tabs">
            <button class="tab" class:on={panel === 'toc'} onclick={() => (panel = 'toc')}>İçindekiler</button>
            <button class="tab" class:on={panel === 'bookmarks'} onclick={() => (panel = 'bookmarks')}>Yer imleri {book.bookmarks.length || ''}</button>
          </div>
          <div class="list">
            {#if panel === 'toc'}
              {#each book.toc as entry, i}
                <button class="entry" class:on={i === currentToc} style:padding-left="{12 + entry.depth * 14}px"
                  onclick={() => load(entry.section, { anchor: entry.anchor })}>{entry.title}</button>
              {/each}
            {:else}
              {#each book.bookmarks as mark (mark.id)}
                <div class="mark">
                  <button class="entry" onclick={() => load(mark.section, { offset: mark.offset })}>
                    <small>{book.sections[mark.section]?.title || `Bölüm ${mark.section + 1}`}</small>{mark.label}</button>
                  <button class="icon-btn del" title="Yer imini sil" onclick={() => removeBookmark(mark.id)}><Icon name="x" size={14} /></button>
                </div>
              {:else}
                <div class="empty">Henüz yer imi yok. Üstteki yer imi düğmesiyle bulunduğunuz yeri işaretleyin.</div>
              {/each}
            {/if}
          </div>
        </nav>
      {/if}

      <div class="page-wrap" bind:this={pageWrap} onscroll={() => savePosition()}
        style:--paper={paper ? paper.bg : 'var(--panel)'} style:--ink={paper ? paper.ink : 'var(--text)'}>
        <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
        <article class="page selectable" class:faded={loading} class:listening={player.active} bind:this={article} onclick={onPageClick}
          style:font-size="{app.reader.size}px" style:line-height={app.reader.leading}
          style:font-family={fonts[app.reader.font]} style:max-width="{app.reader.width}px">
          {@html html}
          {#if html && section < sectionCount - 1}
            <button class="next-section" onclick={() => load(section + 1)}>Sonraki bölüm <Icon name="forward" size={16} /></button>
          {:else if html}
            <div class="the-end no-tts">Kitabın sonu</div>
          {/if}
        </article>
      </div>
    </div>

    <div class="player">
      <div class="track book" title="Kitapta %{Math.round(app.reading.percent)}"><i style:width="{app.reading.percent}%"></i></div>
      <div class="left">
        <button class="icon-btn" title="Önceki bölüm (Alt+←)" disabled={section <= 0} onclick={() => load(section - 1)}><Icon name="back" /></button>
        <div class="where">
          <b>Bölüm {section + 1} / {sectionCount} · %{Math.round(app.reading.percent)}</b>
          <small>{#if app.tts.state === 'loading'}Ses modeli yükleniyor…{:else if player.active}Cümle {player.index + 1} / {player.total}{#if player.waiting} · hazırlanıyor…{/if}{:else}{sectionTitle}{/if}</small>
        </div>
      </div>
      <div class="controls">
        <button class="icon-btn" title="Önceki cümle" disabled={!player.active} onclick={() => step(-1)}><Icon name="prev" /></button>
        <button class="play" title={player.playing ? 'Duraklat (Boşluk)' : 'Sesli oku'} onclick={togglePlay}>
          <Icon name={player.playing ? 'pause' : 'play'} size={20} /></button>
        <button class="icon-btn" title="Sonraki cümle" disabled={!player.active} onclick={() => step(1)}><Icon name="next" /></button>
        {#if player.active}<button class="icon-btn" title="Okumayı bitir" onclick={() => { stop(); highlight(-1); }}><Icon name="stop" /></button>{/if}
      </div>
      <div class="right">
        <button class="chip acc" title="Okuma hızı" onclick={cycleSpeed}>{app.ttsPrefs.speed.toLocaleString('tr-TR')}×</button>
        <button class="chip" title="Bu bölümü WAV ses dosyası olarak kaydet" disabled={Boolean(app.ttsExport)} onclick={exportSection}>
          <Icon name="download" size={14} />{app.ttsExport ? `Kaydediliyor ${app.ttsExport.done}/${app.ttsExport.total}` : 'Kaydet'}</button>
        <button class="icon-btn" title="Sonraki bölüm (Alt+→)" disabled={section >= sectionCount - 1} onclick={() => load(section + 1)}><Icon name="forward" /></button>
      </div>
    </div>
  </section>
{/if}

<style>
  .reader { display: flex; flex-direction: column; height: 100%; min-height: 0; position: relative; }
  .bar-top { display: flex; align-items: center; gap: 6px; padding: 12px 20px; border-bottom: 1px solid var(--outline); }
  .ttl { flex: 1; min-width: 0; margin-left: 6px; }
  .ttl b, .ttl small { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ttl small { color: var(--dim); }
  .icon-btn.on { color: var(--accent); }

  .aa { position: absolute; right: 20px; top: 62px; width: 300px; background: var(--panel); border: 1px solid var(--outline); border-radius: 14px;
    padding: 10px 14px 14px; box-shadow: var(--shadow); z-index: 10; }
  .aa .r { display: flex; align-items: center; justify-content: space-between; padding: 7px 0; font-size: 13px; color: var(--secondary); gap: 10px; }
  .seg { display: flex; align-items: center; background: var(--surface); border-radius: 999px; padding: 3px; }
  .seg button { padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; color: var(--secondary); display: grid; place-items: center; }
  .seg button.on { background: var(--surface-active); color: var(--text); }
  .seg button:hover { color: var(--text); }
  .val { min-width: 26px; text-align: center; font-weight: 700; font-size: 12px; color: var(--text); }
  .papers { display: flex; gap: 8px; }
  .paper { width: 30px; height: 30px; border-radius: 50%; border: 2px solid var(--outline); font: 600 12px var(--serif); }
  .paper.on { border-color: var(--accent); }
  .reset { width: 100%; margin-top: 8px; }

  .body { flex: 1; display: flex; min-height: 0; }
  .side { width: 280px; flex: none; border-right: 1px solid var(--outline); background: var(--panel); display: flex; flex-direction: column; min-height: 0; }
  .tabs { display: flex; gap: 6px; padding: 12px 10px 8px; }
  .tab { padding: 5px 12px; border-radius: 999px; font-weight: 600; font-size: 12px; color: var(--secondary); }
  .tab.on { background: var(--text); color: var(--window); }
  .list { overflow: auto; padding: 0 8px 12px; }
  .entry { display: block; width: 100%; text-align: left; padding: 7px 12px; border-radius: 8px; color: var(--secondary); font-size: 13px; }
  .entry:hover { background: var(--surface-hover); color: var(--text); }
  .entry.on { color: var(--accent); background: var(--surface); font-weight: 600; }
  .entry small { display: block; color: var(--dim); font-size: 11px; }
  .mark { display: flex; align-items: center; }
  .mark .entry { flex: 1; min-width: 0; }
  .del { width: 28px; height: 28px; opacity: 0; }
  .mark:hover .del { opacity: 1; }

  .page-wrap { flex: 1; overflow: auto; background: var(--paper); color: var(--ink); }
  .page { margin: 0 auto; padding: 48px 32px 80px; color: var(--ink); transition: opacity .15s; overflow-wrap: break-word; }
  .page.faded { opacity: .4; }
  .page :global(p) { margin: 0 0 .2em; text-indent: 1.5em; }
  .page :global(p:first-child), .page :global(h1 + p), .page :global(h2 + p), .page :global(h3 + p), .page :global(hr + p) { text-indent: 0; }
  .page :global(h1), .page :global(h2), .page :global(h3), .page :global(h4) { font-weight: 600; line-height: 1.3; margin: 1.4em 0 .8em; text-align: center; text-indent: 0; }
  .page :global(h1) { font-size: 1.5em; }
  .page :global(h2) { font-size: 1.3em; }
  .page :global(h3) { font-size: 1.1em; }
  .page :global(img) { max-width: 100%; height: auto; display: block; margin: 1em auto; border-radius: 4px; }
  .page :global(blockquote) { margin: 1em 0; padding-left: 1em; border-left: 3px solid color-mix(in srgb, var(--ink) 25%, transparent); font-style: italic; }
  .page :global(hr) { border: 0; text-align: center; margin: 1.5em 0; }
  .page :global(hr)::after { content: '* * *'; letter-spacing: .5em; color: color-mix(in srgb, var(--ink) 50%, transparent); }
  .page :global(a) { color: var(--accent); text-decoration: none; cursor: pointer; }
  .page :global(a:hover) { text-decoration: underline; }
  .page :global(ul), .page :global(ol) { padding-left: 1.5em; margin: .6em 0; }
  .page :global(table) { border-collapse: collapse; margin: 1em 0; font-size: .9em; }
  .page :global(td), .page :global(th) { border: 1px solid color-mix(in srgb, var(--ink) 25%, transparent); padding: .3em .6em; }
  .page :global(.align-center) { text-align: center; text-indent: 0; }
  .page :global(.align-right) { text-align: right; text-indent: 0; }
  .page :global(sup) { line-height: 0; }
  .page :global([data-note]) { font-size: .85em; opacity: .85; }
  .next-section { display: flex; align-items: center; gap: 6px; margin: 48px auto 0; padding: 10px 18px; border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 10%, transparent); color: var(--ink); font: 600 14px var(--sans); }
  .next-section:hover { background: color-mix(in srgb, var(--ink) 16%, transparent); }
  .the-end { text-align: center; margin-top: 48px; font: 600 13px var(--sans); letter-spacing: .12em; text-transform: uppercase; opacity: .5; }

  .page.listening :global(.s) { cursor: pointer; border-radius: 3px; }
  .page.listening :global(.s:hover) { background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .page :global(.s.now) { background: color-mix(in srgb, var(--accent) 26%, transparent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 26%, transparent); }

  .player { position: relative; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 16px; padding: 10px 16px; border-top: 1px solid var(--outline); background: var(--panel); }
  .player .book { position: absolute; left: 0; right: 0; top: -1px; height: 2px; border-radius: 0; background: transparent; }
  .left, .right { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .right { justify-content: flex-end; }
  .where { min-width: 0; }
  .where b, .where small { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .where b { font-size: 13px; }
  .where small { color: var(--dim); font-size: 12px; }
  .controls { display: flex; align-items: center; gap: 6px; }
  .play { width: 44px; height: 44px; border-radius: 50%; background: var(--text); color: var(--window); display: grid; place-items: center; }
  .play:hover { transform: scale(1.05); }
  .chip { height: 30px; padding: 0 12px; border-radius: 999px; background: var(--surface); font-size: 12px; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
  .chip:not(:disabled):hover { background: var(--surface-hover); }
  .chip.acc { color: var(--accent); min-width: 54px; justify-content: center; }
</style>
