from types import SimpleNamespace

from pants.core.util_rules.system_binaries import BinaryPath, BinaryPaths
from pants.engine.fs import EMPTY_DIGEST
from pants.engine.platform import Platform
from pants.engine.process import ProcessCacheScope
from pants.testutil.rule_runner import run_rule_with_mocks

from pants_backend_oci.util_rules.image_bundle import ImageBundle
from pants_backend_oci.util_rules.run import (
    PodmanBinary,
    PodmanBuildRequest,
    find_podman,
    podman_build,
)
from pants_backend_oci.util_rules.sandboxed import OciBuilder


def build_process(podman: PodmanBinary):
    processes = []
    run_rule_with_mocks(
        podman_build,
        rule_args=[
            PodmanBuildRequest(ImageBundle(EMPTY_DIGEST, "sha256:abc", True), ("layer",), "build"),
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


def test_podman_is_fingerprinted_by_its_hash_without_running_it():
    processes = []

    def find(request, **kwargs):
        return BinaryPaths(request.binary_name, (BinaryPath(f"/usr/bin/{request.binary_name}"),))

    def execute(process, **kwargs):
        processes.append(process)
        return SimpleNamespace(stdout=b"0123abcd  /usr/bin/podman\n")

    podman = run_rule_with_mocks(
        find_podman,
        rule_args=[
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
    assert podman == PodmanBinary("/usr/bin/podman", "0123abcd")
    assert [process.argv for process in processes] == [("/usr/bin/sha256sum", "/usr/bin/podman")]
    assert processes[0].cache_scope == ProcessCacheScope.PER_RESTART_SUCCESSFUL


def test_podman_upgrade_changes_the_build_process():
    before = build_process(PodmanBinary("/usr/bin/podman", "old"))
    after = build_process(PodmanBinary("/usr/bin/podman", "new"))
    assert before.argv == after.argv
    assert before != after


def test_build_steps_get_the_users_runtime_directory():
    process = build_process(PodmanBinary("/usr/bin/podman", "abc"))
    assert process.env["XDG_RUNTIME_DIR"] == "/run/user/1000"
