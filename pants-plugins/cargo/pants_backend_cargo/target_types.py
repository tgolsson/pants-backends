from __future__ import annotations

from pants.core.environments.target_types import EnvironmentField
from pants.core.goals.package import OutputPathField
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    BoolField,
    Dependencies,
    MultipleSourcesField,
    StringField,
    StringSequenceField,
    Target,
    TargetGenerator,
    generate_multiple_sources_field_help_message,
)
from pants.util.strutil import help_text

from pants_backend_platforms.target_types import PlatformsField

# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class CargoWorkspaceSourcesField(MultipleSourcesField):
    default = ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", ".cargo/config.toml")
    expected_file_extensions = (".toml", ".lock")
    help = generate_multiple_sources_field_help_message(
        "Example: `sources=['Cargo.toml', 'Cargo.lock', '.cargo/config.toml']`"
    )


class CargoWorkspaceTarget(Target):
    alias = "cargo_workspace"
    core_fields = (*COMMON_TARGET_FIELDS, CargoWorkspaceSourcesField)
    help = help_text("""
        The root of a Cargo workspace: its `Cargo.toml`, `Cargo.lock` and the
        `rust-toolchain.toml` and `.cargo/config.toml` cargo reads from there. The member
        packages are found through the manifest's `members` and depend on it. Test
        executables and clippy run once per workspace.
        """)


# ---------------------------------------------------------------------------
# Package
# ---------------------------------------------------------------------------


class CargoPackageSourcesField(MultipleSourcesField):
    default = (
        "Cargo.toml",
        "Cargo.lock",
        "build.rs",
        "src/**/*",
        "tests/**/*.rs",
        "examples/**/*",
        "benches/**/*.rs",
    )
    expected_file_extensions = (".rs", ".toml", ".lock")
    help = generate_multiple_sources_field_help_message(
        "Example: `sources=['Cargo.toml', 'src/lib.rs', 'build.rs', '!test_ignore.rs']`"
    )


class CargoPackageDependenciesField(Dependencies):
    help = help_text("""
        Besides the inferred cargo dependencies: `files` and `resources` targets the build
        reads (`include_str!`, build-script inputs), and packageable targets whose
        `package` output the build needs. Those are built and staged in the sandbox at their
        package path (for build, test, run and clippy; not for fmt and generate-lockfiles),
        but their own dependencies are not; point cargo or a build script at them through
        `env`.
        """)


class SkipCargoTestsField(BoolField):
    alias = "skip_tests"
    default = False
    help = "If true, don't run this package's tests."


class CargoEnvField(StringSequenceField):
    alias = "env"
    help = help_text("""
        `KEY=VALUE` entries set in the environment of every cargo process (build, test,
        clippy, fmt) whose sandbox holds this package: its own, those of packages depending on
        it, and those of its workspace; and of the test executables and `run` binaries those
        build. `{chroot}` expands to the sandbox root, so a staged dependency
        can be named: `env=["MYLIB_DIR={chroot}/build"]`. `PATH`, `CARGO_HOME`,
        `RUSTUP_HOME`, `RUSTUP_TOOLCHAIN`, `RUSTC_WRAPPER`, `RUSTFLAGS`, `KACHE_*` and the C toolchain's
        `CARGO_TARGET_<TRIPLE>_LINKER`, `CC_<triple>`, `CXX_<triple>` and `AR_<triple>` are
        set by the backend and may not be set here.
        """)


class CargoPackageNameField(StringField):
    alias = "package_name"
    help = "The package's name from `Cargo.toml`."


class CargoArtifactKindField(StringField):
    alias = "kind"
    valid_choices = ("lib", "bin", "test")
    help = "What cargo builds this target as: the library, a binary, or an integration test."


class CargoArtifactNameField(StringField):
    alias = "artifact_name"
    help = "The cargo target's name (`[[bin]] name`, `[[test]] name`, or the library's)."


class _CargoPackageMarker(StringField):
    alias = "_package_tag"
    help = "Marker for the package target."


_SHARED_FIELDS = (
    *COMMON_TARGET_FIELDS,
    SkipCargoTestsField,
    OutputPathField,
    EnvironmentField,
    CargoEnvField,
    PlatformsField,
)


class CargoPackageTarget(TargetGenerator):
    alias = "cargo_package"
    core_fields = (*_SHARED_FIELDS, CargoPackageDependenciesField, CargoPackageSourcesField)
    copied_fields = _SHARED_FIELDS
    moved_fields = (CargoPackageDependenciesField,)
    help = help_text("""
        A Cargo package. `cargo metadata` on its `Cargo.toml` generates one
        `cargo_package_impl` holding the sources, a `cargo_library` if it has one, one
        `cargo_binary` per `[[bin]]` and one `cargo_test` per `[[test]]`, all addressable as
        `path:name#<generated name>`.
        """)


class CargoPackageTargetImpl(Target):
    alias = "cargo_package_impl"
    core_fields = (
        *_SHARED_FIELDS,
        CargoPackageDependenciesField,
        CargoPackageSourcesField,
        CargoPackageNameField,
        _CargoPackageMarker,
    )
    help = "The generated package itself: its sources and cargo dependencies. Formatted and linted."


_ARTIFACT_FIELDS = (
    *_SHARED_FIELDS,
    CargoPackageDependenciesField,
    CargoPackageNameField,
    CargoArtifactKindField,
    CargoArtifactNameField,
)


class CargoLibraryTarget(Target):
    alias = "cargo_library"
    core_fields = _ARTIFACT_FIELDS
    help = "The package's library. `test` runs its unit tests."


class CargoBinaryTarget(Target):
    alias = "cargo_binary"
    core_fields = _ARTIFACT_FIELDS
    help = "One `[[bin]]` of the package. `package` and `run` build it; `test` runs its unit tests."


class CargoTestTarget(Target):
    alias = "cargo_test"
    core_fields = _ARTIFACT_FIELDS
    help = "One `[[test]]` of the package, run by `test`."


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


class RustTargetField(StringField):
    alias = "rust_target"
    help = "The Rust target triple cargo builds this platform as. Unset, derived from `os` and `arch`."


def target_types():
    return [
        CargoWorkspaceTarget,
        CargoPackageTarget,
        CargoPackageTargetImpl,
        CargoLibraryTarget,
        CargoBinaryTarget,
        CargoTestTarget,
    ]
