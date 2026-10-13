// Splits a rendered chapter into sentences and wraps each one in <span class="s" data-i="n">,
// so read-aloud can highlight the sentence being spoken and jump to a clicked one. Returns the sentences
// and `breaks`, the indices of sentences that end a block (paragraph, heading, list item…).
// With `quotes` on, a quotation of up to `maxQuoted` sentences stays one piece, so the voice reads
// '"Bekle! Hemen geliyorum!" diye seslendi.' in one go.

const BLOCK = 'p,h1,h2,h3,h4,h5,h6,li,td,th,figcaption,dt,dd,blockquote,pre,div,aside,section,article,header,footer';
const ABBREVIATIONS = new Set([
  'dr', 'prof', 'doç', 'yrd', 'av', 'op', 'sn', 'st', 'mr', 'mrs', 'ms', 'jr', 'sr', 'vb', 'vs', 'örn', 'bkz', 'no',
  'nr', 'alb', 'bnb', 'yzb', 'tğm', 'org', 'gen', 'mah', 'cad', 'sok', 'apt', 'ltd', 'şti', 'inc', 'vd', 'yy', 'bl',
  'mrs', 'dept', 'etc', 'e.g', 'i.e', 'vol', 'ch', 'fig', 'a.ş', 's',
]);
const END = /[.!?…]+["'”’»)\]]*(?=\s|$)/g;
const LETTER = /\p{L}/u;
const OPENS_AFTER = /[\s([{—–-]/;
export const QUOTE_DEFAULTS = { quotes: true, maxQuoted: 4 };

// Double-quoted spans of one block as [open, close] indices (close = text.length when never closed).
// A straight " opens after a space or the block start and closes otherwise; single quotes are never
// used, Turkish writes them as apostrophes (Frodo'nun).
function quoteSpans(text) {
  const spans = [];
  let open = -1;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    const opening = c === '“' || c === '«' || (c === '"' && (i === 0 || OPENS_AFTER.test(text[i - 1])));
    const closing = c === '”' || c === '»' || (c === '"' && !opening);
    if (opening && open < 0) open = i;
    else if (closing && open >= 0) { spans.push([open, i]); open = -1; }
  }
  if (open >= 0) spans.push([open, text.length]);
  return spans;
}

// Spans short enough to keep together: at most `max` sentence ends inside, counting the closing one.
function keptQuotes(text, max) {
  const ends = [...text.matchAll(END)].map((m) => m.index);
  return quoteSpans(text).filter(([open, close]) => ends.filter((i) => i > open && i <= close).length <= max);
}

function sentenceRanges(text, options) {
  const ranges = [];
  const kept = options.quotes ? keptQuotes(text, options.maxQuoted) : [];
  let start = 0;
  for (const match of text.matchAll(END)) {
    const end = match.index + match[0].length;
    // Inside a kept quotation: the cut waits for the closing quote ('!"' ends the match past it).
    if (kept.some(([open, close]) => match.index > open && end <= close)) continue;
    const next = text.slice(end).trimStart()[0] ?? '';
    // A lower-case continuation does not end a sentence: '"Gelir misin?" diye sordu.' is one.
    if (LETTER.test(next) && next !== next.toLocaleUpperCase('tr')) continue;
    if (match[0][0] === '.') {
      const word = text.slice(start, match.index).split(/\s+/).pop().replace(/^[^\p{L}]+/u, '').toLocaleLowerCase('tr');
      // Abbreviations and initials ("J.") do not end a sentence either.
      if (ABBREVIATIONS.has(word) || (word.length === 1 && LETTER.test(word))) continue;
    }
    ranges.push([start, end]);
    start = end;
  }
  if (start < text.length) ranges.push([start, text.length]);
  return ranges
    .map(([a, b]) => {
      while (a < b && /\s/.test(text[a])) a++;
      while (b > a && /\s/.test(text[b - 1])) b--;
      return [a, b];
    })
    .filter(([a, b]) => b > a && LETTER.test(text.slice(a, b)));
}

export function segment(root, options = QUOTE_DEFAULTS) {
  const groups = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (node.parentElement?.closest('.no-tts, button') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  let current = null;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const block = node.parentElement.closest(BLOCK) ?? root;
    if (!current || current.block !== block) groups.push((current = { block, nodes: [] }));
    current.nodes.push(node);
  }

  const sentences = [];
  const breaks = new Set();
  const pieces = [];   // [node, start, end, index], wrapped afterwards
  for (const group of groups) {
    let offset = 0;
    const spans = group.nodes.map((node) => {
      const span = [offset, offset + node.data.length, node];
      offset += node.data.length;
      return span;
    });
    const text = group.nodes.map((node) => node.data).join('');
    for (const [a, b] of sentenceRanges(text, { ...QUOTE_DEFAULTS, ...options })) {
      const index = sentences.length;
      sentences.push(text.slice(a, b).replace(/\s+/g, ' '));
      for (const [start, end, node] of spans) {
        const from = Math.max(a, start), to = Math.min(b, end);
        if (to > from) pieces.push([node, from - start, to - start, index]);
      }
    }
    if (sentences.length) breaks.add(sentences.length - 1);
  }
  // Wrap from the end so earlier offsets in the same text node stay valid.
  for (let i = pieces.length - 1; i >= 0; i--) {
    const [node, start, end, index] = pieces[i];
    const range = document.createRange();
    range.setStart(node, start);
    range.setEnd(node, end);
    const span = document.createElement('span');
    span.className = 's';
    span.dataset.i = index;
    range.surroundContents(span);
  }
  return { sentences, breaks };
}
