<script>
  import Modal from './Modal.svelte';
  import Icon from '../lib/Icon.svelte';

  // rows: [{source, target}]; onsave(rows) may return an error string to keep the dialog open.
  let { rows: initial = [], title = 'Terim sözlüğü', onsave, oncancel } = $props();
  // The dialog edits its own copy of the initial rows.
  // svelte-ignore state_referenced_locally
  let rows = $state(initial.length ? initial.map((r) => ({ ...r })) : [{ source: '', target: '' }]);
  let error = $state('');
  let saving = $state(false);
  let filter = $state('');

  const visible = $derived(
    rows.map((row, index) => ({ row, index }))
      .filter(({ row }) => !filter || (row.source + ' ' + row.target).toLocaleLowerCase('tr').includes(filter.toLocaleLowerCase('tr'))),
  );

  function add() {
    filter = '';
    rows.push({ source: '', target: '' });
    queueMicrotask(() => document.querySelector('.gl-table tr:last-child input')?.focus());
  }

  async function save() {
    saving = true;
    error = (await onsave(rows.map((r) => ({ source: r.source.trim(), target: r.target.trim() })))) || '';
    saving = false;
  }
</script>

<Modal {title} width={680} onclose={oncancel}>
  <p class="hint">İngilizce terim ile zorunlu Türkçe karşılığını girin. Sözlük boş bırakılabilir.</p>
  <div class="tools">
    <input class="field" placeholder="Sözlükte ara" bind:value={filter} />
    <span class="count">{rows.filter((r) => r.source.trim()).length} terim</span>
    <button class="btn sm" onclick={add}><Icon name="plus" size={14} />Satır ekle</button>
  </div>
  <div class="table-wrap">
    <table class="gl-table">
      <thead><tr><th>İngilizce terim</th><th>Türkçe karşılık</th><th></th></tr></thead>
      <tbody>
        {#each visible as { row, index } (index)}
          <tr>
            <td><input class="field" bind:value={row.source} placeholder="Bard" /></td>
            <td><input class="field" bind:value={row.target} placeholder="Ozan" /></td>
            <td><button class="icon-btn" title="Satırı sil" onclick={() => rows.splice(index, 1)}><Icon name="trash" size={16} /></button></td>
          </tr>
        {/each}
      </tbody>
    </table>
    {#if !visible.length}<div class="empty">Eşleşen terim yok.</div>{/if}
  </div>
  {#if error}<div class="err"><Icon name="alert" size={16} />{error}</div>{/if}
  {#snippet footer()}
    <button class="btn" onclick={oncancel}>İptal</button>
    <button class="btn primary" disabled={saving} onclick={save}>Kaydet</button>
  {/snippet}
</Modal>

<style>
  .hint { color: var(--secondary); font-size: 13px; }
  .tools { display: flex; gap: 10px; align-items: center; }
  .tools .field { flex: 1; }
  .count { color: var(--dim); font-size: 12px; }
  .table-wrap { max-height: 46vh; overflow: auto; border: 1px solid var(--outline); border-radius: 10px; }
  table { width: 100%; border-collapse: collapse; }
  th { position: sticky; top: 0; background: var(--surface); text-align: left; font-size: 12px; color: var(--dim); font-weight: 600; padding: 8px 10px; }
  td { padding: 4px 6px; border-top: 1px solid var(--outline); }
  td:last-child { width: 44px; }
  td .field { width: 100%; background: transparent; border-color: transparent; }
  td .field:focus { background: var(--surface); border-color: var(--accent); }
  .err { display: flex; gap: 8px; align-items: center; color: var(--danger); font-size: 13px; }
</style>
