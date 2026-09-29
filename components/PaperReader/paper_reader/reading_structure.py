"""Inert paper structure, locked objects and deterministic translation assembly."""
import copy
import hashlib
import html
import re

from bs4 import BeautifulSoup, NavigableString, Tag

VERSION = "structure-3"
TOKEN = re.compile(r"⟦[POC][0-9]+⟧")
LOCK_CLASSES = re.compile(r"(?:^|[_-])(listing|verbatim|algorithm\w*|pseudocode|typewriter|code\w*|math\w*|equation\w*)(?:$|[_-])", re.IGNORECASE)


def locked(node):
    return isinstance(node, Tag) and (
        node.name in {"pre", "code", "math", "svg", "img", "br"}
        or node.get("translate") == "no"
        or any(LOCK_CLASSES.search(c) or c in {"notranslate", "ltx_cite", "ltx_ref", "ltx_bibref", "ltx_tag"}
               for c in node.get("class", [])))


def clean(source):
    soup = BeautifulSoup(source, "html.parser")
    root = soup.find("article") or soup.find("main")
    if root is None:
        raise ValueError("full_html_not_found")
    root = copy.copy(root)
    for node in root.select("script,style,iframe,object,embed,form,input,button,link,meta,base"):
        node.decompose()
    for node in [root, *root.find_all(True)]:
        for attr in list(node.attrs):
            if attr.lower().startswith("on") or attr in {"srcdoc", "srcset", "style"}:
                del node[attr]
        for attr in ("href", "src", "xlink:href"):
            if str(node.get(attr, "")).strip().lower().startswith(("javascript:", "data:", "file:")):
                del node[attr]
    return root


def encode(node):
    pieces = {}

    def visit(child):
        if isinstance(child, NavigableString):
            return str(child)
        if not isinstance(child, Tag):
            return ""
        index = len(pieces)
        if locked(child):
            key = f"⟦P{index}⟧"
            pieces[key] = str(child)
            return key
        a, b = f"⟦O{index}⟧", f"⟦C{index}⟧"
        pieces[a] = str(child).split(">", 1)[0] + ">"
        pieces[b] = "</" + child.name + ">"
        return a + "".join(visit(c) for c in child.children) + b

    return "".join(visit(c) for c in node.children), pieces


def prepare(source):
    root = clean(source)
    # Lock first, before selecting outer paragraphs/tables. Whole algorithms stay intact.
    inventory = []
    for node in root.find_all(True):
        if locked(node) and not any(locked(p) for p in node.parents if p is not root):
            identity = f"p{len(inventory)}"
            node["data-protected"] = identity
            inventory.append({"id": identity, "tag": node.name,
                              "sha256": hashlib.sha256(str(node).encode()).hexdigest()})
    units, selected = [], []
    for node in root.select("h1,h2,h3,h4,h5,h6,p,figcaption,caption,td,th,li,dt,dd"):
        if locked(node) or any(locked(p) for p in node.parents):
            continue
        if any(any(p is prev for p in node.parents) for prev in selected):
            continue
        if node.name in {"figcaption", "td", "th", "li", "dd"} and node.find("p"):
            continue
        text, pieces = encode(node)
        if not re.search(r"[A-Za-z]{2}", TOKEN.sub("", text)):
            continue
        identity = f"u{len(units)}"
        node["data-reading-unit"] = identity
        units.append({"id": identity, "text": text, "pieces": pieces})
        selected.append(node)
    if not units:
        raise ValueError("no_translatable_text")
    return {"version": VERSION, "original": str(root), "units": units, "protected": inventory}


def cosmetic_hyphen_pairs(unit):
    """Unstyled spans around word-internal hyphens are not scientific objects."""
    pairs = []
    for opening, markup in unit['pieces'].items():
        if not opening.startswith('⟦O'):
            continue
        closing = '⟦C' + opening[2:]
        if unit['pieces'].get(closing) != '</span>':
            continue
        node = BeautifulSoup(markup, 'html.parser').find('span')
        if node is None or set(node.attrs) - {'id', 'class'} or node.get('class', []) != ['ltx_text']:
            continue
        pattern = r'(?<=[A-Za-z])' + re.escape(opening + '-' + closing) + r'(?=[A-Za-z])'
        if re.search(pattern, unit.get('text', '')):
            pairs.append((opening, closing))
    return pairs


def restore_unit(unit, translated):
    if not isinstance(translated, str) or not translated.strip():
        raise ValueError("empty_translation")
    tokens = TOKEN.findall(translated)
    expected = dict(unit["pieces"])
    for opening, closing in cosmetic_hyphen_pairs(unit):
        if opening not in tokens and closing not in tokens:
            expected.pop(opening)
            expected.pop(closing)
    if sorted(tokens) != sorted(expected):
        raise ValueError("protected_marker_changed")
    stack = []
    for token in tokens:
        if token[1] == "O":
            stack.append(token[2:-1])
        elif token[1] == "C" and (not stack or stack.pop() != token[2:-1]):
            raise ValueError("inline_structure_changed")
    if stack:
        raise ValueError("inline_structure_changed")
    return "".join(unit["pieces"].get(p, html.escape(p))
                   for p in re.split(r"(⟦[POC][0-9]+⟧)", translated))


def assemble(plan, translations):
    if set(translations) != {u["id"] for u in plan["units"]}:
        raise ValueError("paragraph_coverage_changed")
    root = BeautifulSoup(plan["original"], "html.parser")
    for unit in plan["units"]:
        target = root.select_one('[data-reading-unit="' + unit["id"] + '"]')
        fragment = BeautifulSoup(restore_unit(unit, translations[unit["id"]]), "html.parser")
        target.clear()
        for child in list(fragment.contents):
            target.append(child)
    actual = [{"id": n["data-protected"], "tag": n.name,
               "sha256": hashlib.sha256(str(n).encode()).hexdigest()}
              for n in root.select("[data-protected]")]
    if sorted(actual, key=lambda x: x["id"]) != sorted(plan["protected"], key=lambda x: x["id"]):
        raise ValueError("protected_object_changed")
    return str(root)


CSS = """
body{margin:0;font-family:system-ui,'Microsoft YaHei',sans-serif;line-height:1.85}
main{max-width:1100px;margin:auto;padding:24px}article,section{min-width:0}
h1{font-size:1.8em;line-height:1.4}h2{font-size:1.4em}h3{font-size:1.15em}
p{margin:.7em 0}figure{margin:1.2em 0}img{max-width:100%;height:auto}
table{display:block;max-width:100%;overflow:auto;border-collapse:collapse;margin:1em 0}
td,th{padding:8px;vertical-align:top;border:1px solid var(--aos-line)}
pre,.ltx_listing,.ltx_algorithm,.ltx_algpseudocode{overflow:auto;white-space:pre;tab-size:4;padding:12px}
code,.ltx_font_typewriter,.ltx_verbatim{font-family:ui-monospace,monospace;white-space:pre-wrap}
math[display=block],.ltx_equation,.ltx_equationgroup{max-width:100%;overflow-x:auto}
.ltx_tag{margin-right:.5em}.ltx_ERROR{color:inherit}.ltx_para{margin:.8em 0}
.ltx_authors{text-align:center}.ltx_author{display:inline-block;margin:8px}
@media(max-width:600px){main{padding:14px}h1{font-size:1.4em}}
"""
