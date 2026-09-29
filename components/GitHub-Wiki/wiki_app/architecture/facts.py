"""Language parsing independent of repository layout and visualization."""

import ast
import hashlib
import json
from functools import lru_cache
from pathlib import Path

PARSER_VERSION = "3-ts0252"
LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
}


def digest(value):
    return hashlib.sha256(value).hexdigest()


@lru_cache(maxsize=3)
def parser_for(language):
    import tree_sitter_javascript
    import tree_sitter_typescript
    from tree_sitter import Language, Parser

    grammar = (
        tree_sitter_javascript.language()
        if language == "javascript"
        else tree_sitter_typescript.language_tsx()
        if language == "tsx"
        else tree_sitter_typescript.language_typescript()
    )
    return Parser(Language(grammar))


def parse(text, language):
    imports, declarations = [], []
    result = {"language": language, "imports": imports, "declarations": declarations, "errors": []}
    if language == "unsupported":
        return result
    if language == "python":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            result["errors"].append("syntax_error")
            return result
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        {
                            "specifier": alias.name,
                            "names": [],
                            "level": 0,
                            "kind": "import",
                            "start": node.lineno,
                            "end": node.end_lineno,
                        }
                    )
            elif isinstance(node, ast.ImportFrom):
                imports.append(
                    {
                        "specifier": node.module or "",
                        "names": [a.name for a in node.names],
                        "level": node.level,
                        "kind": "import",
                        "start": node.lineno,
                        "end": node.end_lineno,
                    }
                )
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                declarations.append(
                    {
                        "name": node.name,
                        "kind": type(node).__name__,
                        "start": node.lineno,
                        "end": node.end_lineno,
                    }
                )
        return result
    tree = parser_for(language).parse(text.encode())
    root = tree.root_node
    if root.has_error:
        result["errors"].append("partial_syntax")
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.named_children))
        raw = node.text.decode()
        if node.type in {
            "function_declaration",
            "class_declaration",
            "interface_declaration",
            "type_alias_declaration",
        }:
            name = node.child_by_field_name("name")
            if name:
                declarations.append(
                    {
                        "name": name.text.decode(),
                        "kind": node.type,
                        "start": node.start_point.row + 1,
                        "end": node.end_point.row + 1,
                    }
                )
        kind, source = None, None
        if node.type in {"import_statement", "export_statement"}:
            source = node.child_by_field_name("source")
            kind = (
                "type"
                if raw.startswith(("import type ", "export type "))
                else "reexport"
                if node.type == "export_statement"
                else "import"
            )
        elif node.type == "call_expression":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if fn and fn.text in {b"require", b"import"} and args and args.named_children:
                source = args.named_children[0]
                kind = "require" if fn.text == b"require" else "dynamic"
                if source.type != "string":
                    imports.append(
                        {
                            "specifier": "<expression>",
                            "kind": "dynamic_unresolved",
                            "start": node.start_point.row + 1,
                            "end": node.end_point.row + 1,
                        }
                    )
                    continue
        if source and kind and source.type == "string":
            value = source.text.decode()[1:-1]
            imports.append(
                {
                    "specifier": value,
                    "kind": kind,
                    "start": node.start_point.row + 1,
                    "end": node.end_point.row + 1,
                }
            )
    return result


def cached_parse(path, text, cache):
    language = LANGUAGES.get(Path(path).suffix, "unsupported")
    key = digest((PARSER_VERSION + ":" + language + ":" + text).encode())
    target = Path(cache) / (key + ".json")
    if target.exists():
        return json.loads(target.read_text()), True
    result = parse(text, language)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False))
    temporary.replace(target)
    return result, False
