<script module>
  // Covers are fetched lazily, once per book, and kept for the session.
  const cache = new Map();
  const palette = [['#7c3aed', '#1e1b4b'], ['#0ea5e9', '#0c4a6e'], ['#22c55e', '#14532d'], ['#f97316', '#7c2d12'],
    ['#e11d48', '#4c0519'], ['#a855f7', '#312e81'], ['#14b8a6', '#134e4a'], ['#eab308', '#713f12']];
</script>

<script>
  import { call } from '../lib/api.js';

  let { book, small = false } = $props();
  let src = $state(null);

  const colors = $derived(palette[[...book.id].reduce((sum, c) => sum + c.charCodeAt(0), 0) % palette.length]);

  $effect(() => {
    const id = book.id;
    if (cache.has(id)) { src = cache.get(id); return; }
    call('library_cover', id).then((value) => {
      const cover = typeof value === 'string' ? value : null;
      cache.set(id, cover);
      if (book.id === id) src = cover;
    });
  });
</script>

<div class="cover" class:small style:background="linear-gradient(160deg, {colors[0]}, {colors[1]})">
  {#if src}<img {src} alt="" />{:else}<span class="t">{book.title}</span><span class="a">{book.author}</span>{/if}
</div>

<style>
  .cover { aspect-ratio: 2 / 3; border-radius: 6px; overflow: hidden; display: flex; flex-direction: column; justify-content: space-between;
    padding: 12px; color: #fff; box-shadow: 0 10px 24px rgba(0, 0, 0, .35); position: relative; }
  .cover img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
  .t { font-weight: 700; font-size: 14px; line-height: 1.2; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 5; -webkit-box-orient: vertical; }
  .a { font-size: 11px; opacity: .8; }
  .small { padding: 6px; box-shadow: 0 4px 10px rgba(0, 0, 0, .3); }
  .small .t { font-size: 8px; -webkit-line-clamp: 4; }
  .small .a { display: none; }
</style>
