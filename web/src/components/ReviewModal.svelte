<script>
  import Modal from './Modal.svelte';

  // data: {current: [{source,target}], proposed: [{source,target,scope,reason}]}
  let { data, onsubmit } = $props();
  // The dialog edits its own copy of the initial rows.
  // svelte-ignore state_referenced_locally
  let current = $state(data.current.map((r) => ({ ...r })));
  // svelte-ignore state_referenced_locally
  let proposed = $state(data.proposed.map((r) => ({ ...r, checked: r.scope === 'book' })));

  function accept() {
    const rows = [...current, ...proposed.filter((r) => r.checked)]
      .map((r) => ({ source: r.source.trim(), target: r.target.trim() }))
      .filter((r) => r.source && r.target);
    onsubmit(rows);
  }
</script>

<Modal title="Terim önerileri" width={760}>
  <p class="hint">
    Ön analiz bu terimleri önerdi. Mevcut sözlük satırları korunur. Kitap geneli öneriler işaretli, yalnızca bir
    bölüme özgü öneriler işaretsiz gelir. Reddederseniz sözlük değişmez; çeviri her iki durumda da devam eder.
  </p>
  <div class="table-wrap">
    <table>
      <thead><tr><th></th><th>Kaynak terim</th><th>Karşılık</th><th>Not</th></tr></thead>
      <tbody>
        {#each proposed as row}
          <tr>
            <td><input type="checkbox" bind:checked={row.checked} /></td>
            <td><input class="field" bind:value={row.source} /></td>
            <td><input class="field" bind:value={row.target} /></td>
            <td class="note">
              <span class="badge" class:active={row.scope === 'book'}>{row.scope === 'book' ? 'kitap geneli' : 'bölüm özel'}</span>
              {row.reason}
            </td>
          </tr>
        {/each}
        {#each current as row}
          <tr class="existing">
            <td><span class="badge done">mevcut</span></td>
            <td class="fixed">{row.source}</td>
            <td><input class="field" bind:value={row.target} /></td>
            <td class="note">sözlükte var</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  {#snippet footer()}
    <button class="btn" onclick={() => onsubmit(null)}>Önerileri reddet</button>
    <button class="btn primary" onclick={accept}>Onayla</button>
  {/snippet}
</Modal>

<style>
  .hint { color: var(--secondary); font-size: 13px; line-height: 1.55; }
  .table-wrap { max-height: 52vh; overflow: auto; border: 1px solid var(--outline); border-radius: 10px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { position: sticky; top: 0; background: var(--surface); text-align: left; font-size: 12px; color: var(--dim); font-weight: 600; padding: 8px 10px; z-index: 1; }
  td { padding: 4px 8px; border-top: 1px solid var(--outline); vertical-align: middle; }
  td:first-child { width: 72px; text-align: center; }
  td .field { width: 100%; background: transparent; border-color: transparent; }
  td .field:focus { background: var(--surface); border-color: var(--accent); }
  .fixed { padding-left: 18px; }
  .note { color: var(--dim); font-size: 12px; }
  .note .badge { margin-right: 6px; }
  input[type='checkbox'] { accent-color: var(--accent); width: 16px; height: 16px; }
</style>
