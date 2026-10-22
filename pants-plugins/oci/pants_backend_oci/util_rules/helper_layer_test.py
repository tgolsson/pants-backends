from __future__ import annotations

import io
import tarfile

import pytest
from pants.core.goals import package as package_goal
from pants.core.goals.package import BuiltPackage
from pants.core.target_types import FileTarget, GenericTarget
from pants.core.target_types import rules as core_target_type_rules
from pants.core.util_rules import source_files
from pants.engine.addresses import Address
from pants.engine.fs import DigestContents
from pants.testutil.rule_runner import QueryRule, RuleRunner, engine_error

from pants_backend_oci.targets import CaCertsLayer, GroupLayer, PasswdLayer
from pants_backend_oci.util_rules import archive, deb_layer, helper_layer, layer
from pants_backend_oci.util_rules.deb_layer_builder_test import FOO
from pants_backend_oci.util_rules.helper_layer_builder import CA_BUNDLE
from pants_backend_oci.util_rules.helper_layer_builder_test import CERT_B, ca_certificates_deb

FIELD_SETS = {
    "oci_passwd_layer": helper_layer.PasswdLayerFieldSet,
    "oci_group_layer": helper_layer.GroupLayerFieldSet,
    "oci_cacerts_layer": helper_layer.CaCertsLayerFieldSet,
}


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        target_types=[PasswdLayer, GroupLayer, CaCertsLayer, FileTarget, GenericTarget],
        rules=[
            *archive.rules(),
            *deb_layer.rules(),
            *helper_layer.rules(),
            *layer.rules(),
            *package_goal.rules(),
            *source_files.rules(),
            *core_target_type_rules(),
            *(QueryRule(BuiltPackage, [field_set]) for field_set in FIELD_SETS.values()),
            QueryRule(layer.ImageLayer, [layer.ImageLayerRequest]),
        ],
    )


BUILD = """
oci_passwd_layer(
    name="passwd",
    users=[
        {"name": "root", "uid": 0, "gid": 0, "home": "/root", "shell": "/bin/sh"},
        {"name": "nonroot", "uid": 65532, "gid": 65532},
    ],
)
oci_group_layer(
    name="group",
    groups=[{"name": "root", "gid": 0}, {"name": "nonroot", "gid": 65532, "members": ["nonroot"]}],
)
file(name="foo", source="foo_1.0-1_amd64.deb")
file(name="ca", source="ca-certificates_1.0-1_all.deb", dependencies=[":foo"])
oci_cacerts_layer(name="cacerts", debs=[":ca"])
"""


def write(rule_runner: RuleRunner, build: str = BUILD) -> None:
    rule_runner.write_files(
        {
            "img/foo_1.0-1_amd64.deb": FOO,
            "img/ca-certificates_1.0-1_all.deb": ca_certificates_deb(),
            "img/BUILD": build,
        }
    )


def package(rule_runner: RuleRunner, name: str) -> BuiltPackage:
    target = rule_runner.get_target(Address("img", target_name=name))
    field_set = FIELD_SETS[target.alias].create(target)
    return rule_runner.request(BuiltPackage, [field_set])


def read(rule_runner: RuleRunner, built: BuiltPackage, name: str) -> bytes:
    (content,) = rule_runner.request(DigestContents, [built.digest])
    with tarfile.open(fileobj=io.BytesIO(content.content)) as tar:
        extracted = tar.extractfile(name)
        assert extracted is not None
        return extracted.read()


def test_packages_passwd_layer(rule_runner: RuleRunner) -> None:
    write(rule_runner)
    built = package(rule_runner, "passwd")
    assert [a.relpath for a in built.artifacts] == ["img/passwd.tar"]
    assert read(rule_runner, built, "etc/passwd") == (
        b"root:x:0:0::/root:/bin/sh\nnonroot:x:65532:65532::/home/nonroot:/sbin/nologin\n"
    )


def test_packages_group_layer(rule_runner: RuleRunner) -> None:
    write(rule_runner)
    built = package(rule_runner, "group")
    assert [a.relpath for a in built.artifacts] == ["img/group.tar"]
    assert read(rule_runner, built, "etc/group") == b"root:x:0:\nnonroot:x:65532:nonroot\n"


def test_cacerts_layer_picks_ca_certificates_from_closure(rule_runner: RuleRunner) -> None:
    write(rule_runner)
    built = package(rule_runner, "cacerts")
    assert [a.relpath for a in built.artifacts] == ["img/cacerts.tar"]
    assert CERT_B in read(rule_runner, built, CA_BUNDLE)


def test_cacerts_layer_finds_package_with_unconventional_filename(rule_runner: RuleRunner) -> None:
    rule_runner.write_files({"img/certs.deb": ca_certificates_deb()})
    write(
        rule_runner,
        BUILD
        + '\nfile(name="certs", source="certs.deb")'
        + '\noci_cacerts_layer(name="renamed", debs=[":certs", ":foo"])\n',
    )
    assert CERT_B in read(rule_runner, package(rule_runner, "renamed"), CA_BUNDLE)


def test_cacerts_layer_requires_ca_certificates(rule_runner: RuleRunner) -> None:
    write(rule_runner, BUILD + '\noci_cacerts_layer(name="no_ca", debs=[":foo"])\n')
    with engine_error(contains="must bring exactly one `ca-certificates` package, found: none"):
        package(rule_runner, "no_ca")


def test_invalid_users_are_reported(rule_runner: RuleRunner) -> None:
    write(rule_runner, 'oci_passwd_layer(name="bad", users=[{"name": "a", "uid": 1}])\n')
    with engine_error(contains="The `users` field of img:bad is invalid: user 'a' is missing gid"):
        rule_runner.get_target(Address("img", target_name="bad"))


@pytest.mark.parametrize("name", ["passwd", "group", "cacerts"])
def test_helper_layers_are_added_as_their_own_layer(rule_runner: RuleRunner, name: str) -> None:
    write(rule_runner)
    target = rule_runner.get_target(Address("img", target_name=name))

    for old_style in (True, False):
        image_layer = rule_runner.request(layer.ImageLayer, [layer.ImageLayerRequest(target, old_style)])

        # The layer tarball is added as-is, not re-archived as a file inside another layer.
        assert image_layer.layer_command[-1] == f"img/{name}.tar"
        assert "--config.entrypoint" not in image_layer.config_command
