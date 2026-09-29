document.querySelectorAll('time[datetime]').forEach(element => {
  const value = new Date(element.dateTime);
  if (!Number.isNaN(value.getTime())) {
    element.textContent = value.toLocaleString();
    element.title = element.dateTime;
  }
});
// Preserve the actual filtered list on return, without trusting external URLs.
try {
  if (location.pathname === '/data') sessionStorage.setItem('aos-data-list', location.href);
  const back = document.querySelector('[data-center-back]');
  const saved = sessionStorage.getItem('aos-data-list');
  if (back && saved) {
    const url = new URL(saved);
    if (url.origin === location.origin && url.pathname === '/data') back.href = url.href;
  }
} catch { /* Navigation remains usable when browser storage is unavailable. */ }
