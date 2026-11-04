"""A narrow parser for the `build.ninja` CMake's Ninja generator writes.

Handles `rule` and `build` blocks, global and edge-scoped bindings, `|` and `||` inputs,
`include`, `pool`, `default`, line continuations and the `$` escapes. Rule bindings are kept
unexpanded and expanded per edge (edge scope, then rule, then global, with `$in`/`$out`), the
way Ninja does. `subninja`, `dyndep` and validation inputs (`|@`) are not supported.

The generator's section comments (`# Object build statements for <TYPE> target <name>`,
`# Link build statements for ...`, `# Utility command for <name>`) are the only machine-readable
record of which CMake target an edge belongs to and of the target's type, so the parser reads
them and tags every edge with the target in whose section it appears. Edges outside a target's
sections (custom commands, aliases, built-ins) carry no target.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property

from pants.util.frozendict import FrozenDict


class NinjaParseError(Exception):
    pass


_VAR_RE = re.compile(r"\$(?:(\$)|(:)|( )|\{([A-Za-z0-9_.-]+)\}|([A-Za-z0-9_-]+))")
_BINDING_RE = re.compile(r"^([A-Za-z0-9_.-]+)[ \t]*=[ \t]*(.*)$")
_KEYWORD_RE = re.compile(r"^([A-Za-z_]+)(?:[ \t]+(.*))?$")
_TARGET_SECTION_RE = re.compile(r"^# (?:Object|Link) build statements for ([A-Z_]+) target (\S+)$")
_UTILITY_SECTION_RE = re.compile(r"^# Utility command for (\S+)$")
_OBJECT_LIBRARY_SECTION_RE = re.compile(r"^# Object library (\S+)$")
_SECTION_END_RE = re.compile(
    r"^# (Custom command for|Phony custom command for|Target aliases|Folder targets|"
    r"Built-in targets|Unknown Build Time Dependencies)"
)

# Bindings Ninja evaluates without shell escaping of `$in`/`$out`: they name files.
_FILE_NAME_BINDINGS = frozenset({"depfile", "rspfile", "dyndep"})

_SHELL_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-./")


def shell_escape(path: str) -> str:
    """Quote a path the way Ninja does for `$in`/`$out` (only when something needs it)."""
    if all(c in _SHELL_SAFE for c in path):
        return path
    return "'" + path.replace("'", "'\\''") + "'"


def expand(text: str, lookup) -> str:
    """Expand `$var`, `${var}` and the `$` escapes in `text`, resolving names through `lookup`."""

    def sub(m: re.Match) -> str:
        if m.group(1):
            return "$"
        if m.group(2):
            return ":"
        if m.group(3):
            return " "
        return lookup(m.group(4) or m.group(5))

    return _VAR_RE.sub(sub, text)


@dataclass(frozen=True)
class NinjaRule:
    name: str
    bindings: FrozenDict[str, str]


@dataclass(frozen=True)
class NinjaEdge:
    rule: str
    outputs: tuple[str, ...]
    implicit_outputs: tuple[str, ...]
    inputs: tuple[str, ...]
    implicit_inputs: tuple[str, ...]
    order_only: tuple[str, ...]
    bindings: FrozenDict[str, str]
    target: str | None

    @property
    def is_phony(self) -> bool:
        return self.rule == "phony"

    @property
    def all_outputs(self) -> tuple[str, ...]:
        return self.outputs + self.implicit_outputs

    @property
    def dependencies(self) -> tuple[str, ...]:
        """Explicit and implicit inputs: what the command actually reads."""
        return self.inputs + self.implicit_inputs


@dataclass(frozen=True)
class NinjaGraph:
    rules: FrozenDict[str, NinjaRule]
    edges: tuple[NinjaEdge, ...]
    bindings: FrozenDict[str, str]
    target_kinds: FrozenDict[str, str]

    @cached_property
    def producers(self) -> FrozenDict[str, NinjaEdge]:
        """Output path to the edge that writes it."""
        return FrozenDict({out: edge for edge in self.edges for out in edge.all_outputs})

    def lookup(
        self,
        edge: NinjaEdge,
        name: str,
        *,
        shell: bool = True,
        overrides: Mapping[str, str] | None = None,
        _active: frozenset[str] = frozenset(),
    ) -> str:
        """Resolve a variable in the scope of `edge`: `$in`/`$out`, then `overrides`, edge,
        rule, global. `shell=False` for bindings that name files. `overrides` pin names for
        one expansion (for example a launcher variable to empty)."""
        if name == "in":
            return " ".join(shell_escape(p) if shell else p for p in edge.inputs)
        if name == "in_newline":
            return "\n".join(shell_escape(p) if shell else p for p in edge.inputs)
        if name == "out":
            return " ".join(shell_escape(p) if shell else p for p in edge.outputs)
        if overrides and name in overrides:
            return overrides[name]
        if name in edge.bindings:
            return edge.bindings[name]
        rule = self.rules.get(edge.rule)
        if rule is not None and name in rule.bindings:
            if name in _active:
                raise NinjaParseError(f"cycle in rule `{edge.rule}` variable `{name}`")
            nested_shell = shell and name not in _FILE_NAME_BINDINGS
            return expand(
                rule.bindings[name],
                lambda n: self.lookup(
                    edge, n, shell=nested_shell, overrides=overrides, _active=_active | {name}
                ),
            )
        return self.bindings.get(name, "")

    def command(self, edge: NinjaEdge, overrides: Mapping[str, str] | None = None) -> str:
        return self.lookup(edge, "command", overrides=overrides)


@dataclass
class _Parser:
    files: Mapping[str, str]
    rules: dict[str, NinjaRule] = field(default_factory=dict)
    edges: list[NinjaEdge] = field(default_factory=list)
    bindings: dict[str, str] = field(default_factory=dict)
    target_kinds: dict[str, str] = field(default_factory=dict)
    current_target: str | None = None
    parsing: list[str] = field(default_factory=list)

    def parse(self, path: str) -> None:
        if path in self.parsing:
            raise NinjaParseError(f"include cycle: {' -> '.join([*self.parsing, path])}")
        if path not in self.files:
            raise NinjaParseError(f"included file `{path}` was not provided")
        self.parsing.append(path)
        for lineno, head, body in _blocks(self.files[path]):
            if head.startswith("#"):
                self._comment(head)
                continue
            m = _KEYWORD_RE.match(head)
            keyword, rest = (m.group(1), m.group(2) or "") if m else ("", "")
            if keyword == "rule":
                self._rule(rest.strip(), body, path, lineno)
            elif keyword == "build":
                self._build(rest, body, path, lineno)
            elif keyword == "include":
                self.parse(self._expand_global(rest.strip()))
            elif keyword in ("default", "pool"):
                continue
            elif keyword == "subninja":
                raise NinjaParseError(f"{path}:{lineno}: subninja is not supported")
            else:
                m = _BINDING_RE.match(head)
                if not m:
                    raise NinjaParseError(f"{path}:{lineno}: cannot parse `{head}`")
                self.bindings[m.group(1)] = self._expand_global(m.group(2))
        self.parsing.pop()

    def _comment(self, line: str) -> None:
        m = _TARGET_SECTION_RE.match(line)
        if m:
            self.target_kinds[m.group(2)] = m.group(1)
            self.current_target = m.group(2)
            return
        m = _UTILITY_SECTION_RE.match(line)
        if m:
            self.target_kinds.setdefault(m.group(1), "UTILITY")
            self.current_target = m.group(1)
            return
        m = _OBJECT_LIBRARY_SECTION_RE.match(line)
        if m:
            self.current_target = m.group(1)
            return
        if _SECTION_END_RE.match(line):
            self.current_target = None

    def _expand_global(self, text: str) -> str:
        return expand(text, lambda n: self.bindings.get(n, ""))

    def _rule(self, name: str, body: list[str], path: str, lineno: int) -> None:
        if not name:
            raise NinjaParseError(f"{path}:{lineno}: rule without a name")
        bindings = {}
        for line in body:
            m = _BINDING_RE.match(line)
            if not m:
                raise NinjaParseError(f"{path}:{lineno}: cannot parse rule binding `{line}` in rule {name}")
            bindings[m.group(1)] = m.group(2)
        self.rules[name] = NinjaRule(name, FrozenDict(bindings))

    def _build(self, rest: str, body: list[str], path: str, lineno: int) -> None:
        colon = _find_unescaped(rest, ":")
        if colon < 0:
            raise NinjaParseError(f"{path}:{lineno}: build line without `:`")
        outputs, implicit_outputs = _split_outputs(_tokens(rest[:colon]), path, lineno)
        right = _tokens(rest[colon + 1 :])
        if not right:
            raise NinjaParseError(f"{path}:{lineno}: build line without a rule")
        rule, *rest_tokens = right
        inputs, implicit_inputs, order_only = _split_inputs(rest_tokens, path, lineno)
        bindings = {}
        for line in body:
            m = _BINDING_RE.match(line)
            if not m:
                raise NinjaParseError(f"{path}:{lineno}: cannot parse edge binding `{line}`")
            bindings[m.group(1)] = self._expand_global(m.group(2))
        self.edges.append(
            NinjaEdge(
                rule=rule,
                outputs=tuple(self._expand_global(t) for t in outputs),
                implicit_outputs=tuple(self._expand_global(t) for t in implicit_outputs),
                inputs=tuple(self._expand_global(t) for t in inputs),
                implicit_inputs=tuple(self._expand_global(t) for t in implicit_inputs),
                order_only=tuple(self._expand_global(t) for t in order_only),
                bindings=FrozenDict(bindings),
                target=self.current_target,
            )
        )


def _trailing_dollars(s: str) -> int:
    n = 0
    while n < len(s) and s[-1 - n] == "$":
        n += 1
    return n


def _rstrip_unescaped(s: str) -> str:
    """Strip trailing whitespace, keeping a final escaped space (`$ `)."""
    stripped = s.rstrip(" \t")
    if stripped != s and _trailing_dollars(stripped) % 2 == 1:
        return stripped + " "
    return stripped


def _blocks(text: str) -> list[tuple[int, str, list[str]]]:
    """Join continuations, then group each top-level line with its indented bindings."""
    logical: list[tuple[int, str]] = []
    pending: list[str] = []
    pending_start = 0
    for lineno, raw in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        if not pending:
            pending_start = lineno
        is_comment = not pending and raw.lstrip(" \t").startswith("#")
        continued = not is_comment and _trailing_dollars(raw) % 2 == 1
        piece = raw[:-1] if continued else raw
        if pending:
            piece = piece.lstrip(" \t")
        pending.append(piece)
        if continued:
            continue
        logical.append((pending_start, "".join(pending)))
        pending = []
    if pending:
        logical.append((pending_start, "".join(pending)))

    blocks: list[tuple[int, str, list[str]]] = []
    for lineno, line in logical:
        if not line.strip(" \t"):
            continue
        if line[0] in " \t":
            stripped = line.lstrip(" \t")
            if stripped.startswith("#"):
                continue
            if not blocks or blocks[-1][1].startswith("#"):
                raise NinjaParseError(f"line {lineno}: indented line outside a block")
            blocks[-1][2].append(_rstrip_unescaped(stripped))
        else:
            blocks.append((lineno, _rstrip_unescaped(line), []))
    return blocks


def _find_unescaped(s: str, ch: str) -> int:
    i = 0
    while i < len(s):
        if s[i] == "$":
            i += 2
            continue
        if s[i] == ch:
            return i
        i += 1
    return -1


def _tokens(s: str) -> list[str]:
    """Split on unescaped whitespace, keeping `$` escapes intact for later expansion."""
    tokens: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "$" and i + 1 < len(s):
            cur.append(s[i : i + 2])
            i += 2
            continue
        if c in " \t":
            if cur:
                tokens.append("".join(cur))
                cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    if cur:
        tokens.append("".join(cur))
    return tokens


def _split_outputs(tokens: list[str], path: str, lineno: int) -> tuple[list[str], list[str]]:
    if "||" in tokens or "|@" in tokens:
        raise NinjaParseError(f"{path}:{lineno}: unsupported token in outputs")
    if "|" in tokens:
        i = tokens.index("|")
        return tokens[:i], tokens[i + 1 :]
    return tokens, []


def _split_inputs(tokens: list[str], path: str, lineno: int) -> tuple[list[str], list[str], list[str]]:
    explicit: list[str] = []
    implicit: list[str] = []
    order_only: list[str] = []
    current = explicit
    for t in tokens:
        if t == "|":
            if current is not explicit:
                raise NinjaParseError(f"{path}:{lineno}: `|` after `||`")
            current = implicit
        elif t == "||":
            current = order_only
        elif t == "|@":
            raise NinjaParseError(f"{path}:{lineno}: validation inputs (`|@`) are not supported")
        else:
            current.append(t)
    return explicit, implicit, order_only


def parse_ninja(files: Mapping[str, str], root: str = "build.ninja") -> NinjaGraph:
    """Parse `root` from `files` (paths relative to the build directory) into a `NinjaGraph`."""
    parser = _Parser(files)
    parser.parse(root)
    seen: dict[str, str] = {}
    for edge in parser.edges:
        if not edge.is_phony and edge.rule not in parser.rules:
            raise NinjaParseError(f"edge for {edge.outputs} uses unknown rule `{edge.rule}`")
        for out in edge.all_outputs:
            if out in seen:
                raise NinjaParseError(f"multiple edges produce `{out}` (rules {seen[out]} and {edge.rule})")
            seen[out] = edge.rule
    return NinjaGraph(
        rules=FrozenDict(parser.rules),
        edges=tuple(parser.edges),
        bindings=FrozenDict(parser.bindings),
        target_kinds=FrozenDict(parser.target_kinds),
    )
