"""Link generated reference headings to Python source declarations."""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

_HEADING = re.compile(r'^(<a id="([^"]+)"></a>\n\n)(#{1,6} )(.+)$', re.MULTILINE)


def source_target(source: Path, output: Path, line: int | None = None) -> str:
    """Return a GitHub-relative URL from the output file to a source line."""
    relative = Path(os.path.relpath(source.resolve(), output.resolve().parent))
    target = relative.as_posix()
    if not target.startswith("."):
        target = f"./{target}"
    return quote(target, safe="/.") + (f"#L{line}" if line is not None else "")


def declaration_lines(source: Path, module: str) -> dict[str, int]:
    """Index modules, scoped declarations and assignments without importing code."""
    lines = {module: 1}

    def visit(node: ast.AST, scope: str) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = f"{scope}.{node.name}"
            lines.setdefault(scope, node.lineno)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store):
                        lines.setdefault(f"{scope}.{name.id}", node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(ast.parse(source.read_text(encoding="utf-8"), filename=str(source)), module)
    return lines


def link_document(document: str, modules: Mapping[str, Path], output: Path) -> str:
    """Link heading labels while preserving explicit anchors and TOC destinations."""
    declarations = {
        name: (source, line)
        for module, source in modules.items()
        for name, line in declaration_lines(source, module).items()
    }

    def replace(match: re.Match[str]) -> str:
        anchor, name, level, title = match.groups()
        if name not in declarations:
            if any(
                name == module or name.startswith(f"{module}.") for module in modules
            ):
                raise ValueError(f"No source declaration for documented symbol {name}")
            return match.group(0)
        source, line = declarations[name]
        return f"{anchor}{level}[{title}]({source_target(source, output, line)})"

    return _HEADING.sub(replace, document)


def main() -> None:
    """Link the API generator's Markdown stream to its source tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    modules = {}
    for source in sorted((args.source_root / "fim").rglob("*.py")):
        module = ".".join(source.relative_to(args.source_root).with_suffix("").parts)
        modules[module.removesuffix(".__init__")] = source
    document = sys.stdin.read()
    for label, source in (
        ("source-tree orientation", args.source_root / "README.md"),
        ("developer guide", args.source_root.parent / "doc" / "developer.md"),
    ):
        document = re.sub(
            rf"\[{label}\]\([^)]+\)",
            f"[{label}]({source_target(source, args.output)})",
            document,
        )
    sys.stdout.write(link_document(document, modules, args.output))


if __name__ == "__main__":
    main()
