<script>
  import Icon from './lib/Icon.svelte';
  import Translate from './views/Translate.svelte';
  import Reading from './views/Reading.svelte';
  import Settings from './views/Settings.svelte';
  import ReviewModal from './components/ReviewModal.svelte';
  import ConfirmHost from './components/ConfirmHost.svelte';
  import { app, init, toasts, setTheme, dismissToast } from './lib/state.svelte.js';
  import { call } from './lib/api.js';
  import logo from '../../assets/app.svg';

  init();

  const nav = [
    ['translate', 'Çeviri', 'languages'],
    ['reading', 'Okuma', 'book'],
    ['settings', 'Ayarlar', 'settings'],
  ];

  const models = $derived(app.models);
  const modelLoaded = $derived((models.loaded ?? []).find((m) => m.name === models.model));

  async function submitReview(values) {
    app.review = null;
    await call('submit_review', values);
  }
</script>

<div class="shell">
  <aside class="side">
    <div class="brand"><img src={logo} alt="" /><span>Çevirgeç</span></div>
    {#each nav as [key, label, icon]}
      <button class="nav" class:on={app.view === key} onclick={() => (app.view = key)}><Icon name={icon} /><span>{label}</span></button>
    {/each}
    <div class="grow"></div>
    <button class="icon-btn theme" title="Temayı değiştir" onclick={() => setTheme(app.theme === 'dark' ? 'light' : 'dark')}>
      <Icon name={app.theme === 'dark' ? 'moon' : 'sun'} /></button>
    <div class="lm">
      <div class="row">
        <span class="dot {models.state === 'probing' ? 'wait' : models.state === 'failed' ? 'bad' : modelLoaded ? '' : 'wait'}"></span>
        <span class="st">{models.state === 'probing' ? 'LM Studio sorgulanıyor…' : models.state === 'failed' ? 'LM Studio bağlı değil' : modelLoaded ? 'LM Studio bağlı' : (models.loaded ?? []).length ? 'Seçili model yüklü değil' : 'Yüklü model yok'}</span>
        <button class="icon-btn mini" title="Yenile" onclick={() => call('refresh_models')}><Icon name="refresh" size={14} /></button>
      </div>
      <div class="m selectable">{models.model}</div>
      {#if modelLoaded?.context}<div class="c">{Number(modelLoaded.context).toLocaleString('tr-TR')} context</div>{/if}
      {#if models.state === 'failed'}<div class="c selectable" title={models.message}>{models.message}</div>{/if}
    </div>
  </aside>

  <main>
    {#if app.view === 'translate'}<Translate />
    {:else if app.view === 'reading'}<Reading />
    {:else}<Settings />{/if}
  </main>
</div>

{#if app.review}<ReviewModal data={app.review} onsubmit={submitReview} />{/if}
<ConfirmHost />

<div class="toasts">
  {#each toasts as t (t.id)}
    <div class="toast {t.kind}">
      <Icon name={t.kind === 'error' ? 'alert' : 'check'} size={16} /><span class="selectable">{t.message}</span>
      <button class="close" title="Kapat" onclick={() => dismissToast(t.id)}><Icon name="x" size={14} /></button>
    </div>
  {/each}
</div>

<style>
  .shell { display: grid; grid-template-columns: 232px 1fr; height: 100%; }
  .side { background: var(--panel); border-right: 1px solid var(--outline); display: flex; flex-direction: column; padding: 18px 12px; gap: 4px; min-height: 0; }
  .brand { display: flex; align-items: center; gap: 10px; padding: 4px 10px 20px; font-weight: 700; font-size: 19px; }
  .brand img { width: 32px; height: 32px; }
  .nav { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: 10px; color: var(--secondary); font-weight: 600; font-size: 15px; width: 100%; text-align: left; }
  .nav:hover { background: var(--surface-hover); color: var(--text); }
  .nav.on { background: var(--surface); color: var(--text); }
  .nav.on :global(svg) { color: var(--accent); }
  .grow { flex: 1; }
  .theme { margin: 0 0 4px 4px; }
  .lm { margin: 4px; padding: 12px; border-radius: 12px; background: var(--surface); font-size: 12px; }
  .lm .row { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 13px; }
  .lm .st { flex: 1; min-width: 0; }
  .mini { width: 26px; height: 26px; margin: -4px -6px -4px 0; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 25%, transparent); flex: none; }
  .dot.wait { background: var(--warn); box-shadow: 0 0 0 3px color-mix(in srgb, var(--warn) 25%, transparent); }
  .dot.bad { background: var(--danger); box-shadow: 0 0 0 3px color-mix(in srgb, var(--danger) 25%, transparent); }
  .lm .m { color: var(--secondary); margin-top: 6px; word-break: break-all; }
  .lm .c { color: var(--dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  main { min-width: 0; min-height: 0; height: 100%; }

  .toasts { position: fixed; right: 20px; bottom: 84px; display: flex; flex-direction: column; gap: 8px; z-index: 60; max-width: 420px; }
  .toast { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: 12px; background: var(--surface-active); border: 1px solid var(--outline); box-shadow: var(--shadow); font-size: 13px; }
  .toast > span { flex: 1; }
  .toast .close { color: var(--dim); margin: -2px -4px 0 0; display: grid; place-items: center; }
  .toast .close:hover { color: var(--text); }
  .toast.ok > :global(svg) { color: var(--ok); }
  .toast.error { border-color: color-mix(in srgb, var(--danger) 50%, var(--outline)); }
  .toast.error > :global(svg) { color: var(--danger); }
</style>
