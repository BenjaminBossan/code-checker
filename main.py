#!/usr/bin/env python3
"""check_code – A lightweight static-analysis utility for Python projects.

Usage (CLI):
    check_code  src/foo/  another_file.py  -o result.json

The tool walks all supplied paths (recursively for directories) and analyses every
``.py`` file it encounters (symlinks are ignored).  The resulting JSON describes
your codebase as a *tree* where the artificial outer levels that contain **no**
Python files are automatically pruned away.  For example, given a path
`foo/bar/baz/src/myprogram/` the tree will start at `myprogram` (the first
folder that actually contains a `.py` file).

Hierarchy:
* Directory → children (files or sub-directories)
* File      → children (top-level classes & functions)
* Class     → children (methods)
* Function/Method → **leaf** with metrics

Each leaf node contains static metadata such as its source, docstring, statement
count, cyclomatic complexity, etc.  The program is dependency-free (stdlib only)
and built with Python ≥3.12 in mind.  It is structured as a reusable core API
plus a thin CLI wrapper.  Optional duplication metrics and tiny progress bars
are available via command line flags.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import tokenize
import sys
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, MutableMapping, Sequence


def _print_progress(current: int, total: int, prefix: str = "") -> None:
    """Print a very small progress bar."""
    bar_len = 20
    filled = int(bar_len * current / total)
    bar = "#" * filled + "-" * (bar_len - filled)
    msg = f"\r{prefix} [{bar}] {current}/{total}"
    print(msg, end="", file=sys.stderr)
    if current == total:
        print(file=sys.stderr)


####################
# Code duplication #
####################

_W = 5  # n-gram width  (5-token shingles)
_K = 50  # how many hashes we keep (winnowing window size)
_MIN_TOKS = 25  # minimum tokens for consideration for code duplication
_JACCARD_MIN = 0.3  # minimum IoU for similarity computation, optimization


def _token_fingerprint(source: str) -> set[str]:
    """
    Return a small, order-insensitive *fingerprint set* for the given
    source code string.  Two similar functions share many fingerprints.

    Algorithm: tokenise → normalise → slide a W-token window →
    hash the window → keep K smallest hashes (winnowing).
    """
    toks: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        # Skip indentation / newlines / comments
        if tok.type in (
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.COMMENT,
        ):
            continue
        s = tok.string
        # Canonicalise literals so "42" and "99" look the same
        if tok.type == tokenize.NUMBER:
            s = "0"
        elif tok.type == tokenize.STRING:
            s = "STR"
        toks.append(s)

    if len(toks) < _MIN_TOKS:
        return set()

    # make shingles
    hashes: list[str] = []
    for i in range(len(toks) - _W + 1):
        window = " ".join(toks[i : i + _W])
        hashes.append(hashlib.md5(window.encode()).hexdigest())

    return set(sorted(hashes)[:_K])


def compute_duplication(
    all_files: Iterable[CodeNode], *, show_progress: bool = False
) -> None:
    """Mutates each CodeNode(metrics) in-place, adding a 'duplication' entry."""
    # flatten to a list of leaf nodes
    leaves: list[CodeNode] = []

    def _walk(node: CodeNode):
        if not node.children:
            leaves.append(node)
        else:
            for c in node.children:
                _walk(c)

    for f in all_files:
        _walk(f)

    # pre-compute fingerprints for speed
    fps = [getattr(n, "_fingerprint") for n in leaves]

    best_ratio = [0.0] * len(leaves)
    best_idx = [-1] * len(leaves)

    # naive O(n²) outer loop but with a cheap Jaccard gate
    for i in range(len(leaves)):
        if show_progress:
            _print_progress(i + 1, len(leaves), prefix="duplication")
        for j in range(i + 1, len(leaves)):
            if not fps[i] or not fps[j]:
                continue

            inter = len(fps[i] & fps[j])
            union = len(fps[i] | fps[j])
            jacc = inter / union if union else 0.0
            if jacc < _JACCARD_MIN:
                continue
            # expensive difflib only on likely matches
            seq_matcher = SequenceMatcher(None, leaves[i].source, leaves[j].source)
            ratio = seq_matcher.ratio()
            if ratio > best_ratio[i]:
                best_ratio[i], best_idx[i] = ratio, j
            if ratio > best_ratio[j]:
                best_ratio[j], best_idx[j] = ratio, i

    # write the result back into metrics
    if show_progress:
        _print_progress(len(leaves), len(leaves), prefix="duplication")

    for idx, node in enumerate(leaves):
        if best_idx[idx] == -1:
            continue

        other = leaves[best_idx[idx]]
        assert node.metrics, "at this point, node.metrics has to exist"
        node.metrics.duplication = Duplication(
            score=round(best_ratio[idx], 3),
            other=other.qualname or other.name,
            lines_other=other.metrics.lines if other.metrics else 0,
        )


###################
# Data structures #
###################


@dataclass
class FileTask:
    """A unit of work: analyse a single `.py` file."""

    path: Path

    def __post_init__(self) -> None:
        if not self.path.is_file() or self.path.suffix != ".py":
            raise ValueError(f"{self.path!s} is not a Python source file")


@dataclass
class Duplication:
    score: float
    other: str
    lines_other: int


@dataclass
class Metrics:
    lines: int
    statements: int
    expressions: int
    expression_statements: int
    cyclomatic_complexity: int
    parameters: int
    duplication: Duplication | None = None


@dataclass
class CodeNode:
    """Generic tree node used for JSON serialisation."""

    name: str
    nodetype: str  # directory | file | class | function | method
    path: str  # absolute path for files & below
    qualname: str = ""
    lineno: int | None = None
    end_lineno: int | None = None
    docstring: str = ""
    metrics: Metrics | None = None
    source: str = ""
    children: list["CodeNode"] = field(default_factory=list)
    _fingerprint: set[str] = field(
        default_factory=set, repr=False, compare=False, metadata={"serialize": False}
    )

    def _clean(self, d):
        if isinstance(d, (int, float, str, type(None))):
            return d

        if isinstance(d, (tuple, list)):
            return [self._clean(i) for i in d]

        if isinstance(d, CodeNode):
            return d.to_dict()

        if isinstance(d, dict):
            return {k: self._clean(v) for k, v in d.items() if not k.startswith("_")}

        raise TypeError(f"Unknown type {type(d)}")

    def to_dict(self) -> dict[str, Any]:
        """Recursively turn the node (and children) into plain JSON-safe data."""
        raw = asdict(self)
        cleaned = self._clean(raw)
        return cleaned

    @property
    def is_directory(self) -> bool:  # noqa: D401 – simple property
        return self.nodetype == "directory"

    @property
    def has_file_children(self) -> bool:  # noqa: D401 – simple property
        return any(ch.nodetype == "file" for ch in self.children)


###############
# AST helpers #
###############


class _MetricVisitor(ast.NodeVisitor):
    """Collect basic metrics for complexity & size (no external deps)."""

    STMTS = (
        ast.If,
        ast.For,
        ast.While,
        ast.AsyncFor,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.ExceptHandler,
        ast.Match,
        ast.Assign,
        ast.AugAssign,
        ast.AnnAssign,
        ast.Raise,
        ast.Return,
    )
    DECISIONS = (
        ast.If,
        ast.For,
        ast.While,
        ast.AsyncFor,
        ast.Try,
        ast.ExceptHandler,
        ast.With,
        ast.AsyncWith,
        ast.Match,
        ast.BoolOp,
    )

    def __init__(self) -> None:
        self.stmt_count = 0
        self.expr_count = 0
        self.top_expr_stmts = 0
        self.decision_points = 0

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, self.STMTS):
            self.stmt_count += 1
        if isinstance(node, self.DECISIONS):
            self.decision_points += 1
        if isinstance(node, ast.Expr):
            self.top_expr_stmts += 1
        if isinstance(node, ast.expr):
            self.expr_count += 1
        super().generic_visit(node)

    @property
    def cyclomatic_complexity(self) -> int:  # noqa: D401 – simple property
        """McCabe's complexity number (≈ decisions + 1)."""
        return self.decision_points + 1  # +1 for function entry

    @property
    def expression_statements(self) -> int:
        return self.top_expr_stmts


#######################
# Core analysis logic #
#######################


def _analyse_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: Sequence[str],
    file_path: Path,
    kind: str = "function",
    parent: str | None = None,
) -> CodeNode:
    """Return a *leaf* `CodeNode` for a function or method."""

    visitor = _MetricVisitor()
    visitor.visit(func)

    start, end = func.lineno, func.end_lineno  # Python 3.8+
    end = end if end is not None else start + 1
    text = "".join(source_lines[start - 1 : end])
    fp = _token_fingerprint(text)
    metrics = Metrics(
        lines=end - start + 1,
        statements=visitor.stmt_count,
        expressions=visitor.expr_count,
        expression_statements=visitor.expression_statements,
        cyclomatic_complexity=visitor.cyclomatic_complexity,
        parameters=(
            len(func.args.args) + len(func.args.posonlyargs) + len(func.args.kwonlyargs)
        ),
    )

    return CodeNode(
        name=func.name,
        nodetype=kind,
        qualname=f"{parent}.{func.name}" if parent else func.name,
        path=str(file_path),
        lineno=start,
        end_lineno=end,
        docstring=ast.get_docstring(func) or "",
        source=text,
        metrics=metrics,
        _fingerprint=fp,  # leading _ => not serialized
    )


def _analyse_class(
    cls: ast.ClassDef,
    source_lines: Sequence[str],
    file_path: Path,
) -> CodeNode:
    """Return a `CodeNode` for a class plus its *method* children."""

    children: list[CodeNode] = []
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Ignore *nested* functions inside methods – they'll be included in metrics only
            children.append(
                _analyse_function(
                    item, source_lines, file_path, kind="method", parent=cls.name
                )
            )

    return CodeNode(
        name=cls.name,
        nodetype="class",
        path=str(file_path),
        lineno=cls.lineno,
        end_lineno=cls.end_lineno,
        docstring=ast.get_docstring(cls) or "",
        children=children,
    )


def analyse_file(task: FileTask) -> CodeNode:
    """Analyse a Python source file, returning its *file* `CodeNode`."""

    text = task.path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text, filename=str(task.path))
    lines = text.splitlines(keepends=True)

    children: list[CodeNode] = []
    for node in tree.body:  # Only *top-level* defs
        if isinstance(node, ast.ClassDef):
            children.append(_analyse_class(node, lines, task.path))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            children.append(_analyse_function(node, lines, task.path))
        # Nested functions are skipped as requested

    return CodeNode(
        name=task.path.name,
        nodetype="file",
        path=str(task.path),
        children=children,
    )


#########################
# Tree building helpers #
#########################


def build_tree(file_nodes: Iterable[CodeNode]) -> CodeNode:
    """Assemble a *directory* tree from analysed file nodes.

    The tree always has a synthetic top node named `root`.  Use
    :func:`prune_tree` afterwards to strip superfluous outer layers.
    """
    # for the root, just set a dummy path, it doesn't matter
    root = CodeNode(name="root", nodetype="directory", path=os.getcwd())
    lookup: MutableMapping[Path, CodeNode] = {Path(): root}

    for fnode in file_nodes:
        p = Path(fnode.path).resolve()
        parent_path = p.parent
        current_path = Path()
        parent_node = root
        for part in parent_path.parts:
            current_path /= part
            node = lookup.get(current_path)
            if node is None:
                node = CodeNode(name=part, nodetype="directory", path=str(current_path))
                lookup[current_path] = node
                parent_node.children.append(node)
            parent_node = node
        parent_node.children.append(fnode)

    return root


def prune_tree(node: CodeNode) -> CodeNode:
    """
    Collapse leading directories that contain *no* Python files.

    Starting at *node*, keep walking down while
      • the current node is a directory AND
      • it has exactly one child AND
      • that single child is a directory AND
      • that child itself has **no** direct file children
    The first directory that actually contains a .py file (or forks into
    multiple sub-dirs) becomes the new root.
    """
    current = node
    while (
        current.is_directory
        and len(current.children) == 1
        and current.children[0].is_directory
        and not current.children[0].has_file_children
    ):
        current = current.children[0]
    return current


###################
# Task collection #
###################


def gather_tasks(paths: Sequence[str]) -> list[FileTask]:
    """Expand the given paths into a list of `FileTask`s (recursively)."""

    tasks: list[FileTask] = []
    seen: set[Path] = set()

    for raw in paths:
        p = Path(raw).resolve()
        if p in seen:
            continue  # avoid duplicates
        if p.is_dir():
            for root, _, files in os.walk(p):
                root_p = Path(root)
                for fname in files:
                    if fname.endswith(".py"):
                        f = (root_p / fname).resolve()
                        if not f.is_symlink():
                            tasks.append(FileTask(f))
                            seen.add(f)
        elif p.is_file() and p.suffix == ".py":
            tasks.append(FileTask(p))
            seen.add(p)
        else:
            print(
                f"warning: {p!s} is neither a directory nor a .py file", file=sys.stderr
            )

    return tasks


#######
# CLI #
#######


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_code",
        description="Static analyser producing a JSON tree of your Python codebase.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Input file(s) or directory/ies to analyse recursively.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="result.json",
        help="Path to output JSON file (default: result.json).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the list of files that *would* be analysed and exit.",
    )
    parser.add_argument(
        "--duplication",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Compute code duplication metrics (default: enabled)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:  # noqa: N802 – main style
    args = _parse_args(argv)

    tasks = gather_tasks(args.paths)

    if args.dry_run:
        print("Planned analysis ({} file(s)):".format(len(tasks)))
        for t in tasks:
            print("  ", t.path)
        return

    print(f"Analysing {len(tasks)} files…", file=sys.stderr)
    file_nodes: list[CodeNode] = []
    for idx, t in enumerate(tasks, 1):
        file_nodes.append(analyse_file(t))
        _print_progress(idx, len(tasks), prefix="analyse")

    if args.duplication:
        compute_duplication(file_nodes, show_progress=True)
    tree = prune_tree(build_tree(file_nodes))

    output_path = Path(args.output)
    output_path.write_text(
        json.dumps(tree.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"→ JSON written to {output_path}")


if __name__ == "__main__":
    main()
