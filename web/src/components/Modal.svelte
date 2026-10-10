<script>
  import Icon from '../lib/Icon.svelte';
  let { title, width = 560, onclose = null, children, footer } = $props();

  function keydown(event) {
    if (event.key === 'Escape' && onclose) onclose();
  }
</script>

<svelte:window onkeydown={keydown} />

<div class="backdrop">
  <div class="modal" style:width="{width}px" role="dialog" aria-modal="true" aria-label={title}>
    <header>
      <h2>{title}</h2>
      {#if onclose}<button class="icon-btn" onclick={onclose} title="Kapat"><Icon name="x" /></button>{/if}
    </header>
    <div class="body">{@render children()}</div>
    {#if footer}<footer>{@render footer()}</footer>{/if}
  </div>
</div>

<style>
  .backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, .55); display: grid; place-items: center; z-index: 50; padding: 24px; }
  .modal {
    max-width: 100%; max-height: 100%; display: flex; flex-direction: column; background: var(--panel);
    border: 1px solid var(--outline); border-radius: 16px; box-shadow: var(--shadow);
  }
  header { display: flex; align-items: center; gap: 12px; padding: 16px 16px 8px 22px; }
  h2 { flex: 1; font-size: 17px; font-weight: 700; }
  .body { padding: 6px 22px 16px; overflow: auto; min-height: 0; display: flex; flex-direction: column; gap: 12px; }
  footer { display: flex; align-items: center; gap: 8px; justify-content: flex-end; padding: 12px 22px 18px; border-top: 1px solid var(--outline); }
</style>
