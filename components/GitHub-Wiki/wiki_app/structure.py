"""Static Python declarations and import edges, without executing source."""

import ast
import re
from pathlib import PurePosixPath

from .engine import cite
from .readable import readable_excerpt


def describe(files, url, sha):
    modules, declarations, edges, failures = {}, [], [], []
    for path, text in files.items():
        if not path.endswith(".py"):
            continue
        module = path[:-3].replace("/", ".")
        module = module.removesuffix(".__init__")
        modules[module] = path
        try:
            tree = ast.parse(text)
        except (SyntaxError, RecursionError, ValueError):
            failures.append(path)
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                calls = []
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        try:
                            name = ast.unparse(child.func)
                        except RecursionError:
                            continue
                        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name) and name not in calls:
                            calls.append(name)
                declarations.append(
                    {
                        "path": path,
                        "name": node.name,
                        "kind": "类" if isinstance(node, ast.ClassDef) else "函数",
                        "start": node.lineno,
                        "end": node.end_lineno,
                        "calls": calls[:8],
                    }
                )
    for module, path in modules.items():
        try:
            tree = ast.parse(files[path])
        except (SyntaxError, RecursionError, ValueError):
            continue
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    package = module.split(".") if path.endswith("/__init__.py") else module.split(".")[:-1]
                    package = package[: len(package) - node.level + 1]
                    target = ".".join(package + ([node.module] if node.module else []))
                else:
                    target = node.module or ""
                targets = [target] + [target + "." + n.name for n in node.names]
            for target in targets:
                if target in modules and modules[target] != path:
                    edge = (path, modules[target], node.lineno, node.end_lineno)
                    if edge not in edges:
                        edges.append(edge)

    def reference(path, start, end):
        lines = files[path].splitlines()
        from urllib.parse import quote

        return {
            "source": path,
            "start": start,
            "end": end,
            "commit": sha,
            "url": f"{url}/blob/{sha}/{quote(path, safe='/')}#L{start}-L{end}",
            "text_preview": "\n".join(lines[start - 1 : end]),
        }

    return declarations, edges, failures, reference


def compile_pages(files, url, sha, manifest):
    # Deferred engine import avoids initializing source acquisition just to inspect AST.
    declarations, edges, failures, reference = describe(files, url, sha)
    mode = "本页由源码静态分析生成，未调用模型。只报告文档摘录、声明和导入关系；不推断运行时行为。"
    pages = []

    def page(slug, title, lines, refs):
        pages.append(
            {
                "slug": slug,
                "title": title,
                "markdown": readable_excerpt("# " + title + "\n\n" + mode + "\n\n" + "\n\n".join(lines)),
                "citations": refs,
            }
        )

    readmes = sorted(
        [p for p in files if PurePosixPath(p).name.lower().startswith("readme") and files[p].strip()],
        key=lambda p: (p.count("/"), len(p)),
    )
    refs = []
    lines = [f"固定源码版本：{sha}。本次读取 {manifest['included']} 个文本文件。"]
    if readmes:
        path = readmes[0]
        excerpt = files[path].splitlines()[:30]
        c = reference(path, 1, len(excerpt))
        refs.append(c)
        lines += ["## 项目自述（文档原文节选）", cite(c), "\n".join("> " + line for line in excerpt)]
    else:
        lines += ["仓库未发现 README；无法仅凭目录确定项目用途。"]
    top = sorted({p.split("/")[0] for p in files})
    lines += ["## 顶层文件与目录", "\n".join("- " + p for p in top[:35])]
    page("overview", "项目概览", lines, refs)

    refs = []
    lines = ["## Python 模块的显式导入"]
    limited = edges[:24]
    if limited:
        nodes = list(dict.fromkeys(p for e in limited for p in e[:2]))
        graph = ["flowchart LR"]
        graph += [f'  N{i}["{p.replace(chr(34), "")}"]' for i, p in enumerate(nodes)]
        graph += [f"  N{nodes.index(a)} --> N{nodes.index(b)}" for a, b, _, _ in limited]
        lines += [
            "箭头表示源码 import/from 的静态依赖；不表示已运行或完整调用链。",
            "~~~mermaid\n" + "\n".join(graph) + "\n~~~",
        ]
        for a, b, start, end in limited:
            c = reference(a, start, end)
            refs.append(c)
            lines += [f"- {a} 导入 {b}。{cite(c)}"]
    else:
        lines += ["未识别出可解析到本仓库文件的 Python 导入边。其他语言请结合文件与文档阅读。"]
    lines += ["## 主要声明"]
    selected = [d for d in declarations if not d["path"].startswith("tests/")][:24]
    for d in selected:
        c = reference(d["path"], d["start"], min(d["end"], d["start"] + 8))
        refs.append(c)
        lines += [f"- {d['path']} 声明{d['kind']} {d['name']}。{cite(c)}"]
    page("architecture", "架构与模块", lines, refs)

    refs = []
    lines = [
        "## 从入口追踪调用表达式",
        "下面列出函数体内出现的调用名称，包括条件分支；未解析动态绑定，也不保证执行顺序。",
    ]
    selected = sorted(
        [d for d in declarations if d["kind"] == "函数" and not d["path"].startswith("tests/")],
        key=lambda d: (
            not any(x in d["name"] for x in ("main", "start", "run", "create_app", "serve")),
            d["path"],
        ),
    )[:12]
    for d in selected:
        c = reference(d["path"], d["start"], min(d["end"], d["start"] + 59))
        refs.append(c)
        lines += [
            f"### {d['path']} / {d['name']}",
            "调用表达式：" + ("、".join(d["calls"]) or "未发现直接调用") + "。 " + cite(c),
        ]
    if not selected:
        lines += ["未识别 Python 顶层函数；应从项目 README 和声明的运行入口继续定位。"]
    page("flows", "关键流程", lines, refs)

    refs = []
    lines = ["推荐按用途 → 配置 → 入口 → 核心声明 → 测试逐步阅读。此顺序为阅读建议。"]
    ordered = list(
        dict.fromkeys(
            readmes[:1]
            + [
                p
                for p in files
                if PurePosixPath(p).name in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
            ]
            + [d["path"] for d in selected[:4]]
            + [p for p in files if p.startswith("tests/")][:4]
        )
    )
    for i, path in enumerate(ordered, 1):
        c = reference(path, 1, min(30, len(files[path].splitlines())))
        refs.append(c)
        lines += [f"{i}. 阅读 {path}，核对其声明与上下游引用。{cite(c)}"]
    page("reading", "推荐阅读路线", lines, refs)

    refs = []
    lines = ["只展示仓库声明的命令和测试入口；本产品没有执行它们。"]
    for path in readmes[:2]:
        all_lines = files[path].splitlines()
        hits = [
            i
            for i, t in enumerate(all_lines)
            if any(
                k in t.lower()
                for k in ("uv run", "uv sync", "pytest", "npm ", "pip ", "cargo ", "go test", "docker ")
            )
        ]
        for i in hits[:8]:
            c = reference(path, max(1, i), min(len(all_lines), i + 3))
            refs.append(c)
            lines += [f"- 文档命令：{all_lines[i].strip()} {cite(c)}"]
    tests = [d for d in declarations if d["name"].startswith("test_")][:15]
    lines += ["## 测试声明"]
    for d in tests:
        c = reference(d["path"], d["start"], min(d["end"], d["start"] + 5))
        refs.append(c)
        lines += [f"- {d['name']}，位于 {d['path']}。{cite(c)}"]
    page("operations", "运行与测试", lines, refs)

    lines = [
        "- 静态导入与调用表达式不能证明运行时调用链、配置有效性或测试通过。",
        "- 文档自述与实现可能存在差异；本模式不自动裁定冲突。",
        "- 未执行仓库代码；未检查线上服务、私有依赖或外部系统。",
        f"- 跳过 {len(manifest['skipped'])} 个文件；完整覆盖清单保存在导出 manifest.json。",
        f"- 无法解析的 Python 文件：{', '.join(failures) or '无'}。",
        "- 深度中文综合与语义问答需要在模型设置中启用提供商。",
    ]
    page("questions", "边界与未解问题", lines, [])
    pages[1]["diagram"] = [{"source": a, "target": b} for a, b, _, _ in edges[:10]]
    return pages
