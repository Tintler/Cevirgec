<script>
  import Icon from '../lib/Icon.svelte';
  import Cover from '../components/Cover.svelte';
  import { app, act, toast } from '../lib/state.svelte.js';
  import { confirm } from '../lib/confirm.svelte.js';

  let { onopen } = $props();
  let menu = $state(null);

  const recent = $derived(app.library.find((b) => b.opened && b.exists && b.percent < 100) ?? null);

  async function add() {
    const result = await act('library_add');
    if (result) app.library = result.books;
  }

  async function remove(book) {
    menu = null;
    const yes = await confirm({
      title: 'Kitaplıktan kaldır',
      message: `"${book.title}" kitaplıktan kaldırılsın mı? Dosyanın kendisi silinmez; okuma konumu ve yer imleri silinir.`,
      ok: 'Kaldır', cancel: 'Vazgeç',
    });
    if (!yes) return;
    const books = await act('library_remove', book.id);
    if (books) { app.library = books; toast('Kitap kaldırıldı.'); }
  }

  function open(book) {
    if (!book.exists) { toast('Kitap dosyası bulunamadı: ' + book.path, 'error'); return; }
    onopen(book);
  }

  const progressLabel = (b) => (!b.exists ? 'Dosya bulunamadı' : b.percent >= 100 ? 'Bitti' : b.percent ? `%${Math.round(b.percent)} okundu` : 'Yeni');
</script>

<svelte:window onclick={() => (menu = null)} />

<section class="screen">
  <div class="top">
    <h1>Okuma</h1>
    <button class="btn primary" onclick={add}><Icon name="plus" />Kitap ekle</button>
  </div>

  <div class="scroll">
    {#if recent}
      <div class="hero card">
        <div class="hc"><Cover book={recent} /></div>
        <div class="hm">
          <div class="k">Okumaya devam et</div>
          <div class="t">{recent.title}</div>
          <div class="s">{[recent.author, `%${Math.round(recent.percent)} okundu`].filter(Boolean).join(' · ')}</div>
          <div class="track"><i style:width="{recent.percent}%"></i></div>
          <button class="btn primary" onclick={() => open(recent)}><Icon name="book" />Devam et</button>
        </div>
      </div>
    {/if}

    <h2>Kitaplığım <small>{app.library.length || ''}</small></h2>
    <div class="shelf">
      {#each app.library as book (book.id)}
        <button class="bk" class:missing={!book.exists} onclick={() => open(book)} title={book.path}
          oncontextmenu={(e) => { e.preventDefault(); menu = { x: e.clientX, y: e.clientY, book }; }}>
          <Cover {book} />
          <span class="t">{book.title}</span>
          <span class="s">{progressLabel(book)}</span>
          <span class="p"><i style:width="{book.percent}%"></i></span>
          <span class="x" role="presentation" title="Kitaplıktan kaldır" onclick={(e) => { e.stopPropagation(); remove(book); }}><Icon name="x" size={14} /></span>
        </button>
      {/each}
      <button class="drop" onclick={add}>
        <Icon name="plus" size={28} />
        <span>EPUB ekleyin</span>
        <small>ya da pencereye sürükleyip bırakın</small>
      </button>
    </div>
  </div>
</section>

{#if menu}
  <div class="menu" style:left="{menu.x}px" style:top="{menu.y}px">
    <button onclick={() => open(menu.book)}><Icon name="book" size={15} />Aç</button>
    <button onclick={() => remove(menu.book)}><Icon name="trash" size={15} />Kitaplıktan kaldır</button>
  </div>
{/if}

<style>
  .screen { display: flex; flex-direction: column; gap: 16px; padding: 18px 0 0 28px; height: 100%; min-height: 0; }
  .top { display: flex; align-items: center; gap: 10px; padding-right: 28px; }
  .top h1 { font-size: 28px; font-weight: 700; letter-spacing: -.02em; flex: 1; }
  .scroll { overflow: auto; padding: 0 28px 28px 0; display: flex; flex-direction: column; gap: 22px; }

  .hero { display: flex; gap: 22px; align-items: center; padding: 20px; background: linear-gradient(120deg, color-mix(in srgb, var(--accent) 14%, var(--panel)), var(--panel) 70%); }
  .hc { width: 96px; flex: none; }
  .hm { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
  .k { font-size: 12px; font-weight: 600; color: var(--secondary); text-transform: uppercase; letter-spacing: .06em; }
  .hm .t { font-size: 24px; font-weight: 700; }
  .hm .s { color: var(--secondary); }
  .hm .track { width: min(360px, 100%); flex: none; margin: 8px 0 12px; }

  h2 { font-size: 20px; font-weight: 700; }
  h2 small { color: var(--dim); font-size: 14px; font-weight: 500; margin-left: 6px; }
  .shelf { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 18px; }
  .bk { display: flex; flex-direction: column; text-align: left; padding: 10px; border-radius: 12px; position: relative; min-width: 0; }
  .bk:hover { background: var(--surface); }
  .bk.missing { opacity: .5; }
  .bk .t { margin-top: 10px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .bk .s { color: var(--dim); font-size: 12px; }
  .bk .p { height: 3px; border-radius: 3px; background: var(--surface-active); margin-top: 8px; overflow: hidden; }
  .bk .p i { display: block; height: 100%; background: var(--accent); }
  .bk .x { position: absolute; top: 16px; right: 16px; width: 26px; height: 26px; border-radius: 50%; display: none; place-items: center;
    background: rgba(0, 0, 0, .65); color: #fff; }
  .bk:hover .x { display: grid; }
  .drop { border: 2px dashed var(--outline); border-radius: 12px; display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 6px; color: var(--dim); aspect-ratio: 2 / 3; margin: 10px; font-size: 13px; text-align: center; padding: 12px; }
  .drop small { font-size: 11px; }
  .drop:hover { border-color: var(--accent); color: var(--accent); }

  .menu { position: fixed; z-index: 40; background: var(--panel); border: 1px solid var(--outline); border-radius: 10px; padding: 4px; box-shadow: var(--shadow); }
  .menu button { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 7px; font-size: 13px; width: 100%; }
  .menu button:hover { background: var(--surface-hover); }
</style>
