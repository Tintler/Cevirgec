<script>
  import Icon from '../lib/Icon.svelte';
</script>

<section class="screen">
  <div class="top"><h1>Okuma</h1></div>
  <div class="card soon">
    <div class="logo"><Icon name="headphones" size={34} /></div>
    <h2>Kitap okuma ve sesli dinleme yakında</h2>
    <p>Bu sekmede kendi açtığınız EPUB kitapları okuyabilecek ve EMA Lightning ile Türkçe sesli dinleyebileceksiniz.</p>
  </div>
</section>

<style>
  .screen { display: flex; flex-direction: column; gap: 16px; padding: 18px 28px 28px; height: 100%; }
  .top h1 { font-size: 28px; font-weight: 700; letter-spacing: -.02em; }
  .soon { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; text-align: center; padding: 40px; }
  .logo { width: 72px; height: 72px; border-radius: 20px; display: grid; place-items: center; background: var(--surface); color: var(--accent); }
  h2 { font-size: 22px; }
  p { color: var(--secondary); max-width: 460px; }
</style>
