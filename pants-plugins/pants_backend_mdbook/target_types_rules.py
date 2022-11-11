"""

"""
from pants.engine.rules import UnionRule, collect_rules, rule
from pants.engine.target import TargetFilesGeneratorSettings, TargetFilesGeneratorSettingsRequest


def rules():
    return [
        *collect_rules(),
    ]
