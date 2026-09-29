/* Shared public appearance client; no business data leaves the current origin. */
(() => {
  'use strict';
  const root = document.documentElement;
  const config = document.querySelector('meta[name="aos-appearance"]');
  const host = config?.dataset.host === 'true';
  const valid = mode => ['light', 'dark', 'system'].includes(mode);
  const key = 'agentos-k.appearance.v2';
  const system = matchMedia('(prefers-color-scheme: dark)');
  let mode = 'system', busy = false, generation = 0, pending = false;
  try { const saved = localStorage.getItem(key); if (valid(saved)) mode = saved; } catch (_) {}
  const incoming = new URL(location.href).searchParams.get('aos_theme');
  if (valid(incoming)) mode = incoming;
  if (host && valid(config.dataset.mode)) mode = config.dataset.mode;
  function apply() {
    root.dataset.theme = mode === 'system' ? (system.matches ? 'dark' : 'light') : mode;
    root.dataset.appearance = mode;
    try { localStorage.setItem(key, mode); } catch (_) {}
    document.querySelectorAll('[data-appearance-select]').forEach(el => { el.value = mode; });
    // Carry appearance across separate WebView/browser profiles on the first navigation.
    if (host) document.querySelectorAll('a[data-product-link]').forEach(link => {
      const url = new URL(link.getAttribute('href'), location.href);
      url.searchParams.set('aos_theme', mode);
      link.href = url.href;
    });
    window.dispatchEvent(new CustomEvent('aos-theme-change', {detail:{mode,theme:root.dataset.theme}}));
  }
  apply();
  if (valid(incoming)) {
    const clean = new URL(location.href); clean.searchParams.delete('aos_theme');
    history.replaceState(history.state, '', clean);
  }
  async function sync() {
    if (busy || pending || document.hidden) return;
    const version = generation;
    pending = true;
    try {
      const response = await fetch('/api/v1/appearance', {cache:'no-store',signal:AbortSignal.timeout(2500)});
      if (!response.ok) return;
      const value = await response.json();
      if (version === generation && !busy && valid(value.mode) && value.mode !== mode) {
        mode = value.mode; apply();
      }
    } catch (_) { /* Reading remains available while AgentOS is offline. */ }
    finally { pending = false; }
  }
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-appearance-select]').forEach(select => {
      select.addEventListener('change', async () => {
        if (!host || !valid(select.value)) return;
        const previous = mode;
        mode = select.value; busy = true; generation++; select.disabled = true; apply();
        const status = document.querySelector('[data-theme-status]');
        if (status) status.textContent = '';
        try {
          const response = await fetch('/api/v1/appearance', {
            method:'POST',headers:{'Content-Type':'application/json','X-AOS-Token':config.dataset.token},
            body:JSON.stringify({mode}),signal:AbortSignal.timeout(3500)
          });
          if (!response.ok) throw new Error('save failed');
          if (status) status.textContent = '已同步到 Wiki 和 Reader';
        } catch (_) {
          mode = previous; apply();
          if (status) status.textContent = '未能保存外观，请重试';
        } finally { busy = false; select.disabled = false; }
      });
    });
    sync();
    setInterval(sync, 3000);
  });
  system.addEventListener('change', () => { if (mode === 'system') apply(); });
  document.addEventListener('visibilitychange', sync);
  window.addEventListener('focus', sync);
})();
