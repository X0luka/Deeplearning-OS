"""Render README quotation text without displaying or executing HTML markup."""

import re
from html import escape
from html.parser import HTMLParser


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def readable_excerpt(markdown):
    # Only normalize quoted README excerpts, never code fences or source views.
    lines, output, i, fence = markdown.splitlines(), [], 0, None
    while i < len(lines):
        line = lines[i]
        marker = re.match(r"^\s*(" + chr(96) + r"{3,}|~{3,})", line)
        if marker:
            found = marker.group(1)[0]
            fence = None if fence == found else found if fence is None else fence
        if fence is None and line.startswith("> "):
            group = []
            while i < len(lines) and lines[i].startswith("> "):
                group.append(lines[i][2:])
                i += 1
            text = "\n".join(group)
            if re.search(r"</?[A-Za-z][^>]*>", text):
                parser = VisibleText()
                parser.feed(text)
                text = escape("".join(parser.parts), quote=False)
            output.extend("> " + x.strip() for x in text.splitlines() if x.strip())
            continue
        output.append(line)
        i += 1
    return "\n".join(output)
