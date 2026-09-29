// Standalone interaction primitives. No product data or network access.
export function clampWidth(value, available, min = 320, reserve = 360) {
  const max = Math.max(min, available - reserve);
  return Math.round(Math.min(max, Math.max(min, Number(value) || 420)));
}
export function readLocal(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
export function writeLocal(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
}
export function splitPane({handle, panel, key, available, apply}) {
  let preferred = readLocal(key, 420);
  function update(value = preferred, save = false) {
    const width = clampWidth(value, available());
    if (save) { preferred = width; writeLocal(key, width); }
    apply(width);
    handle.setAttribute('aria-valuemin', '320');
    handle.setAttribute('aria-valuemax', String(Math.max(320, available() - 360)));
    handle.setAttribute('aria-valuenow', String(width));
    handle.setAttribute('aria-valuetext', `助手宽度 ${width} 像素`);
  }
  handle.setAttribute('role', 'separator');
  handle.setAttribute('aria-orientation', 'vertical');
  handle.setAttribute('aria-controls', panel.id);
  handle.tabIndex = 0;
  handle.addEventListener('keydown', e => {
    const width = Number(handle.getAttribute('aria-valuenow'));
    const step = e.shiftKey ? 64 : 16;
    const value = {ArrowLeft: width + step, ArrowRight: width - step, Home: 320, End: available() - 360}[e.key];
    if (value !== undefined) { e.preventDefault(); update(value, true); }
    if (e.key === 'Enter') update(420, true);
  });
  let dragging = false;
  handle.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    dragging = true; handle.setPointerCapture(e.pointerId); handle.focus(); e.preventDefault();
  });
  handle.addEventListener('pointermove', e => {
    if (dragging) update(panel.getBoundingClientRect().right - e.clientX, true);
  });
  const stop = () => { dragging = false; };
  handle.addEventListener('pointerup', stop); handle.addEventListener('lostpointercapture', stop);
  handle.addEventListener('dblclick', () => update(420, true));
  window.addEventListener('resize', () => update()); update();
  return {reset: () => update(420, true), update: () => update()};
}
export function scrollSnapshot(box) {
  return {top: box.scrollTop, following: box.scrollHeight - box.clientHeight - box.scrollTop < 64};
}
export function restoreScroll(box, snapshot, fresh, button) {
  box.scrollTop = snapshot.following ? box.scrollHeight : snapshot.top;
  button.hidden = snapshot.following || !fresh;
}
export function composer(textarea, form, expand) {
  const resize = () => {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, Math.max(70, innerHeight * (form.classList.contains('expanded') ? .5 : .22))) + 'px';
  };
  textarea.addEventListener('input', resize);
  textarea.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault(); form.requestSubmit();
    }
  });
  expand.addEventListener('click', () => {
    const open = form.classList.toggle('expanded'); expand.textContent = open ? '收起编辑' : '展开编辑';
    expand.setAttribute('aria-expanded', String(open)); resize(); textarea.focus();
  });
  window.addEventListener('resize', resize); resize(); return resize;
}
export function readability(container, key) {
  const settings = readLocal(key, {size: 14, line: 1.85});
  const size = container.querySelector('[data-font-size]'), line = container.querySelector('[data-line-height]');
  size.value = String(settings.size); line.value = String(settings.line);
  const apply = () => {
    const value = {size: Number(size.value) || 14, line: Number(line.value) || 1.85};
    document.documentElement.style.setProperty('--reading-size', value.size + 'px');
    document.documentElement.style.setProperty('--reading-line', String(value.line)); writeLocal(key, value);
  };
  size.onchange = line.onchange = apply; apply();
}
