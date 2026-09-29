// Preserve the selected text before focus moves from the article to the host menu.
export function createSelectionNoteMenu({onNote, notice}, host = document) {
  const menu = host.createElement('div');
  menu.className = 'reading-note-menu'; menu.setAttribute('popover', 'manual');
  menu.setAttribute('role', 'menu'); menu.setAttribute('aria-label', '选段操作');
  const button = host.createElement('button');
  button.type = 'button'; button.setAttribute('role', 'menuitem'); button.textContent = '记笔记';
  menu.append(button); host.body.append(menu);
  let pending = null, returnFocus = null, frameController = null;
  function close(restore = false) {
    if (menu.matches(':popover-open')) menu.hidePopover();
    pending = null;
    if (restore && returnFocus?.isConnected) returnFocus.focus({preventScroll:true});
    returnFocus = null;
  }
  button.addEventListener('click', () => {
    const value = pending; close();
    if (value) { try { onNote(value); } catch (error) { notice(error.message); } }
  });
  const outside = event => { if (!menu.contains(event.target)) close(); };
  host.addEventListener('pointerdown', outside, true);
  host.addEventListener('keydown', event => {
    if (!menu.matches(':popover-open')) return;
    if (event.key === 'Escape' || event.key === 'Tab') { close(true); if (event.key === 'Escape') event.preventDefault(); }
  });
  host.addEventListener('scroll', () => close(), true);
  host.defaultView.addEventListener('resize', () => close());
  function bind(root, getDraft, frame = null) {
    const content = root.ownerDocument || root;
    const controller = new AbortController();
    if (frame) { frameController?.abort(); frameController = controller; }
    const options = {signal:controller.signal};
    root.addEventListener('contextmenu', event => {
      close();
      const value = getDraft();
      if (!value) return; // Keep the browser's menu for non-selections.
      event.preventDefault(); event.stopPropagation();
      pending = structuredClone(value);
      returnFocus = content.activeElement;
      const offset = frame?.getBoundingClientRect() || {left:0,top:0};
      let x = event.clientX, y = event.clientY;
      if (!x && !y) {
        const selection = content.defaultView.getSelection();
        const rect = selection?.rangeCount ? selection.getRangeAt(0).getBoundingClientRect() : null;
        x = rect?.left || 8; y = rect?.bottom || 8;
      }
      menu.style.left = '0px'; menu.style.top = '0px'; menu.showPopover();
      menu.style.left = `${Math.max(8, Math.min(offset.left + x, host.defaultView.innerWidth - menu.offsetWidth - 8))}px`;
      menu.style.top = `${Math.max(8, Math.min(offset.top + y, host.defaultView.innerHeight - menu.offsetHeight - 8))}px`;
      button.focus({preventScroll:true});
    }, options);
    if (frame) {
      content.addEventListener('pointerdown', outside, {...options,capture:true});
      content.addEventListener('scroll', () => close(), {...options,capture:true});
    }
    return () => controller.abort();
  }
  return {bind, close};
}

export function readArticleQuote(content, maxLength = 11994) {
  const selection = content?.defaultView?.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return '';
  const range = selection.getRangeAt(0), main = content.querySelector('main');
  if (!main?.contains(range.startContainer) || !main.contains(range.endContainer)) return '';
  const text = selection.toString().trim();
  if (text.length > maxLength) throw new Error('选段过长，请分段记笔记；原选文没有被截断。');
  return text;
}

export function setupReadingDisclosure(panel, {read, write}) {
  panel.open = read('paper-reading-work-open', false) === true;
  panel.addEventListener('toggle', () => write('paper-reading-work-open', panel.open));
}
