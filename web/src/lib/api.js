// Bridge to the Python backend (bridge.py) through pywebview.
// In a plain browser (npm run dev without pywebview) a mock backend is used so the
// interface can be developed and reviewed on its own.

const listeners = new Map();

window.__cevirgecEmit = (event, data) => {
  for (const fn of listeners.get(event) ?? []) fn(data);
};

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}

let backend;

function ready() {
  if (backend) return backend;
  backend = new Promise((resolve) => {
    if (window.pywebview?.api) return resolve(window.pywebview.api);
    let settled = false;
    window.addEventListener('pywebviewready', () => {
      settled = true;
      resolve(window.pywebview.api);
    });
    // Outside pywebview: fall back to the development mock.
    setTimeout(async () => {
      if (settled || window.pywebview) return;
      const { createMock } = await import('./mock.js');
      resolve(createMock(window.__cevirgecEmit));
    }, 600);
  });
  return backend;
}

export async function call(method, ...args) {
  const api = await ready();
  return api[method](...args);
}
