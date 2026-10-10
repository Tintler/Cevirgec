<script>
  let { checked = $bindable(false), disabled = false, label = '' } = $props();
</script>

<button type="button" class="toggle" class:on={checked} {disabled} role="switch" aria-checked={checked}
  onclick={() => (checked = !checked)}>
  <span class="sw"></span>{#if label}<span>{label}</span>{/if}
</button>

<style>
  .toggle { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; color: var(--secondary); }
  .sw { width: 34px; height: 20px; border-radius: 999px; background: var(--surface-active); position: relative; transition: .15s; flex: none; }
  .sw::after { content: ''; position: absolute; top: 3px; left: 3px; width: 14px; height: 14px; border-radius: 50%; background: var(--secondary); transition: .15s; }
  .on .sw { background: var(--accent); }
  .on .sw::after { left: 17px; background: var(--on-accent); }
</style>
