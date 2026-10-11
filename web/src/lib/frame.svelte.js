// The frameless window on Windows (window_frame.py): title bar buttons, moving and resizing.
import { call, on } from './api.js';

export const frame = $state({ custom: false, maximized: false });

export function initFrame() {
  on('window', (state) => Object.assign(frame, state));
  call('window_state').then((state) => state && Object.assign(frame, state)).catch(() => {});
}

export const windowCommand = (name, edge) => call('window_command', name, edge);

const interactive = 'button, a, input, select, textarea, [role="button"]';

// Svelte action for empty strips that move the window. The move starts once the pointer has
// travelled a few pixels, so a double-click still reaches the page and maximises or restores.
export function dragArea(node) {
  let start = null;
  const down = (event) => {
    if (!frame.custom || event.button !== 0 || event.target.closest(interactive)) return;
    start = [event.screenX, event.screenY];
  };
  const move = (event) => {
    if (!start) return;
    if (!(event.buttons & 1)) { start = null; return; }
    if (Math.abs(event.screenX - start[0]) + Math.abs(event.screenY - start[1]) > 3) {
      start = null;
      windowCommand('drag');
    }
  };
  const up = () => (start = null);
  const dblclick = (event) => {
    if (frame.custom && !event.target.closest(interactive)) windowCommand('toggle_maximize');
  };
  node.addEventListener('mousedown', down);
  node.addEventListener('dblclick', dblclick);
  window.addEventListener('mousemove', move);
  window.addEventListener('mouseup', up);
  return {
    destroy() {
      node.removeEventListener('mousedown', down);
      node.removeEventListener('dblclick', dblclick);
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    },
  };
}
