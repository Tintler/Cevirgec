<script>
  import Library from './Library.svelte';
  import Reader from './Reader.svelte';
  import { app, act } from '../lib/state.svelte.js';

  async function open(entry) {
    const data = await act('reader_open', entry.id);
    if (!data) return;
    app.reading = { book: data, section: data.position.section, offset: data.position.offset, percent: data.percent };
  }

  async function close() {
    app.reading = { book: null, section: 0, offset: 0, percent: 0 };
    const books = await act('library_list');
    if (books) app.library = books;
  }
</script>

{#if app.reading.book}
  <Reader onclose={close} />
{:else}
  <Library onopen={open} />
{/if}
