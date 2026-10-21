// Applies a saved light/dark choice before the first paint (no flash of the other theme).
// With nothing saved the page follows the operating system (style.css, light-dark()).
(() => {
  let saved = null;
  try { saved = localStorage.getItem('phpwatch.theme'); } catch { /* private mode: follow the OS */ }
  if (saved !== 'light' && saved !== 'dark') return;
  document.documentElement.dataset.theme = saved;
  const chrome = saved === 'light' ? '#e1e2e7' : '#16161e';
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) meta.content = chrome;
})();
