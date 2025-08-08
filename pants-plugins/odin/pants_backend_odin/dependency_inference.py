from __future__ import annotations

import re
from pathlib import Path

from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest, DigestContents
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import AllTargets, InferDependenciesRequest, InferredDependencies
from pants.engine.unions import UnionRule
from pants_backend_odin.target_types import InferOdinPackageDependenciesRequest, InferOdinSourceDependenciesRequest, OdinSourceField


def parse_odin_imports(file_content: str) -> list[str]:
    """
    Parse Odin import statements and return local package imports.
    
    Handles imports like:
    - import "relative/path"
    - import name "relative/path"  
    - import xyz "../library/foobar"
    
    Ignores collection imports like:
    - import "core:fmt"
    - import "library:something"
    """
    imports = []
    
    # Match import statements with optional name and quoted path
    # Matches: import ["name"] "path"
    import_pattern = r'import\s+(?:[a-zA-Z_][a-zA-Z0-9_]*\s+)?"([^"]+)"'
    
    for match in re.finditer(import_pattern, file_content):
        import_path = match.group(1)
        
        # Skip collection imports (contain colon)
        if ":" in import_path:
            continue
            
        imports.append(import_path)
    
    return imports


def resolve_import_to_package_path(import_path: str, current_file_path: str) -> str:
    """
    Resolve an import path to a package directory path relative to the project root.
    
    Args:
        import_path: The import path from the import statement (e.g., "../lib", "utils")
        current_file_path: The path of the file containing the import
        
    Returns:
        The resolved package directory path relative to project root
    """
    current_dir = Path(current_file_path).parent
    
    if import_path.startswith("../") or import_path.startswith("./"):
        # Relative import
        resolved_path = current_dir / import_path
    else:
        # Absolute import - treat as relative to current directory for now
        resolved_path = current_dir / import_path
    
    # Normalize the path by resolving .. and . components
    # but keep it as a relative path
    parts = []
    for part in resolved_path.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    
    return "/".join(parts) if parts else ""


@rule
async def infer_odin_source_dependencies(
    request: InferOdinSourceDependenciesRequest, all_targets: AllTargets
) -> InferredDependencies:
    """Infer dependencies for odin_source targets based on import statements."""
    
    # Get the content of the source file
    source_files = await Get(SourceFiles, SourceFilesRequest([request.field_set.source]))
    
    if not source_files.snapshot.files:
        return InferredDependencies([])
    
    digest_contents = await Get(DigestContents, Digest, source_files.snapshot.digest)
    
    if not digest_contents:
        return InferredDependencies([])
    
    file_content = digest_contents[0].content.decode("utf-8")
    
    # Parse imports from the file
    imports = parse_odin_imports(file_content)
    
    if not imports:
        return InferredDependencies([])
    
    # Resolve imports to package paths and find corresponding targets
    inferred_deps = []
    # Use the full address path, not just the source filename
    current_file_path = f"{request.field_set.address.spec_path}/{request.field_set.source.value}"
    
    for import_path in imports:
        package_path = resolve_import_to_package_path(import_path, current_file_path)
        if not package_path:
            continue
            
        # Find targets in the resolved package path
        for target in all_targets:
            if (
                target.has_field(OdinSourceField)
                and target.address.spec_path == package_path
                and target.address != request.field_set.address
            ):
                inferred_deps.append(target.address)
    
    return InferredDependencies(inferred_deps)


@rule
async def infer_odin_package_dependencies(
    request: InferOdinPackageDependenciesRequest, all_targets: AllTargets
) -> InferredDependencies:
    """Infer that odin_package targets depend on all odin_source targets in the same directory."""
    odin_package_address = request.field_set.address
    odin_package_spec_path = odin_package_address.spec_path

    # Find all odin_source targets in the same directory
    inferred_deps = []
    for target in all_targets:
        if (
            target.has_field(OdinSourceField)
            and target.address.spec_path == odin_package_spec_path
            and target.address != odin_package_address
        ):
            inferred_deps.append(target.address)

    return InferredDependencies(inferred_deps)


def rules():
    return [
        *collect_rules(),
        UnionRule(InferDependenciesRequest, InferOdinPackageDependenciesRequest),
        UnionRule(InferDependenciesRequest, InferOdinSourceDependenciesRequest),
    ]
