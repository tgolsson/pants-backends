from types import SimpleNamespace

import pytest
from pants.core.util_rules.system_binaries import BinaryPath, BinaryPaths
from pants.engine.fs import EMPTY_DIGEST
from pants.engine.platform import Platform
from pants.engine.process import ProcessCacheScope
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.util_rules.image_bundle import ImageBundle
from pants_backend_oci.util_rules.run import (
    LocalPodmanBinary,
    PodmanBinary,
    PodmanBuildRequest,
    find_local_podman,
    find_podman,
    podman_build,
)
from pants_backend_oci.util_rules.sandboxed import OciBuilder


def build_process(podman: LocalPodmanBinary):
    processes = []
    run_rule_with_mocks(
        podman_build,
        rule_args=[
            PodmanBuildRequest(
                ImageBundle(EMPTY_DIGEST, "sha256:abc", True, metadata=EMPTY_DIGEST), ("layer",), "build"
            ),
            podman,
            OciBuilder(EMPTY_DIGEST),
            SimpleNamespace(system_binary_paths=("/usr/bin", "/bin")),
        ],
        mock_calls={
            "pants.core.util_rules.env_vars.environment_vars_subset": lambda *a, **k: {
                "XDG_RUNTIME_DIR": "/run/user/1000"
            },
            "pants.engine.process.fallible_to_exec_result_or_raise": processes.append,
        },
    )
    return processes[0]


def find(request, **kwargs):
    return BinaryPaths(request.binary_name, (BinaryPath(f"/usr/bin/{request.binary_name}"),))


def test_podman_is_found_without_running_it():
    podman = run_rule_with_mocks(
        find_podman,
        rule_args=[SimpleNamespace(system_binary_paths=("/usr/bin",))],
        mock_calls={"pants.core.util_rules.system_binaries.find_binary": find},
    )
    assert podman.path == "/usr/bin/podman"


def test_build_step_podman_is_fingerprinted_by_its_hash_without_running_it():
    processes = []

    def execute(process, **kwargs):
        processes.append(process)
        return SimpleNamespace(stdout=b"0123abcd  /usr/bin/podman\n")

    podman = run_rule_with_mocks(
        find_local_podman,
        rule_args=[
            PodmanBinary("/usr/bin/podman"),
            SimpleNamespace(system_binary_paths=("/usr/bin",)),
            Platform.linux_x86_64,
            SimpleNamespace(
                executable_search_path_cache_scope=lambda: ProcessCacheScope.PER_RESTART_SUCCESSFUL
            ),
        ],
        mock_calls={
            "pants.core.util_rules.system_binaries.find_binary": find,
            "pants.engine.process.fallible_to_exec_result_or_raise": execute,
        },
    )
    assert podman == LocalPodmanBinary("/usr/bin/podman", "0123abcd")
    assert [process.argv for process in processes] == [("/usr/bin/sha256sum", "/usr/bin/podman")]
    assert processes[0].cache_scope == ProcessCacheScope.PER_RESTART_SUCCESSFUL


@pytest.mark.parametrize("platform", [Platform.macos_arm64, Platform.macos_x86_64])
def test_build_steps_need_local_linux_podman(platform):
    with pytest.raises(ValueError, match="Scripted OCI build steps require local Linux podman"):
        run_rule_with_mocks(
            find_local_podman, rule_args=[PodmanBinary("/usr/bin/podman"), SimpleNamespace(), platform, None]
        )


def test_podman_upgrade_changes_the_build_process():
    before = build_process(LocalPodmanBinary("/usr/bin/podman", "old"))
    after = build_process(LocalPodmanBinary("/usr/bin/podman", "new"))
    assert before.argv == after.argv
    assert before != after


def test_build_steps_get_the_users_runtime_directory():
    process = build_process(LocalPodmanBinary("/usr/bin/podman", "abc"))
    assert process.env["XDG_RUNTIME_DIR"] == "/run/user/1000"
