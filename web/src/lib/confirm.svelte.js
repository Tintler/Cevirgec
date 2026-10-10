// Promise-based confirmation / notice dialogs rendered by components/ConfirmHost.svelte.
export const pending = $state({ current: null });

export function confirm({ title, message, ok = 'Evet', cancel = 'Hayır', danger = false, list = null }) {
  return new Promise((resolve) => {
    pending.current = { title, message, ok, cancel, danger, list, resolve };
  });
}

export function notice(title, message, list = null) {
  return confirm({ title, message, ok: 'Tamam', cancel: null, list });
}
