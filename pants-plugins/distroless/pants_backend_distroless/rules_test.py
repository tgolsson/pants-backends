from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from pants.core.goals.generate_lockfiles import GenerateLockfileResult, UserGenerateLockfiles
from pants.core.target_types import FileSourceField
from pants.core.target_types import rules as core_target_type_rules
from pants.core.util_rules import system_binaries
from pants.engine import process
from pants.engine.addresses import Address, Addresses
from pants.engine.fs import Digest, DigestContents
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    HydratedSources,
    HydrateSourcesRequest,
    SourcesField,
)
from pants.testutil.rule_runner import QueryRule, RuleRunner, engine_error

from pants_backend_distroless import lockfile, sandboxed, subsystem, target_generation
from pants_backend_distroless.apt_resolver import LOCKFILE_VERSION, normalize_inputs
from pants_backend_distroless.apt_resolver_test import stanza, write_repo
from pants_backend_distroless.openpgp_test import fixture
from pants_backend_distroless.target_generation import DebPackagesLockfileError
from pants_backend_distroless.target_types import (
    DebPackageArchitectureField,
    DebPackageGroupTarget,
    DebPackagePackageField,
    DebPackagesTargetGenerator,
    DebPackageTarget,
)


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        target_types=[
            DebPackagesTargetGenerator,
            DebPackageTarget,
            DebPackageGroupTarget,
        ],
        rules=[
            *lockfile.rules(),
            *subsystem.rules(),
            *target_generation.rules(),
            *sandboxed.rules(),
            *system_binaries.rules(),
            *process.rules(),
            *core_target_type_rules(),
            QueryRule(UserGenerateLockfiles, [lockfile.RequestedDebPackagesUserResolveNames]),
            QueryRule(GenerateLockfileResult, [lockfile.GenerateDebPackagesLockfile]),
            QueryRule(DigestContents, [Digest]),
            QueryRule(Addresses, [DependenciesRequest]),
            QueryRule(HydratedSources, [HydrateSourcesRequest]),
        ],
    )


BUILD = """
deb_packages(
    name="bookworm",
    resolve="bookworm",
    sources=["{uri} stable main"],
    architectures=["amd64", "arm64"],
    packages=["jq"],
    exclude=["libc6"],
    keyrings=["keyring.asc"],
)
"""

URI = "https://example.com/debian"


def locked_package(
    name: str, depends: list[str], arch: str = "amd64", closure: list[str] | None = None, uri: str = URI
) -> dict:
    content = deb_content(name, arch)
    return {
        "name": name,
        "version": "1.0",
        "architecture": arch,
        "url": f"{uri}/pool/main/{name[0]}/{name}/{name}_1.0_{arch}.deb",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "depends": depends,
        "closure": depends if closure is None else closure,
    }


def deb_content(name: str, arch: str) -> bytes:
    return f"{name} {arch}".encode()


def write_debs(lockfile: dict) -> None:
    """Writes the `.deb` files of a lockfile whose packages are locked at `file://` URLs."""
    for arch in lockfile["architectures"].values():
        for package in arch["packages"]:
            path = Path(urlparse(package["url"]).path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(deb_content(package["name"], package["architecture"]))


def write_project(rule_runner: RuleRunner, lockfile: dict | None, build: str = BUILD) -> None:
    files = {"debs/BUILD": build.format(uri=URI), "debs/keyring.asc": fixture("rsa.asc").decode()}
    if lockfile is not None:
        files["debs/bookworm.lock.json"] = json.dumps(lockfile)
    rule_runner.write_files(files)
    rule_runner.set_options(['--apt-resolves={"bookworm": "debs/bookworm.lock.json"}'], env_inherit={"PATH"})


def valid_lockfile(uri: str = URI) -> dict:
    return {
        "version": LOCKFILE_VERSION,
        "inputs": normalize_inputs([f"{URI} stable main"], ["amd64", "arm64"], ["jq"], ["libc6"]),
        "architectures": {
            "amd64": {
                "roots": {"jq": "jq"},
                "packages": [
                    locked_package("jq", ["libjq1"], uri=uri),
                    locked_package("libjq1", [], uri=uri),
                ],
            },
            "arm64": {"roots": {"jq": "jq"}, "packages": [locked_package("jq", [], "arm64", uri=uri)]},
        },
    }


def address(generated_name: str) -> Address:
    return Address("debs", target_name="bookworm", generated_name=generated_name)


def deb_files(rule_runner: RuleRunner, generated_name: str) -> list[str]:
    """The `.deb` files of a generated target, as `oci_deb_layer` hydrates them."""
    target = rule_runner.get_target(address(generated_name))
    hydrated = rule_runner.request(
        HydratedSources,
        [
            HydrateSourcesRequest(
                target[SourcesField], for_sources_types=(FileSourceField,), enable_codegen=True
            )
        ],
    )
    contents = rule_runner.request(DigestContents, [hydrated.snapshot.digest])
    for content in contents:
        name, arch = os.path.basename(content.path).split("_")[0], generated_name.split("/")[0]
        assert content.content == deb_content(name, arch)
    return [content.path for content in contents]


def dependencies(rule_runner: RuleRunner, target_address: Address) -> list[str]:
    target = rule_runner.get_target(target_address)
    addresses = rule_runner.request(Addresses, [DependenciesRequest(target[Dependencies])])
    return sorted(a.spec for a in addresses)


@pytest.mark.parametrize("lockfile", [None, valid_lockfile()])
def test_generates_targets_from_build_file(rule_runner: RuleRunner, lockfile: dict | None) -> None:
    # The lockfile isn't read, so `pants list ::` works without one.
    write_project(rule_runner, lockfile, BUILD.replace('packages=["jq"]', 'packages=["jq", "curl"]'))

    jq = rule_runner.get_target(address("amd64/jq"))
    assert isinstance(jq, DebPackageTarget)
    assert jq[DebPackageArchitectureField].value == "amd64"
    assert jq[DebPackagePackageField].value == "jq"

    group = rule_runner.get_target(address("amd64"))
    assert isinstance(group, DebPackageGroupTarget)
    assert dependencies(rule_runner, group.address) == ["debs:bookworm#amd64/curl", "debs:bookworm#amd64/jq"]
    assert dependencies(rule_runner, address("arm64")) == [
        "debs:bookworm#arm64/curl",
        "debs:bookworm#arm64/jq",
    ]

    # Only requested packages get targets.
    with engine_error(contains="was not generated by the target"):
        rule_runner.get_target(address("amd64/libjq1"))


def test_sources_are_the_locked_closure(rule_runner: RuleRunner, tmp_path: Path) -> None:
    lock = valid_lockfile(tmp_path.as_uri())
    write_debs(lock)
    write_project(rule_runner, lock)

    assert deb_files(rule_runner, "amd64/jq") == ["debs/jq_1.0_amd64.deb", "debs/libjq1_1.0_amd64.deb"]
    assert deb_files(rule_runner, "arm64/jq") == ["debs/jq_1.0_arm64.deb"]


def test_virtual_package_sources(rule_runner: RuleRunner, tmp_path: Path) -> None:
    build = BUILD.replace('["amd64", "arm64"]', '["amd64"]').replace('packages=["jq"]', 'packages=["awk"]')
    lock = {
        "version": LOCKFILE_VERSION,
        "inputs": normalize_inputs([f"{URI} stable main"], ["amd64"], ["awk"], ["libc6"]),
        "architectures": {
            "amd64": {
                "roots": {"awk": "mawk"},
                "packages": [locked_package("mawk", [], uri=tmp_path.as_uri())],
            }
        },
    }
    write_debs(lock)
    write_project(rule_runner, lock, build)

    assert deb_files(rule_runner, "amd64/awk") == ["debs/mawk_1.0_amd64.deb"]


def test_cyclic_lockfile(rule_runner: RuleRunner, tmp_path: Path) -> None:
    # libc6 and libgcc-s1 depend on each other, as they do in Debian.
    uri = tmp_path.as_uri()
    packages = [
        locked_package("libc6", ["libgcc-s1"], closure=["libgcc-s1"], uri=uri),
        locked_package("libgcc-s1", ["libc6"], closure=["libc6"], uri=uri),
    ]
    build = (
        BUILD.replace('["amd64", "arm64"]', '["amd64"]')
        .replace('exclude=["libc6"]', "exclude=[]")
        .replace('packages=["jq"]', 'packages=["libc6", "libgcc-s1"]')
    )
    lock = {
        "version": LOCKFILE_VERSION,
        "inputs": normalize_inputs([f"{URI} stable main"], ["amd64"], ["libc6", "libgcc-s1"], []),
        "architectures": {
            "amd64": {"roots": {"libc6": "libc6", "libgcc-s1": "libgcc-s1"}, "packages": packages}
        },
    }
    write_debs(lock)
    write_project(rule_runner, lock, build)

    both = ["debs/libc6_1.0_amd64.deb", "debs/libgcc-s1_1.0_amd64.deb"]
    assert deb_files(rule_runner, "amd64/libc6") == both
    assert deb_files(rule_runner, "amd64/libgcc-s1") == both


@pytest.mark.parametrize(
    "lockfile, problem",
    [
        (None, "doesn't exist"),
        ({**valid_lockfile(), "version": 2}, "has an unsupported version 2"),
        (
            {**valid_lockfile(), "inputs": normalize_inputs([f"{URI} stable main"], ["amd64"], ["jq"], [])},
            "is out of date",
        ),
        (
            {
                **valid_lockfile(),
                "architectures": {
                    **valid_lockfile()["architectures"],
                    "amd64": {"roots": {}, "packages": []},
                },
            },
            "is malformed: it has no consistent closure of `jq`",
        ),
    ],
)
def test_unusable_lockfile_is_an_error(rule_runner: RuleRunner, lockfile: dict | None, problem: str) -> None:
    write_project(rule_runner, lockfile)

    with pytest.raises(ExecutionError) as e:
        deb_files(rule_runner, "amd64/jq")
    (error,) = e.value.wrapped_exceptions
    assert isinstance(error, DebPackagesLockfileError)
    message = str(error)
    assert (
        f"The lockfile debs/bookworm.lock.json for the `bookworm` resolve of debs:bookworm {problem}"
        in message
    )
    assert "pants generate-lockfiles --resolve=bookworm" in message


def test_unknown_resolve(rule_runner: RuleRunner) -> None:
    write_project(rule_runner, valid_lockfile(), BUILD.replace('resolve="bookworm"', 'resolve="other"'))

    with engine_error(contains="isn't a key of `[apt].resolves`. Known resolves: bookworm"):
        rule_runner.get_target(address("amd64/jq"))


def test_generates_lockfile(rule_runner: RuleRunner, tmp_path: Path) -> None:
    write_repo(
        tmp_path,
        "stable",
        {
            "main/amd64": "\n".join(
                [stanza("jq", Depends="libjq1, libc6"), stanza("libjq1", Depends="libc6"), stanza("libc6")]
            ),
            "main/arm64": stanza("jq", Architecture="arm64"),
        },
    )
    write_project(rule_runner, None, BUILD.replace("{uri}", tmp_path.as_uri()))

    (request,) = rule_runner.request(
        UserGenerateLockfiles, [lockfile.RequestedDebPackagesUserResolveNames(["bookworm"])]
    )
    assert request.lockfile_dest == "debs/bookworm.lock.json"

    result = rule_runner.request(GenerateLockfileResult, [request])
    (content,) = rule_runner.request(DigestContents, [result.digest])
    assert content.path == "debs/bookworm.lock.json"

    locked = json.loads(content.content)
    assert locked["inputs"] == json.loads(request.inputs_json)
    amd64 = locked["architectures"]["amd64"]
    assert amd64["roots"] == {"jq": "jq"}
    assert [(p["name"], p["depends"]) for p in amd64["packages"]] == [("jq", ["libjq1"]), ("libjq1", [])]

    # The generated lockfile is accepted when the packages are used: it gets as far as downloading
    # the `.deb` files, which the test repository doesn't have.
    rule_runner.write_files({"debs/bookworm.lock.json": content.content.decode()})
    with engine_error(contains=f"opening file at {tmp_path}/pool/main/"):
        deb_files(rule_runner, "amd64/jq")


def test_generate_lockfile_fails_on_unverified_release(rule_runner: RuleRunner, tmp_path: Path) -> None:
    write_repo(
        tmp_path, "stable", {"main/amd64": stanza("jq"), "main/arm64": stanza("jq", Architecture="arm64")}
    )
    write_project(rule_runner, None, BUILD.replace("{uri}", tmp_path.as_uri()))
    rule_runner.write_files({"debs/keyring.asc": fixture("other.asc").decode()})

    (request,) = rule_runner.request(
        UserGenerateLockfiles, [lockfile.RequestedDebPackagesUserResolveNames(["bookworm"])]
    )
    with engine_error(contains="dists/stable/InRelease doesn't verify: No good signature"):
        rule_runner.request(GenerateLockfileResult, [request])


@pytest.mark.parametrize(
    "replacement",
    [
        "",
        '    keyrings=["keyring.asc"],\n    allow_unsigned=True,\n',
    ],
)
def test_needs_keyrings_or_allow_unsigned(rule_runner: RuleRunner, replacement: str) -> None:
    write_project(rule_runner, None, BUILD.replace('    keyrings=["keyring.asc"],\n', replacement))

    with engine_error(contains="must set either `keyrings`"):
        rule_runner.request(
            UserGenerateLockfiles, [lockfile.RequestedDebPackagesUserResolveNames(["bookworm"])]
        )


def test_generates_lockfile_unsigned(rule_runner: RuleRunner, tmp_path: Path) -> None:
    write_repo(
        tmp_path, "stable", {"main/amd64": stanza("jq"), "main/arm64": stanza("jq", Architecture="arm64")}
    )
    for name in ("InRelease", "Release.gpg"):
        (tmp_path / "dists" / "stable" / name).unlink()
    write_project(
        rule_runner,
        None,
        BUILD.replace("{uri}", tmp_path.as_uri()).replace('keyrings=["keyring.asc"]', "allow_unsigned=True"),
    )

    (request,) = rule_runner.request(
        UserGenerateLockfiles, [lockfile.RequestedDebPackagesUserResolveNames(["bookworm"])]
    )
    result = rule_runner.request(GenerateLockfileResult, [request])
    (content,) = rule_runner.request(DigestContents, [result.digest])
    assert json.loads(content.content)["architectures"]["amd64"]["roots"] == {"jq": "jq"}


def test_resolve_needs_exactly_one_target(rule_runner: RuleRunner) -> None:
    write_project(rule_runner, None, BUILD + BUILD.replace('name="bookworm"', 'name="again"', 1))

    with engine_error(contains="must be declared by exactly one `deb_packages` target, found "):
        rule_runner.request(
            UserGenerateLockfiles, [lockfile.RequestedDebPackagesUserResolveNames(["bookworm"])]
        )
