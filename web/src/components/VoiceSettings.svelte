<script>
  import Icon from '../lib/Icon.svelte';
  import { app, act, toast, saveTtsPrefs } from '../lib/state.svelte.js';
  import { call } from '../lib/api.js';
  import { confirm } from '../lib/confirm.svelte.js';

  const LANGUAGES = { tr: 'Ses' };
  const DEFAULT_ENGINE = { tr: 'ema-lightning' };

  const usable = $derived(app.engines.filter((e) => e.mode));
  const languages = ['tr'];

  function options(language) {
    return usable.flatMap((engine) => engine.voices.filter((v) => v.language === language).map((voice) => ({
      value: `${engine.id}/${voice.id}`,
      label: engine.voices.length > 1 ? `${engine.name} · ${voice.name}` : engine.name,
      note: voice.downloaded || !voice.files?.length ? '' : ` — ilk kullanımda indirilir (${voice.size_mb} MB)`,
      engine: engine.id,
    })));
  }

  // Mirrors Engines.choice(): the saved choice when still usable, otherwise the default.
  function selected(language) {
    const list = options(language);
    const saved = app.ttsPrefs.voices?.[language];
    if (list.some((o) => o.value === saved)) return saved;
    return (list.find((o) => o.engine === DEFAULT_ENGINE[language]) ?? list[0])?.value ?? '';
  }

  function choose(language, value) {
    app.ttsPrefs.voices = { ...(app.ttsPrefs.voices ?? {}), [language]: value };
    saveTtsPrefs();
  }

  // Settings of the chosen voice (motor.json "options"): voice description, reference recording…
  function voiceOf(value) {
    const [engineId, voiceId] = value.split('/');
    return usable.find((e) => e.id === engineId)?.voices.find((v) => v.id === voiceId) ?? null;
  }

  function optionValue(value, option) {
    return app.ttsPrefs.options?.[value]?.[option.key] ?? option.default ?? '';
  }

  function setOption(value, key, text) {
    const all = { ...(app.ttsPrefs.options ?? {}) };
    all[value] = { ...(all[value] ?? {}), [key]: text };
    app.ttsPrefs.options = all;
    saveTtsPrefs();
  }

  async function pickReference(value, key) {
    const path = await call('tts_choose_reference');
    if (path) setOption(value, key, path);
  }

  let cache = $state(null);
  const refreshCache = async () => { cache = await call('tts_cache_info'); };
  $effect(() => { app.ttsPrepare; refreshCache(); });

  async function clearCache() {
    const yes = await confirm({
      title: 'Hazırlanan sesleri sil',
      message: 'Önceden üretilip saklanan bütün sesler silinecek; bu kitaplar yeniden okunurken ses yeniden üretilir. Devam edilsin mi?',
      ok: 'Sil', cancel: 'Vazgeç',
    });
    if (!yes) return;
    cache = await act('tts_cache_clear');
    if (cache) toast('Hazırlanan sesler silindi.');
  }

  const megabytes = (bytes) => (bytes / 1e6).toLocaleString('tr-TR', { maximumFractionDigits: bytes > 1e9 ? 0 : 1 });

  function setDevice(value) {
    app.ttsPrefs.device = value;
    saveTtsPrefs();
  }

  async function installFile() {
    await act('tts_install_file');
  }

  async function remove(engine) {
    const yes = await confirm({
      title: 'Ses motorunu kaldır',
      message: `${engine.name} ve indirdiği model/ses dosyaları program klasöründen silinecek. Devam edilsin mi?`,
      ok: 'Kaldır', cancel: 'Vazgeç',
    });
    if (!yes) return;
    const engines = await act('tts_remove', engine.id);
    if (engines) { app.engines = engines; toast(`${engine.name} kaldırıldı.`); }
  }

  const modeBadge = (engine) => engine.busy ? [engine.busy, 'active']
    : engine.mode === 'package' ? [`Kurulu · ${engine.installed_version ?? engine.version}`, 'done']
    : engine.mode === 'source' ? ['Python paketiyle', 'done'] : ['Kurulu değil', ''];

  const stateText = $derived({
    idle: 'Henüz yüklenmedi; okumaya başladığınızda yüklenir.',
    loading: app.tts.message,
    ready: `Hazır · ${app.engines.find((e) => e.id === app.tts.engine)?.name ?? app.tts.engine} · ${String(app.tts.device ?? '').startsWith('cuda') ? 'GPU' : 'CPU'}`,
    error: app.tts.message,
    missing: app.tts.message,
  }[app.tts.state] ?? '');

  $effect(() => { call('tts_engines').then((list) => { if (Array.isArray(list)) app.engines = list; }); });
</script>

<div class="card">
  <div class="head">
    <h3>Sesli okuma</h3>
    <button class="btn sm primary" onclick={installFile} title="BUILD_TTS.cmd ile oluşturduğunuz ses motoru paketini (.zip) kurun"><Icon name="download" size={14} />Dosyadan kur</button>
  </div>
  {#if !app.engines.some((e) => e.mode)}
    <div class="info"><span>Kurulu ses motoru yok. Paketleri proje klasöründe <b>BUILD_TTS.cmd</b> ile oluşturun
      (<code>dist-engines</code> klasörüne yazılır), sonra <b>Dosyadan kur</b> ile seçin.</span></div>
  {/if}

  {#each app.engines as engine (engine.id)}
    {@const badge = modeBadge(engine)}
    <div class="fr engine">
      <div class="l">
        {engine.name} <span class="ver">{engine.version}</span>
        <small>{engine.description}</small>
        <small class="dim">Diller: {engine.languages.map((l) => ({ tr: 'Türkçe', en: 'İngilizce' })[l] ?? l).join(', ')} · {engine.license}</small>
      </div>
      <span class="badge {badge[1]}">{badge[0]}</span>
      {#if engine.removable && !engine.busy}
        <button class="icon-btn" title="Kaldır" onclick={() => remove(engine)}><Icon name="trash" size={16} /></button>
      {/if}
    </div>
  {/each}

  {#if app.tts.state === 'loading' || app.tts.state === 'error' || app.tts.state === 'ready'}
    <div class={app.tts.state === 'error' ? 'warn' : 'info'}>
      {#if app.tts.state === 'error'}<Icon name="alert" size={16} />{/if}
      <span class="selectable">{stateText}</span>
    </div>
  {/if}

  {#each languages as language}
    {@const list = options(language)}
    <div class="fr">
      <div class="l">{LANGUAGES[language] ?? `Dil: ${language}`}<small>{list.length ? 'Bu dildeki kitapları okuyacak ses' : 'Bu dil için kurulu ses motoru yok'}</small></div>
      {#if list.length}
        <select class="field w" value={selected(language)} onchange={(e) => choose(language, e.currentTarget.value)}>
          {#each list as option}<option value={option.value}>{option.label}{option.note}</option>{/each}
        </select>
      {/if}
    </div>
    {@const value = selected(language)}
    {#each voiceOf(value)?.options ?? [] as option (value + option.key)}
      <div class="fr sub">
        <div class="l">{option.label}{#if option.hint}<small>{option.hint}</small>{/if}</div>
        {#if option.type === 'file'}
          <span class="path selectable" title={optionValue(value, option)}>{optionValue(value, option) || 'Seçilmedi'}</span>
          <button class="btn sm" onclick={() => pickReference(value, option.key)}>Seç…</button>
        {:else}
          <input class="field w" value={optionValue(value, option)} placeholder={option.default ?? ''}
            onchange={(e) => setOption(value, option.key, e.currentTarget.value.trim())} />
        {/if}
      </div>
    {/each}
  {/each}

  <div class="fr">
    <div class="l">Hazırlanan sesler<small>Okuyucudaki "Hazırla" ile önceden üretilen sesler; motor yüklenmeden anında okunur.
      Kitap başına program klasöründeki ses-onbellegi altında tutulur.</small></div>
    <span class="dim-text">{cache ? `${cache.files.toLocaleString('tr-TR')} cümle · ${megabytes(cache.bytes)} MB` : '…'}</span>
    <button class="btn sm" disabled={!cache?.files} onclick={clearCache}><Icon name="trash" size={14} />Temizle</button>
  </div>
  <div class="fr">
    <div class="l">İşlemci<small>EMA Lightning için. Otomatik: NVIDIA ekran kartı varsa onu, yoksa CPU'yu kullanır.</small></div>
    <div class="seg">
      {#each [['auto', 'Otomatik'], ['cuda', 'GPU'], ['cpu', 'CPU']] as [value, label]}
        <button class:on={app.ttsPrefs.device === value} onclick={() => setDevice(value)}>{label}</button>
      {/each}
    </div>
  </div>
  <div class="fr">
    <div class="l">Varsayılan hız</div>
    <div class="seg">
      {#each [0.8, 1, 1.25, 1.5, 2] as value}
        <button class:on={app.ttsPrefs.speed === value} onclick={() => { app.ttsPrefs.speed = value; saveTtsPrefs(); }}>{value.toLocaleString('tr-TR')}×</button>
      {/each}
    </div>
  </div>
</div>

<style>
  .card { padding: 6px 18px; max-width: 840px; }
  .head { display: flex; align-items: center; justify-content: space-between; }
  h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--dim); padding: 12px 0 4px; }
  .fr { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-top: 1px solid var(--outline); }
  .head + .fr { border-top: 0; }
  .l { flex: 1; min-width: 0; }
  .l small { display: block; color: var(--secondary); font-size: 12px; }
  .l small.dim { color: var(--dim); }
  .info code { font-family: var(--mono); }
  .dim-text { font-size: 12px; color: var(--secondary); white-space: nowrap; }
  .sub { padding-left: 16px; border-top-style: dashed; }
  .path { max-width: 260px; font-size: 12px; color: var(--secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; }
  .ver { color: var(--dim); font-size: 12px; margin-left: 4px; }
  .field.w { width: 320px; }
  .info, .warn { display: flex; gap: 8px; align-items: center; font-size: 12px; padding: 8px 12px; border-radius: 8px; margin: 4px 0 8px; }
  .info { color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .warn { color: var(--warn); background: color-mix(in srgb, var(--warn) 10%, transparent); }
  .seg { display: flex; background: var(--surface); border-radius: 999px; padding: 3px; }
  .seg button { display: inline-flex; gap: 6px; align-items: center; padding: 5px 12px; border-radius: 999px; font-size: 12px; font-weight: 600; color: var(--secondary); }
  .seg button.on { background: var(--surface-active); color: var(--text); }
</style>
