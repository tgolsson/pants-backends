import pytest
from pants.core.util_rules import external_tool
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.platform import Platform
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pants_backend_odin.subsystem import OdinTool


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *external_tool.rules(),
            QueryRule(DownloadedExternalTool, [ExternalToolRequest]),
            QueryRule(OdinTool, []),
        ]
    )


def test_odin_tool_properties(rule_runner):
    """Test that OdinTool has the expected properties."""
    odin = rule_runner.request(OdinTool, [])
    assert odin.options_scope == "odin-tool"
    assert odin.default_version == "dev-2025-07"
    assert not odin.skip  # Default should be False
    assert odin.tailor  # Default should be True


def test_odin_url_generation():
    """Test URL generation for different platforms."""
    odin = OdinTool()
    
    # Test Linux x86_64
    linux_url = odin.generate_url(Platform.linux_x86_64)
    expected = "https://github.com/odin-lang/Odin/releases/download/dev-2025-07/odin-dev-2025-07-linux-amd64.tar.gz"
    assert linux_url == expected
    
    # Test macOS arm64
    macos_url = odin.generate_url(Platform.macos_arm64)
    expected = "https://github.com/odin-lang/Odin/releases/download/dev-2025-07/odin-dev-2025-07-darwin-arm64.tar.gz"
    assert macos_url == expected


def test_odin_exe_generation():
    """Test executable path generation."""
    odin = OdinTool()
    exe = odin.generate_exe(Platform.linux_x86_64)
    assert exe == "./odin"