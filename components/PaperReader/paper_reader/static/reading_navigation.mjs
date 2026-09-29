// Keep untrusted article links from navigating the reading iframe.
export function readingDocument(frame) {
  try {
    const content = frame.contentDocument;
    if (!content || !frame.contentWindow || content.URL !== 'about:srcdoc') return null;
    void frame.contentWindow.scrollY;
    return content;
  } catch { return null; }
}
export function bindReadingLinks(content, {notice, openExternal}) {
  const navigate = event => {
    const link = event.target?.closest?.('a[href]');
    if (!link) return;
    event.preventDefault();
    const href = (link.getAttribute('href') || '').trim();
    if (href.startsWith('#')) {
      let id;
      try { id = decodeURIComponent(href.slice(1)); } catch { notice('未找到对应位置'); return; }
      const target = content.getElementById(id) || Array.from(content.getElementsByName(id)).find(n => n.tagName === 'A');
      if (target) target.scrollIntoView({block:'start'});
      else notice('未找到对应位置');
      return;
    }
    // Relative links have no trustworthy base inside srcdoc.
    if (/^https?:\/\//i.test(href)) openExternal(href);
    else notice('此链接暂无可用的外部地址');
  };
  content.addEventListener('click', navigate);
  content.addEventListener('auxclick', navigate);
}
