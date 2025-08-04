from pants.core.goals.tailor import PutativeTargets, PutativeTargetsRequest
from pants.core.goals.tailor import rules as core_tailor_rules
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_odin.goals.tailor import PutativeOdinTargetsRequest
from pants_backend_odin.goals.tailor import rules as odin_tailor_rules
from pants_backend_odin.target_types import OdinSourcesGeneratorTarget


def test_find_putative_odin_targets():
    rule_runner = RuleRunner(
        rules=[
            *core_tailor_rules(),
            *odin_tailor_rules(),
            QueryRule(PutativeTargets, [PutativeOdinTargetsRequest]),
        ],
        target_types=[OdinSourcesGeneratorTarget],
    )
    
    rule_runner.write_files({
        "src/main.odin": "package main\n\nmain :: proc() {}",
        "src/lib.odin": "package lib\n\nsquare :: proc(x: int) -> int { return x * x }",
        "other/test.txt": "not an odin file",
    })
    
    pts = rule_runner.request(
        PutativeTargets,
        [
            PutativeOdinTargetsRequest(
                ("src/*.odin", "other/*.odin"),
                description_of_origin="tests",
            )
        ],
    )
    
    assert len(pts.putative_targets) == 1
    pt = pts.putative_targets[0]
    assert pt.target_type == OdinSourcesGeneratorTarget
    assert pt.path == "src"
    assert pt.name == "odin"
    assert set(pt.triggering_sources) == {"src/main.odin", "src/lib.odin"}


def test_find_putative_odin_targets_empty():
    """Test that no targets are suggested when no .odin files exist."""
    rule_runner = RuleRunner(
        rules=[
            *core_tailor_rules(),
            *odin_tailor_rules(),
            QueryRule(PutativeTargets, [PutativeOdinTargetsRequest]),
        ],
        target_types=[OdinSourcesGeneratorTarget],
    )
    
    rule_runner.write_files({
        "src/main.py": "print('hello')",
        "other/test.txt": "not an odin file",
    })
    
    pts = rule_runner.request(
        PutativeTargets,
        [
            PutativeOdinTargetsRequest(
                ("src/*.odin", "other/*.odin"),
                description_of_origin="tests",
            )
        ],
    )
    
    assert len(pts.putative_targets) == 0