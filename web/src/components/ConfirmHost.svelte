<script>
  import Modal from './Modal.svelte';
  import { pending } from '../lib/confirm.svelte.js';

  function done(value) {
    const current = pending.current;
    pending.current = null;
    current?.resolve(value);
  }
</script>

{#if pending.current}
  {@const c = pending.current}
  <Modal title={c.title} width={500} onclose={() => done(false)}>
    <p class="msg">{c.message}</p>
    {#if c.list?.length}
      <ul>{#each c.list as item}<li>{item}</li>{/each}</ul>
    {/if}
    {#snippet footer()}
      {#if c.cancel}<button class="btn" onclick={() => done(false)}>{c.cancel}</button>{/if}
      <button class="btn primary" class:danger={c.danger} onclick={() => done(true)}>{c.ok}</button>
    {/snippet}
  </Modal>
{/if}

<style>
  .msg { color: var(--secondary); white-space: pre-line; line-height: 1.55; }
  ul { list-style: none; background: var(--surface); border-radius: 10px; padding: 8px 12px; max-height: 220px; overflow: auto; font-size: 13px; }
  li { padding: 3px 0; color: var(--text); }
</style>
