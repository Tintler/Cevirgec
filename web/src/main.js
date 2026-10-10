import { mount } from 'svelte';
import '@fontsource-variable/inter';
import '@fontsource/literata/400.css';
import '@fontsource/literata/600.css';
import '@fontsource/literata/400-italic.css';
import './app.css';
import App from './App.svelte';

// Disable the browser's own context menu outside text fields (the app has its own menus).
window.addEventListener('contextmenu', (event) => {
  if (!event.target.closest('input, textarea, .selectable')) event.preventDefault();
});

export default mount(App, { target: document.getElementById('app') });
