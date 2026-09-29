// The host owns the dialog; article scripts remain disabled in the iframe.
export function createReadingImageViewer(host = document) {
  const dialog = host.createElement('dialog');
  dialog.className = 'reading-image-viewer';
  dialog.setAttribute('aria-label', '查看论文图片');
  dialog.innerHTML = `<div class="image-toolbar"><strong>图片</strong><button data-action="out" aria-label="缩小图片">−</button><output aria-live="polite"></output><button data-action="in" aria-label="放大图片">＋</button><button data-action="actual">原始尺寸</button><button data-action="fit">适应窗口</button><a target="_blank" rel="noopener">查看原文 ↗</a><button data-action="close" aria-label="关闭图片">关闭 ×</button></div><div class="image-viewport" tabindex="0" aria-label="图片区域，可拖动或使用方向键查看"><div class="image-stage"><img draggable="false" alt=""></div></div><p class="image-caption"></p><p class="image-status" role="status"></p>`;
  host.body.append(dialog);
  const viewport = dialog.querySelector('.image-viewport');
  const stage = dialog.querySelector('.image-stage');
  const img = dialog.querySelector('img');
  const output = dialog.querySelector('output');
  const status = dialog.querySelector('.image-status');
  let scale = 1, fitted = true, source = null, drag = null, generation = 0;
  const ready = () => img.naturalWidth > 0 && img.naturalHeight > 0;
  const fitScale = () => Math.min(1, (viewport.clientWidth - 32) / img.naturalWidth, (viewport.clientHeight - 32) / img.naturalHeight);
  function resize(next, anchor) {
    if (!ready()) return;
    const before = img.getBoundingClientRect(), box = viewport.getBoundingClientRect();
    const point = anchor || {x:box.left + viewport.clientWidth / 2, y:box.top + viewport.clientHeight / 2};
    const x = (point.x - before.left) / scale, y = (point.y - before.top) / scale;
    scale = Math.max(Math.min(.05, fitScale()), Math.min(8, next));
    img.style.width = `${img.naturalWidth * scale}px`;
    img.style.height = `${img.naturalHeight * scale}px`;
    stage.style.width = `${Math.max(viewport.clientWidth, img.naturalWidth * scale + 32)}px`;
    stage.style.height = `${Math.max(viewport.clientHeight, img.naturalHeight * scale + 32)}px`;
    const after = img.getBoundingClientRect();
    viewport.scrollLeft += after.left + x * scale - point.x;
    viewport.scrollTop += after.top + y * scale - point.y;
    output.value = `${Math.round(scale * 100)}%`;
    viewport.classList.toggle('is-zoomed', stage.offsetWidth > viewport.clientWidth || stage.offsetHeight > viewport.clientHeight);
  }
  function fit() { fitted = true; resize(fitScale()); viewport.scrollTo(0, 0); }
  function zoom(factor, anchor) { fitted = false; resize(scale * factor, anchor); }
  function close() { if (dialog.open) dialog.close(); }
  dialog.addEventListener('close', () => {
    generation++; drag = null; viewport.classList.remove('is-dragging');
    img.onload = img.onerror = null; img.removeAttribute('src');
    if (source?.isConnected) source.focus({preventScroll:true});
    source = null;
  });
  dialog.addEventListener('click', event => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (action === 'close') close();
    if (action === 'in') zoom(1.25);
    if (action === 'out') zoom(1 / 1.25);
    if (action === 'actual') { fitted = false; resize(1); }
    if (action === 'fit') fit();
    if (event.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) close();
    }
  });
  viewport.addEventListener('wheel', event => {
    event.preventDefault(); zoom(event.deltaY < 0 ? 1.15 : 1 / 1.15, {x:event.clientX,y:event.clientY});
  }, {passive:false});
  viewport.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !ready()) return;
    drag = {id:event.pointerId,x:event.clientX,y:event.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};
    viewport.setPointerCapture(event.pointerId); viewport.classList.add('is-dragging');
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    viewport.scrollLeft = drag.left - (event.clientX - drag.x);
    viewport.scrollTop = drag.top - (event.clientY - drag.y);
  });
  const endDrag = () => { drag = null; viewport.classList.remove('is-dragging'); };
  viewport.addEventListener('pointerup', endDrag);
  viewport.addEventListener('pointercancel', endDrag);
  viewport.addEventListener('lostpointercapture', endDrag);
  const observer = new ResizeObserver(() => { if (dialog.open && ready()) { if (fitted) fit(); else resize(scale); } });
  observer.observe(viewport);
  function open(target, originalUrl) {
    const url = target.currentSrc || target.src;
    if (!url) return;
    const resolved = new URL(url, target.ownerDocument.baseURI);
    if (!['data:', 'blob:'].includes(resolved.protocol) && !(resolved.origin === host.location.origin && ['http:', 'https:'].includes(resolved.protocol))) return;
    source = target; const serial = ++generation;
    img.style.visibility = 'hidden'; output.value = '—'; status.textContent = '正在加载图片…';
    dialog.querySelector('.image-caption').textContent = target.closest('figure')?.querySelector('figcaption')?.textContent?.trim() || target.alt || '论文图片';
    const link = dialog.querySelector('a'); link.hidden = !originalUrl;
    if (originalUrl) link.href = originalUrl;
    img.alt = target.alt || '论文图片';
    img.onload = () => {
      if (serial !== generation || !dialog.open) return;
      img.style.visibility = ''; fit(); status.textContent = `${img.naturalWidth} × ${img.naturalHeight} · 滚轮缩放，放大后拖动查看；清晰度取决于原图。`;
    };
    img.onerror = () => { if (serial === generation) status.textContent = '图片加载失败，请关闭后重试，或查看原文。'; };
    if (!dialog.open) dialog.showModal();
    dialog.querySelector('[data-action="close"]').focus({preventScroll:true});
    img.src = resolved.href;
  }
  function bind(content, originalUrl) {
    for (const target of content.querySelectorAll('img')) {
      target.tabIndex = 0; target.setAttribute('role', 'button');
      target.setAttribute('aria-label', `${target.alt || '论文图片'}，点击查看大图`);
      target.title = '点击查看大图'; target.style.cursor = 'zoom-in';
    }
    const activate = event => {
      const target = event.target.closest?.('img');
      if (!target) return;
      if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
      if (event.type === 'click' && event.button !== 0) return;
      event.preventDefault(); event.stopImmediatePropagation(); open(target, originalUrl);
    };
    content.addEventListener('click', activate, true);
    content.addEventListener('keydown', activate, true);
  }
  return {bind, close};
}
