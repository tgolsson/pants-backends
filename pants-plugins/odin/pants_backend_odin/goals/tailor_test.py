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

    assert len(pts) == 1
    pt = list(pts)[0]

    print(pt)
    assert pt.type_alias == "odin_package"
    assert pt.path == "src"
    assert pt.name == "odin"
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
