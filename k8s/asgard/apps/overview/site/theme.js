// Applies a saved light/dark choice before the first paint, so there is no flash of the other
// theme. Loaded as a plain blocking script in <head>; it is a few hundred bytes. With nothing saved
// the page follows the operating system (style.css, light-dark()). The switch itself is in app.js.
(() => {
  let saved = null;
  try { saved = localStorage.getItem('overview.theme'); } catch { /* private mode: follow the OS */ }
  if (saved !== 'light' && saved !== 'dark') return;
  document.documentElement.dataset.theme = saved;
  // The browser's own chrome (mobile address bar) takes the page colour, too.
  const chrome = saved === 'light' ? '#e1e2e7' : '#16161e';
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) meta.content = chrome;
})();
