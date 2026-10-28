from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from pants.base.build_environment import get_pants_cachedir

from pants_backend_oci.util_rules import podman_runner
from pants_backend_oci.util_rules.run import PODMAN_CACHE, PODMAN_STORE_VERSION


@pytest.fixture(scope="session")
def isolated_pants_args():
    """`run_pants` options that keep a test session's podman store and sandboxes to itself.

    The other named caches are the user's, so tools aren't fetched again."""
    root = Path(tempfile.mkdtemp(prefix="pants-oci-test-"))
    caches = root / "named_caches"
    caches.mkdir()
    shared = Path(get_pants_cachedir()) / "named_caches"
    if shared.is_dir():
        for cache in shared.iterdir():
            if cache.name != PODMAN_CACHE:
                (caches / cache.name).symlink_to(cache)
    (root / "sandboxes").mkdir()
    yield [f"--named-caches-dir={caches}", f"--local-execution-root-dir={root / 'sandboxes'}"]
    store = caches / PODMAN_CACHE / PODMAN_STORE_VERSION
    if store.exists():
        runtime = podman_runner.store_runtime(podman_runner.runtime_dir(os.geteuid()), store.resolve())
        shutil.rmtree(runtime, ignore_errors=True)
    # The store's files belong to the user's subordinate ids, and sandboxes hold read-only inputs.
    subprocess.run(["podman", "unshare", "rm", "-rf", str(root)], check=True)
