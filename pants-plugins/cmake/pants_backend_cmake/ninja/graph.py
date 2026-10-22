"""Discovery on a parsed Ninja graph: CMake targets, their final outputs, and the edges one
target needs, bounded at other addressable targets' outputs."""

from __future__ import annotations

from dataclasses import dataclass

from pants.util.frozendict import FrozenDict

from pants_backend_cmake.ninja.parser import NinjaEdge, NinjaGraph
from pants_backend_cmake.util_rules.probes import PROBE_PREFIX

ADDRESSABLE_KINDS = frozenset({"STATIC_LIBRARY", "SHARED_LIBRARY", "MODULE_LIBRARY", "EXECUTABLE"})
_SYMLINK_RULES = frozenset({"CMAKE_SYMLINK_LIBRARY", "CMAKE_SYMLINK_EXECUTABLE"})


class UnsupportedGraphError(Exception):
    pass


def is_link_edge(edge: NinjaEdge) -> bool:
    """The edge that writes a target's artefact: CMake names the rules `<LANG>_<KIND>_LINKER__...`;
    versioned libraries add a symlink edge next to it."""
    return "_LINKER__" in edge.rule or edge.rule in _SYMLINK_RULES


@dataclass(frozen=True)
class CMakeTargetInfo:
    name: str
    kind: str
    final_outputs: tuple[str, ...]

    @property
    def addressable(self) -> bool:
        """Libraries and executables, except the consumer probes."""
        return self.kind in ADDRESSABLE_KINDS and not self.name.startswith(PROBE_PREFIX)


def target_edges(graph: NinjaGraph, name: str) -> list[NinjaEdge]:
    return [edge for edge in graph.edges if edge.target == name]


def discover_targets(graph: NinjaGraph) -> FrozenDict[str, CMakeTargetInfo]:
    """Every CMake target the generator wrote a section for. A library's or executable's final
    outputs are what its link and symlink edges write (`libfoo.so.1.2`, `libfoo.so.1`,
    `libfoo.so`); for other kinds, the outputs of its command edges that none of its own edges
    consume."""
    by_target: dict[str, list[NinjaEdge]] = {}
    for edge in graph.edges:
        if edge.target is not None:
            by_target.setdefault(edge.target, []).append(edge)

    targets = {}
    for name, edges in by_target.items():
        link_outputs = [out for edge in edges if is_link_edge(edge) for out in edge.all_outputs]
        if link_outputs:
            final = tuple(link_outputs)
        else:
            consumed: set[str] = set()
            for edge in edges:
                consumed.update(edge.inputs, edge.implicit_inputs, edge.order_only)
            produced = [out for edge in edges if not edge.is_phony for out in edge.all_outputs]
            final = tuple(out for out in produced if out not in consumed)
        targets[name] = CMakeTargetInfo(
            name=name, kind=graph.target_kinds.get(name, "UNKNOWN"), final_outputs=final
        )
    return FrozenDict(targets)


def boundary_outputs(targets: FrozenDict[str, CMakeTargetInfo], name: str) -> dict[str, str]:
    """Final output path to owning target, for every addressable target other than `name`."""
    boundary: dict[str, str] = {}
    for other in targets.values():
        if not other.addressable or other.name == name:
            continue
        for out in other.final_outputs:
            if out in boundary:
                raise UnsupportedGraphError(
                    f"`{out}` is a final output of both {boundary[out]} and {other.name}"
                )
            boundary[out] = other.name
    return boundary


@dataclass(frozen=True)
class BuildPlan:
    """The edges a target needs, grouped into waves by longest path from a leaf."""

    target: str
    waves: tuple[tuple[NinjaEdge, ...], ...]
    dependencies: tuple[str, ...]
    """Other addressable targets whose final outputs this plan consumes."""
    sources: tuple[str, ...]
    """Leaf inputs: paths no edge produces."""
    outputs: tuple[str, ...]

    @property
    def edges(self) -> tuple[NinjaEdge, ...]:
        return tuple(edge for wave in self.waves for edge in wave)


def plan_target(graph: NinjaGraph, name: str, targets: FrozenDict[str, CMakeTargetInfo]) -> BuildPlan:
    """Walk producers from the target's final outputs through explicit and implicit inputs.
    Stop at another addressable target's final output (a dependency) or at a path nothing
    produces (a source). Order-only inputs are not followed: they sequence a Ninja build but a
    replayed command only reads its declared inputs. A generated file that is reachable only
    through an order-only input (an `add_custom_command` output a source includes) would be
    missing from the replay, so that is an error rather than a silent miss."""
    info = targets[name]
    if not info.final_outputs:
        raise UnsupportedGraphError(f"CMake target `{name}` ({info.kind}) has no outputs to build")
    boundary = boundary_outputs(targets, name)
    producers = graph.producers

    edges: dict[NinjaEdge, None] = {}
    dependencies: dict[str, None] = {}
    sources: dict[str, None] = {}
    seen: set[str] = set()
    stack = list(info.final_outputs)
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        if path in boundary:
            dependencies[boundary[path]] = None
            continue
        edge = producers.get(path)
        if edge is None:
            sources[path] = None
            continue
        if edge in edges:
            continue
        edges[edge] = None
        stack.extend(edge.dependencies)

    generated = _generated_via_order_only(graph, edges, boundary)
    if generated:
        raise UnsupportedGraphError(
            f"CMake target `{name}` depends on generated files only through order-only inputs, "
            f"which the replay does not follow: {sorted(generated)}. Check the generated files "
            "in, or list them as sources of the consuming target."
        )

    levels: dict[str, int] = {}

    def level_of(path: str) -> int:
        if path in levels:
            return levels[path]
        edge = producers.get(path)
        if edge is None or path in boundary or edge not in edges:
            level = 0
        else:
            below = max((level_of(p) for p in edge.dependencies), default=0)
            level = below if edge.is_phony else below + 1
        levels[path] = level
        return level

    by_level: dict[int, list[NinjaEdge]] = {}
    for edge in edges:
        if edge.is_phony:
            continue
        by_level.setdefault(level_of(edge.all_outputs[0]), []).append(edge)

    return BuildPlan(
        target=name,
        waves=tuple(tuple(by_level[level]) for level in sorted(by_level)),
        dependencies=tuple(dependencies),
        sources=tuple(sources),
        outputs=info.final_outputs,
    )


def _generated_via_order_only(
    graph: NinjaGraph, edges: dict[NinjaEdge, None], boundary: dict[str, str]
) -> set[str]:
    """Custom-command outputs the plan's edges reach only through order-only inputs."""
    producers = graph.producers
    missing: set[str] = set()
    seen: set[str] = set()
    stack = [p for edge in edges for p in edge.order_only]
    while stack:
        path = stack.pop()
        if path in seen or path in boundary:
            continue
        seen.add(path)
        producer = producers.get(path)
        if producer is None:
            continue
        if producer.is_phony:
            stack.extend(producer.dependencies)
            stack.extend(producer.order_only)
        elif producer.rule == "CUSTOM_COMMAND" and producer not in edges:
            missing.add(path)
    return missing
