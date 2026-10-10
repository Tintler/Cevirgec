<script>
  import Modal from './Modal.svelte';

  // chapters: [{filename, title, story, checked}]
  let { chapters, onconfirm, oncancel } = $props();
  // The dialog edits its own copy of the initial rows.
  // svelte-ignore state_referenced_locally
  let items = $state(chapters.map((c) => ({ ...c })));
  const selected = $derived(items.filter((c) => c.checked).length);

  const setAll = (value) => items.forEach((c) => (c.checked = value));
  const guessStory = () => items.forEach((c) => (c.checked = c.story));
</script>

<Modal title="Ön analize dahil edilecek bölümler" width={620} onclose={oncancel}>
  <p class="hint">Yalnızca hikâye bölümlerini işaretleyin. Seçilmeyen bölümler çevrilir fakat ön analize girmez.</p>
  <div class="tools">
    <button class="btn sm" onclick={() => setAll(true)}>Tümünü seç</button>
    <button class="btn sm" onclick={() => setAll(false)}>Tümünü kaldır</button>
    <button class="btn sm" onclick={guessStory}>Hikâyeyi tahmin et</button>
    <span class="count">{selected} / {items.length} seçili</span>
  </div>
  <div class="list">
    {#each items as item}
      <label class="row">
        <input type="checkbox" bind:checked={item.checked} />
        <span class="t">{item.title}</span>
        <span class="f">{item.filename}</span>
      </label>
    {/each}
  </div>
  {#snippet footer()}
    <button class="btn" onclick={oncancel}>İptal</button>
    <button class="btn primary" disabled={!selected} onclick={() => onconfirm(items.filter((c) => c.checked).map((c) => c.filename))}>Başlat</button>
  {/snippet}
</Modal>

<style>
  .hint { color: var(--secondary); font-size: 13px; }
  .tools { display: flex; gap: 8px; align-items: center; }
  .count { margin-left: auto; color: var(--dim); font-size: 12px; }
  .list { max-height: 50vh; overflow: auto; border: 1px solid var(--outline); border-radius: 10px; padding: 4px; }
  .row { display: flex; align-items: center; gap: 10px; padding: 7px 10px; border-radius: 8px; cursor: pointer; }
  .row:hover { background: var(--surface-hover); }
  .t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .f { color: var(--dim); font-size: 12px; }
  input[type='checkbox'] { accent-color: var(--accent); width: 16px; height: 16px; }
</style>
