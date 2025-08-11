import pytest
from pants.core.util_rules import source_files
from pants.engine.addresses import Address
from pants.engine.rules import QueryRule
from pants.engine.target import InferredDependencies
from pants.testutil.rule_runner import RuleRunner
from pants_backend_odin.dependency_inference import (
    InferOdinSourceDependenciesRequest,
    parse_odin_imports,
    resolve_import_to_package_path,
)
from pants_backend_odin.dependency_inference import rules as dependency_inference_rules
from pants_backend_odin.target_types import (
    OdinBinaryTarget,
    OdinPackageTarget,
    OdinSourcesGeneratorTarget,
    OdinSourceTarget,
)


def test_parse_odin_imports():
    """Test parsing of Odin import statements."""

    # Test basic imports
    content = """package main

import "core:fmt"
import "some/local/package"
import xyz "../relative/path"
import "library:something"

main :: proc() {
    fmt.println("Hello")
}
"""

    imports = parse_odin_imports(content)

    # Should include local imports but exclude collection imports (those with colons)
    expected = ["some/local/package", "../relative/path"]
    assert imports == expected


def test_parse_odin_imports_no_imports():
    """Test parsing file with no imports."""

    content = """package main

main :: proc() {
    fmt.println("Hello")
}
"""

    imports = parse_odin_imports(content)
    assert imports == []


def test_parse_odin_imports_only_collection_imports():
    """Test parsing file with only collection imports."""

    content = """package main

import "core:fmt"
import "library:something"
import "vendor:testing"

main :: proc() {
    fmt.println("Hello")
}
"""

    imports = parse_odin_imports(content)
    assert imports == []


def test_resolve_import_to_package_path():
    """Test resolving import paths to package paths."""

    # Test relative import
    current_file = "src/main/main.odin"
    import_path = "../lib"
    result = resolve_import_to_package_path(import_path, current_file)
    assert result == "src/lib"

    # Test relative import with ./
    import_path = "./utils"
    result = resolve_import_to_package_path(import_path, current_file)
    assert result == "src/main/utils"

    # Test going up multiple levels
    import_path = "../../common/shared"
    result = resolve_import_to_package_path(import_path, current_file)
    assert result == "common/shared"

    # Test absolute-style import (treated as relative for now)
    import_path = "utils"
    result = resolve_import_to_package_path(import_path, current_file)
    assert result == "src/main/utils"


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *dependency_inference_rules(),
            *source_files.rules(),
            QueryRule(InferredDependencies, [InferOdinSourceDependenciesRequest]),
        ],
        target_types=[
            OdinSourceTarget,
            OdinSourcesGeneratorTarget,
            OdinBinaryTarget,
            OdinPackageTarget,
        ],
    )


def test_infer_odin_source_dependencies_with_imports(rule_runner):
    """Test dependency inference for Odin source files with local imports."""

    rule_runner.write_files(
        {
            "src/main/main.odin": """package main

import "../utils"
import "../../lib/math"

main :: proc() {
    utils.helper()
    math.add(1, 2)
}
""",
            "src/utils/helper.odin": """package utils

helper :: proc() {
    // Helper function
}
""",
            "lib/math/operations.odin": """package math

add :: proc(a, b: int) -> int {
    return a + b
}
""",
            "src/main/BUILD": """
odin_source(name="main", source="main.odin")

""",
            "src/utils/BUILD": """
odin_sources(name="sources")
odin_package()
""",
            "lib/math/BUILD": """
odin_sources(name="sources")
odin_package()
""",
        }
    )

    # Create targets
    rule_runner.set_options([])

    # Test inference for main.odin
    main_target = rule_runner.get_target(Address("src/main", target_name="main"))
    field_set = InferOdinSourceDependenciesRequest.infer_from.create(main_target)
    inferred_deps = rule_runner.request(InferredDependencies, [InferOdinSourceDependenciesRequest(field_set)])

    # Should find dependencies based on the imports
    # Note: the exact addresses depend on how path resolution works
    assert len(inferred_deps.include) > 0


def test_infer_odin_source_dependencies_no_imports(rule_runner):
    """Test dependency inference for Odin source files without imports."""

    rule_runner.write_files(
        {
            "src/main.odin": """package main

main :: proc() {
    // No imports
}
""",
            "src/BUILD": """
odin_source(name="main", source="main.odin")
""",
        }
    )
    main_target = rule_runner.get_target(Address("src", target_name="main"))
    # Test inference
    field_set = InferOdinSourceDependenciesRequest.infer_from.create(main_target)
    inferred_deps = rule_runner.request(InferredDependencies, [InferOdinSourceDependenciesRequest(field_set)])

    assert len(inferred_deps.include) == 0


def test_infer_odin_source_dependencies_collection_imports_only(rule_runner):
    """Test dependency inference for Odin source files with only collection imports."""

    rule_runner.write_files(
        {
            "src/main.odin": """package main

import "core:fmt"
import "library:testing"

main :: proc() {
    fmt.println("Hello")
}
""",
            "src/BUILD": """
odin_source(name="main", source="main.odin")
""",
        }
    )

    # Create target
    rule_runner.set_options([])
    main_target = rule_runner.get_target(Address("src", target_name="main"))
    # Test inference
    field_set = InferOdinSourceDependenciesRequest.infer_from.create(main_target)
    inferred_deps = rule_runner.request(InferredDependencies, [InferOdinSourceDependenciesRequest(field_set)])

    # Should have no dependencies since collection imports are ignored
    assert len(inferred_deps.include) == 0
