<script>
  import Icon from '../lib/Icon.svelte';
  import Toggle from '../components/Toggle.svelte';
  import { app, act, setTheme, toast, saveTtsPrefs } from '../lib/state.svelte.js';
  import { call } from '../lib/api.js';

  let cfg = $state(null);
  let defaults = {};
  let locked = $state([]);
  let saving = $state(false);
  let saved = $state('');
  const dirty = $derived(cfg !== null && JSON.stringify(cfg) !== saved);

  const numbers = [
    ['timeout_seconds', 'Zaman aşımı (sn)', 'Tek bir LM Studio isteği için beklenecek en uzun süre.', 1, 86400],
    ['chunk_chars', 'Parça boyutu (karakter)', 'Bölümler bu uzunlukta parçalara bölünerek çevrilir.', 1, 1000000],
    ['max_tokens', 'Çıktı token payı', 'Bir çeviri yanıtı için ayrılan en fazla token.', 1, 262144],
    ['notes_max_tokens', 'Not çıktı tokenı', 'Bölüm notları yanıtı için en fazla token.', 1, 32768],
    ['fallback_context_length', 'Yedek context', 'LM Studio context bilgisini veremezse kullanılır.', 1, 1048576],
    ['context_safety_tokens', 'Güvenlik payı', 'Context hesabında boş bırakılan token.', 1, 65536],
  ];

  async function load() {
    const r = await call('get_settings');
    cfg = r.config; defaults = r.defaults; locked = r.locked; saved = JSON.stringify(cfg);
  }
  load();

  const isLocked = (key) => locked.includes(key);
  const ttsBadge = $derived({
    missing: ['Kurulu değil', ''], idle: ['Yüklenmedi', ''], loading: ['Yükleniyor…', 'active'],
    ready: [`Hazır · ${app.tts.device?.startsWith('cuda') ? 'GPU' : 'CPU'}`, 'done'], error: ['Hata', 'partial'],
  }[app.tts.state] ?? [app.tts.state, '']);

  function setTtsDevice(value) {
    app.ttsPrefs.device = value;
    saveTtsPrefs();
    if (app.tts.state === 'ready' || app.tts.state === 'error') call('tts_load', value);
  }
  const models = $derived([...new Set([...(app.models.loaded ?? []).map((m) => m.name), cfg?.model].filter(Boolean))]);

  async function save() {
    saving = true;
    const r = await act('save_settings', $state.snapshot(cfg));
    saving = false;
    if (r) { cfg = r.config; saved = JSON.stringify(cfg); app.models.model = r.config.model; toast('Ayarlar kaydedildi.', 'ok'); }
  }

  function reset() {
    for (const key of Object.keys(defaults)) if (!isLocked(key)) cfg[key] = defaults[key];
  }
</script>

<section class="screen">
  <div class="top">
    <h1>Ayarlar</h1>
    {#if dirty}<span class="unsaved">Kaydedilmemiş değişiklik var</span>{/if}
    <button class="btn" onclick={reset} disabled={!cfg}><Icon name="rotate" />Varsayılanlar</button>
    <button class="btn primary" onclick={save} disabled={!cfg || saving || !dirty}><Icon name="check" />Kaydet</button>
  </div>

  {#if cfg}
    <div class="scroll">
      <div class="card">
        <h3>Görünüm</h3>
        <div class="fr">
          <div class="l">Tema</div>
          <div class="seg">
            <button class:on={app.theme === 'dark'} onclick={() => setTheme('dark')}><Icon name="moon" size={14} />Koyu</button>
            <button class:on={app.theme === 'light'} onclick={() => setTheme('light')}><Icon name="sun" size={14} />Açık</button>
          </div>
        </div>
      </div>

      <div class="card">
        <h3>Sesli okuma</h3>
        <div class="fr">
          <div class="l">Ses modeli<small>EMA Lightning · Türkçe · çevrimdışı çalışır · Apache 2.0</small></div>
          <span class="badge {ttsBadge[1]}">{ttsBadge[0]}</span>
          {#if app.tts.state === 'idle' || app.tts.state === 'error'}
            <button class="btn sm" onclick={() => call('tts_load', app.ttsPrefs.device)}>Modeli yükle</button>
          {/if}
        </div>
        {#if app.tts.state === 'missing'}
          <div class="warn"><Icon name="alert" size={16} /><span>Kurulu değil. Komut isteminde <code>py -3 -m pip install ema-lightning</code>
            çalıştırıp uygulamayı yeniden başlatın. NVIDIA ekran kartı için önce PyTorch'un CUDA sürümünü kurun.</span></div>
        {:else if app.tts.state === 'loading'}
          <div class="info"><span>{app.tts.message}</span><small>İlk açılışta model indirilir; ilerleme komut penceresinde de görünür.</small></div>
        {:else if app.tts.state === 'error'}
          <div class="warn"><Icon name="alert" size={16} /><span class="selectable">{app.tts.message}</span></div>
        {/if}
        <div class="fr">
          <div class="l">İşlemci<small>Otomatik: NVIDIA ekran kartı varsa onu, yoksa CPU'yu kullanır.</small></div>
          <div class="seg">
            {#each [['auto', 'Otomatik'], ['cuda', 'GPU'], ['cpu', 'CPU']] as [value, label]}
              <button class:on={app.ttsPrefs.device === value} onclick={() => setTtsDevice(value)}>{label}</button>
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

      <div class="card">
        <h3>LM Studio ve model</h3>
        {#if locked.length}<div class="warn"><Icon name="alert" size={16} />Çeviri sürerken LM Studio ve model ayarları değiştirilemez.</div>{/if}
        <div class="fr">
          <div class="l">Sunucu adresi<small>Yalnızca yerel HTTP adresi (localhost / 127.0.0.1)</small></div>
          <input class="field w" bind:value={cfg.base_url} disabled={isLocked('base_url')} />
        </div>
        <div class="fr">
          <div class="l">Model<small>LM Studio'nun yüklü bildirdiği modeller</small></div>
          <select class="field w" bind:value={cfg.model} disabled={isLocked('model')}>
            {#each models as m}<option value={m}>{m}</option>{/each}
          </select>
        </div>
      </div>

      <div class="card">
        <h3>Kitap ve EPUB</h3>
        <div class="fr">
          <div class="l">EPUBCheck ile doğrula<small>EPUB oluşturulduktan sonra harici EPUBCheck çalıştırılır. JAR için Java gerekir.</small></div>
          <Toggle bind:checked={cfg.epubcheck_enabled} />
        </div>
        <div class="fr">
          <div class="l">EPUBCheck yolu<small>Boşsa PATH içindeki epubcheck kullanılır; .jar veya .exe olabilir.</small></div>
          <input class="field w" bind:value={cfg.epubcheck_path} placeholder="epubcheck" />
        </div>
        <div class="fr">
          <div class="l">Katı sözlük<small>Kapalıyken uygulanamayan terim glossary_fixes.log dosyasına yazılır ve çeviri sürer.</small></div>
          <Toggle bind:checked={cfg.glossary_strict} />
        </div>
        <div class="fr">
          <div class="l">Bitiş işareti denetimi<small>Uzun yanıtlarda modelin bitiş işaretini üretmesi gerekir; eksik sonuçlar kaydedilmez.</small></div>
          <Toggle bind:checked={cfg.completion_marker_enabled} />
        </div>
        <div class="fr">
          <div class="l">İşaretsiz sınır (karakter)<small>Bu uzunluk ve altındaki parçalarda bitiş işareti aranmaz.</small></div>
          <input class="field n" type="number" min="0" max="1000000" bind:value={cfg.completion_marker_exempt_chars} disabled={!cfg.completion_marker_enabled} />
        </div>
        <div class="fr">
          <div class="l">Otomatik tekrar sayısı<small>İlk istek hariç ek deneme sayısı: 2 seçilirse en fazla 3 istek yapılır.</small></div>
          <input class="field n" type="number" min="1" max="100" bind:value={cfg.auto_retry_count} />
        </div>
      </div>

      <div class="card">
        <h3>Gelişmiş</h3>
        <div class="warn"><Icon name="alert" size={16} />Bu ayarlar deneyseldir ve çeviri kararlılığını etkiler. Ne yaptığınızdan emin değilseniz değiştirmeyin.</div>
        <div class="fr">
          <div class="l">Temperature</div>
          <input class="field n" type="number" min="0" max="2" step="0.05" bind:value={cfg.temperature} disabled={isLocked('temperature')} />
        </div>
        {#each numbers as [key, title, hint, min, max]}
          <div class="fr">
            <div class="l">{title}<small>{hint}</small></div>
            <input class="field n" type="number" {min} {max} bind:value={cfg[key]} disabled={isLocked(key)} />
          </div>
        {/each}
        <div class="fr">
          <div class="l">Token tahmini<small>İstek göndermeden önce context sınırı tahmin edilerek denetlenir.</small></div>
          <Toggle bind:checked={cfg.token_estimation_enabled} disabled={isLocked('token_estimation_enabled')} />
        </div>
      </div>
    </div>
  {/if}
</section>

<style>
  .screen { display: flex; flex-direction: column; gap: 16px; padding: 18px 0 0 28px; height: 100%; min-height: 0; }
  .top { display: flex; align-items: center; gap: 10px; padding-right: 28px; max-width: 868px; }
  .top h1 { font-size: 28px; font-weight: 700; letter-spacing: -.02em; flex: 1; }
  .unsaved { color: var(--accent); font-size: 12px; font-weight: 600; }
  .scroll { overflow: auto; display: flex; flex-direction: column; gap: 16px; padding: 0 28px 28px 0; }
  .card { padding: 6px 18px; max-width: 840px; }
  h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--dim); padding: 12px 0 4px; }
  .fr { display: flex; align-items: center; gap: 16px; padding: 12px 0; border-top: 1px solid var(--outline); }
  h3 + .fr, .warn + .fr { border-top: 0; }
  .l { flex: 1; min-width: 0; }
  .l small { display: block; color: var(--dim); font-size: 12px; }
  .field.w { width: 320px; }
  .field.n { width: 140px; }
  .info { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, transparent); padding: 8px 12px; border-radius: 8px; margin: 4px 0 8px; }
  .info small { color: var(--dim); }
  .warn code { font-family: var(--mono); background: var(--surface); padding: 1px 5px; border-radius: 4px; user-select: text; }
  .warn { display: flex; gap: 8px; align-items: center; color: var(--warn); font-size: 12px; background: color-mix(in srgb, var(--warn) 10%, transparent); padding: 8px 12px; border-radius: 8px; margin: 4px 0 8px; }
  .seg { display: flex; background: var(--surface); border-radius: 999px; padding: 3px; }
  .seg button { display: inline-flex; gap: 6px; align-items: center; padding: 5px 12px; border-radius: 999px; font-size: 12px; font-weight: 600; color: var(--secondary); }
  .seg button.on { background: var(--surface-active); color: var(--text); }
</style>
