from pants.core.goals.tailor import PutativeTargets
from pants.core.goals.tailor import rules as core_tailor_rules
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner
from pants_backend_odin.goals.tailor import PutativeOdinTargetsRequest
from pants_backend_odin.goals.tailor import rules as odin_tailor_rules
from pants_backend_odin.subsystem import OdinTool
from pants_backend_odin.target_types import OdinPackageTarget, OdinSourcesGeneratorTarget


def test_find_putative_odin_targets():
    rule_runner = RuleRunner(
        rules=[
            *core_tailor_rules(),
            *odin_tailor_rules(),
            *OdinTool.rules(),
            QueryRule(PutativeTargets, [PutativeOdinTargetsRequest]),
        ],
        target_types=[OdinPackageTarget, OdinSourcesGeneratorTarget],
    )

    rule_runner.write_files(
        {
            "src/main.odin": "package main\n\nmain :: proc() {}",
            "src/lib.odin": "package main\n\nsquare :: proc(x: int) -> int { return x * x }",
            "other/test.txt": "not an odin file",
        }
    )

    pts = rule_runner.request(
        PutativeTargets,
        [
            PutativeOdinTargetsRequest(
                ("src/", "other/"),
            )
        ],
    )

    assert len(pts) == 2

    pt = list(pts)[0]

    assert pt.type_alias == "odin_sources"
    assert pt.path == "src"
    assert pt.name == "odin"
    assert sorted(pt.triggering_sources) == sorted(("main.odin", "lib.odin"))

    pt = list(pts)[1]

    assert pt.type_alias == "odin_package"
    assert pt.path == "src"
    assert pt.name == "package"
    assert pt.triggering_sources == ()  # OdinPackageTarget doesn't have triggering sources


def test_find_putative_odin_targets_empty():
    """Test that no targets are suggested when no .odin files exist."""
    rule_runner = RuleRunner(
        rules=[
            *core_tailor_rules(),
            *odin_tailor_rules(),
            *OdinTool.rules(),
            QueryRule(PutativeTargets, [PutativeOdinTargetsRequest]),
        ],
        target_types=[OdinPackageTarget, OdinSourcesGeneratorTarget],
    )

    rule_runner.write_files(
        {
            "src/main.py": "print('hello')",
            "other/test.txt": "not an odin file",
        }
    )

    pts = rule_runner.request(
        PutativeTargets,
        [
            PutativeOdinTargetsRequest(
                ("src/", "other/"),
            )
        ],
    )

    assert len(pts) == 0
