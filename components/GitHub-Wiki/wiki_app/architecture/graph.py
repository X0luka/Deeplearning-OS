"""Repository resolution and graph aggregation; no model or drawing dependency."""

import json
import posixpath
import re
from pathlib import PurePosixPath

from .facts import PARSER_VERSION, cached_parse, digest

ANALYZER_VERSION = "2"
EXTENSIONS = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".json")


def normalize(path):
    value = posixpath.normpath(path)
    return None if value.startswith(("/", "../")) or value == ".." else value


def json_config(text):
    # Strip JSONC comments without touching quoted strings.
    text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*[\s\S]*?\*/', lambda m: m.group(1) or "", text)
    return json.loads(re.sub(r",\s*([}\]])", r"\1", text))


def analyze(files, cache, version, sha, excluded=0):
    paths = set(files)
    facts, hits = {}, 0
    nodes, edges, issues = [], {}, []
    for path in sorted(files):
        text = files[path]
        try:
            fact, hit = cached_parse(path, text, cache)
        except (ValueError, RecursionError, UnicodeError, TimeoutError) as exc:
            fact, hit = {"language": "unsupported", "imports": [], "declarations": [],
                         "errors": ["file_parse_failed:" + type(exc).__name__]}, False
        facts[path] = fact
        hits += int(hit)
        nodes.append(
            {
                "id": "file:" + path,
                "path": path,
                "label": PurePosixPath(path).name,
                "kind": "file",
                "language": fact["language"],
                "test": bool(re.search(r"(^|/)(__tests__|tests?|specs?)(/|$)|[._](test|spec)\.", path)),
                "declarations": fact["declarations"],
                "lines": len(text.splitlines()),
            }
        )
        for error in fact["errors"]:
            issues.append({"path": path, "reason": error})
    configs, packages = {}, {}
    for path in sorted(files):
        text = files[path]
        if PurePosixPath(path).name in {"tsconfig.json", "jsconfig.json"}:
            try:
                configs[path] = json_config(text)
            except (ValueError, TypeError):
                issues.append({"path": path, "reason": "invalid_config"})
        if PurePosixPath(path).name == "package.json":
            try:
                package = json.loads(text)
                if isinstance(package, dict) and isinstance(package.get("name"), str):
                    packages.setdefault(package["name"], []).append((posixpath.dirname(path), package))
            except ValueError:
                issues.append({"path": path, "reason": "invalid_package"})
    config_cache = {}

    def config_for(path, seen=()):
        if path in config_cache:
            return config_cache[path]
        if path in seen or path not in configs or not isinstance(configs[path], dict):
            return {}
        cfg, result = configs[path], {}
        parent = cfg.get("extends")
        if isinstance(parent, str) and parent.startswith("."):
            parent = normalize(posixpath.join(posixpath.dirname(path), parent))
            if parent and not parent.endswith(".json"):
                parent += ".json"
            result.update(config_for(parent, (*seen, path)))
        elif parent:
            issues.append({"path": path, "reason": "unsupported_config_extends"})
        options = cfg.get("compilerOptions", {})
        if isinstance(options, dict):
            base = (
                normalize(posixpath.join(posixpath.dirname(path), options.get("baseUrl", ".")))
                if isinstance(options.get("baseUrl", "."), str)
                else None
            )
            if "baseUrl" in options:
                result["base"] = base
            if isinstance(options.get("paths"), dict):
                result["aliases"] = options["paths"]
                result["alias_base"] = base
        config_cache[path] = result
        return result

    def candidate(base):
        if base is None:
            return None
        # TypeScript projects commonly import .js specifiers backed by .ts source.
        alternatives = [base]
        if base.endswith((".js", ".jsx", ".mjs", ".cjs")):
            stem = base.rsplit(".", 1)[0]
            alternatives = [stem + x for x in (".ts", ".tsx", ".mts", ".cts")] + alternatives
        for value in alternatives:
            for suffix in EXTENSIONS:
                if value + suffix in paths:
                    return value + suffix
            for suffix in EXTENSIONS[1:]:
                if value + "/index" + suffix in paths:
                    return value + "/index" + suffix
        return None

    def py_target(module):
        path = module.replace(".", "/")
        for value in (
            path + ".py",
            path + "/__init__.py",
            "src/" + path + ".py",
            "src/" + path + "/__init__.py",
        ):
            if value in paths:
                return value
        return None

    def resolve(path, item):
        spec = item["specifier"]
        if item["kind"] == "dynamic_unresolved":
            return [(None, "unresolved")]
        if facts[path]["language"] == "python":
            level = item.get("level", 0)
            package = posixpath.dirname(path).split("/") if posixpath.dirname(path) else []
            if level:
                if level > len(package):
                    return [(None, "unresolved")]
                prefix = package[: len(package) - level + 1]
                base = ".".join(prefix + ([spec] if spec else []))
            else:
                base = spec
            names = item.get("names", [])
            if names:
                values = [py_target(base + "." + name) or py_target(base) for name in names]
            else:
                values = [py_target(base)]
            return [
                (v, "internal" if v else "unresolved" if level else "external") for v in dict.fromkeys(values)
            ]
        if spec.startswith("."):
            value = candidate(normalize(posixpath.join(posixpath.dirname(path), spec)))
            return [(value, "internal" if value else "unresolved")]
        ancestors = sorted(
            [c for c in configs if not posixpath.dirname(c) or path.startswith(posixpath.dirname(c) + "/")],
            key=lambda c: (-c.count("/"), c),
        )
        cfg = config_for(ancestors[0]) if ancestors else {}
        for alias, targets in cfg.get("aliases", {}).items():
            if not isinstance(targets, list):
                continue
            match = re.fullmatch(re.escape(alias).replace(r"\*", "(.*)"), spec)
            if match:
                captured = match.group(1) if match.groups() else ""
                for target in targets:
                    if isinstance(target, str) and cfg.get("alias_base") is not None:
                        value = candidate(
                            normalize(posixpath.join(cfg["alias_base"], target.replace("*", captured)))
                        )
                        if value:
                            return [(value, "internal")]
                return [(None, "unresolved")]
        if cfg.get("base") is not None:
            value = candidate(normalize(posixpath.join(cfg["base"], spec)))
            if value:
                return [(value, "internal")]
        name = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
        if name in packages:
            matches = packages[name]
            if len(matches) != 1:
                return [(None, "unresolved")]
            directory, package = matches[0]
            rest = spec[len(name) :].lstrip("/")
            entry = rest or package.get("types") or package.get("module") or package.get("main") or "index"
            if package.get("exports") and not rest:
                export = package["exports"]
                if isinstance(export, str):
                    entry = export
                elif isinstance(export, dict) and isinstance(export.get("."), str):
                    entry = export["."]
                else:
                    return [(None, "unresolved")]
            value = candidate(normalize(posixpath.join(directory, entry))) if isinstance(entry, str) else None
            return [(value, "internal" if value else "unresolved")]
        return [(None, "external")]

    extra = {}
    for path, fact in facts.items():
        for item in fact["imports"]:
            for target, status in resolve(path, item):
                destination = "file:" + target if target else status + ":" + item["specifier"]
                if target is None:
                    extra[destination] = {
                        "id": destination,
                        "label": item["specifier"],
                        "kind": status,
                        "test": False,
                    }
                key = ("file:" + path, destination, item["kind"])
                if key not in edges:
                    edges[key] = {
                        "id": "edge:" + digest(json.dumps(key).encode())[:24],
                        "source": key[0],
                        "target": destination,
                        "kind": item["kind"],
                        "status": status,
                        "evidence": [],
                    }
                proof = {
                    "path": path,
                    "start": item["start"],
                    "end": item["end"],
                    "specifier": item["specifier"],
                }
                if proof not in edges[key]["evidence"]:
                    edges[key]["evidence"].append(proof)
    return {
        "schema_version": 1,
        "parser_version": PARSER_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "version_id": version,
        "commit": sha,
        "nodes": nodes + sorted(extra.values(), key=lambda n: n["id"]),
        "edges": sorted(edges.values(), key=lambda e: e["id"]),
        "coverage": {
            "files": len(files),
            "parsed": sum(f["language"] != "unsupported" for f in facts.values()),
            "unsupported": sum(f["language"] == "unsupported" for f in facts.values()),
            "excluded": excluded,
            "cache_hits": hits,
            "issues": issues,
            "unresolved": sum(e["status"] == "unresolved" for e in edges.values()),
        },
    }


def project(graph, scope="", view="overview", query="", tests=False, external=False, types=True):
    all_nodes = {n["id"]: n for n in graph["nodes"]}
    visible, mapping = {}, {}
    scope = scope.strip("/")
    for node in graph["nodes"]:
        path = node.get("path")
        if path is None:
            if external:
                mapping[node["id"]] = node["id"]
                visible[node["id"]] = node
            continue
        if scope and not path.startswith(scope + "/") and path != scope:
            continue
        if not tests and node["test"] or query.lower() not in path.lower():
            continue
        rest = path[len(scope) + 1 :] if scope and path != scope else path
        if view == "overview" and "/" in rest:
            directory = (scope + "/" if scope else "") + rest.split("/")[0]
            identity = "module:" + directory
            visible.setdefault(
                identity,
                {
                    "id": identity,
                    "path": directory,
                    "label": rest.split("/")[0],
                    "kind": "module",
                    "files": 0,
                },
            )["files"] += 1
        else:
            identity = node["id"]
            visible[identity] = node
        mapping[node["id"]] = identity
    groups = {}
    for edge in graph["edges"]:
        if not types and edge["kind"] == "type":
            continue
        source, target = mapping.get(edge["source"]), mapping.get(edge["target"])
        if source is None or target is None or source == target:
            continue
        key = (source, target, edge["kind"])
        group = groups.setdefault(
            key,
            {
                "id": "view:" + digest(json.dumps(key).encode())[:24],
                "source": source,
                "target": target,
                "kind": edge["kind"],
                "evidence": [],
                "edges": [],
            },
        )
        group["evidence"].extend(edge["evidence"])
        group["edges"].append(edge["id"])
    used = {x for e in groups.values() for x in (e["source"], e["target"])}
    visible = {k: n for k, n in visible.items() if n["kind"] == "module" or "path" in n or k in used}
    too_large = len(visible) > 150 or len(groups) > 600
    return {
        "analysis_id": graph["analysis_id"],
        "version_id": graph["version_id"],
        "commit": graph["commit"],
        "coverage": graph["coverage"],
        "scope": scope,
        "view": view,
        "counts": {
            "nodes": len(visible),
            "edges": len(groups),
            "hidden_files": sum("path" in n for n in all_nodes.values())
            - sum(1 for k in mapping if "path" in all_nodes[k]),
        },
        "too_large": too_large,
        "nodes": [] if too_large else list(visible.values()),
        "edges": [] if too_large else list(groups.values()),
    }
