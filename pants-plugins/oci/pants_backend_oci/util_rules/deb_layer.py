"""

"""

from __future__ import annotations

import datetime
import io
import logging
from dataclasses import dataclass

from debian import deb822, debfile
from pants.core.goals.package import BuiltPackage, BuiltPackageArtifact, PackageFieldSet
from pants.core.target_types import FileSourceField
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.core.util_rules.system_binaries import (
    BashBinary,
    BinaryPath,
    BinaryPathRequest,
    BinaryPaths,
    BinaryPathTest,
)
from pants.engine.addresses import Address
from pants.engine.fs import (
    CreateDigest,
    Digest,
    DigestContents,
    Directory,
    DownloadFile,
    FileContent,
    MergeDigests,
    Snapshot,
)
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    FieldSetsPerTarget,
    FieldSetsPerTargetRequest,
    SourcesField,
    Target,
    Targets,
)
from pants.version import PANTS_SEMVER, Version

from pants_backend_oci.target_types import DebArchive
from pants_backend_oci.targets import DebLayer
from pants_backend_oci.util_rules.archive import CreateDeterministicTar
from pants_backend_oci.util_rules.layer import BuiltLayerArtifact, ImageLayer

logger = logging.getLogger(__name__)


class CurlBinary(BinaryPath):
    pass


@dataclass(frozen=True)
class CurlBinaryRequest:
    pass


class ArBinary(BinaryPath):
    pass


@dataclass(frozen=True)
class ArBinaryRequest:
    pass


class XzBinary(BinaryPath):
    pass


@dataclass(frozen=True)
class XzBinaryRequest:
    pass


class GzipBinary(BinaryPath):
    pass


@dataclass(frozen=True)
class GzipBinaryRequest:
    pass


if PANTS_SEMVER >= Version("2.19.0.dev0"):
    from pants.core.util_rules.system_binaries import SystemBinariesSubsystem

    @rule(desc="Finding the `xz` binary")
    async def find_xz(
        system_binaries_subsystem: SystemBinariesSubsystem.EnvironmentAware,
    ) -> XzBinary:
        request = BinaryPathRequest(
            binary_name="xz",
            search_path=system_binaries_subsystem,
            test=BinaryPathTest(args=["--version"]),
        )
        paths = await Get(BinaryPaths, BinaryPathRequest, request)
        first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
        return XzBinary(first_path.path, first_path.fingerprint)

    from pants.core.util_rules.system_binaries import SystemBinariesSubsystem

    @rule(desc="Finding the `ar` binary")
    async def find_ar(
        system_binaries_subsystem: SystemBinariesSubsystem.EnvironmentAware,
    ) -> ArBinary:
        request = BinaryPathRequest(
            binary_name="ar",
            search_path=system_binaries_subsystem,
            test=BinaryPathTest(args=["--version"]),
        )
        paths = await Get(BinaryPaths, BinaryPathRequest, request)
        first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
        return ArBinary(first_path.path, first_path.fingerprint)

    @rule(desc="Finding the `curl` binary")
    async def find_curl(
        system_binaries_subsystem: SystemBinariesSubsystem.EnvironmentAware,
    ) -> CurlBinary:
        request = BinaryPathRequest(
            binary_name="curl",
            search_path=system_binaries_subsystem,
            test=BinaryPathTest(args=["--version"]),
        )
        paths = await Get(BinaryPaths, BinaryPathRequest, request)
        first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
        return CurlBinary(first_path.path, first_path.fingerprint)

    @rule(desc="Finding the `gzip` binary")
    async def find_gzip(
        system_binaries_subsystem: SystemBinariesSubsystem.EnvironmentAware,
    ) -> GzipBinary:
        request = BinaryPathRequest(
            binary_name="gzip",
            search_path=system_binaries_subsystem,
            test=BinaryPathTest(args=["--version"]),
        )
        paths = await Get(BinaryPaths, BinaryPathRequest, request)
        first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
        return GzipBinary(first_path.path, first_path.fingerprint)

else:
    from pants.core.util_rules.system_binaries import SEARCH_PATHS

    @rule(desc="Finding the `xz` binary")
    async def find_xz() -> XzBinary:
        request = BinaryPathRequest(
            binary_name="xz", search_path=SEARCH_PATHS, test=BinaryPathTest(args=["--version"])
        )
        paths = await Get(BinaryPaths, BinaryPathRequest, request)
        first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
        return XzBinary(first_path.path, first_path.fingerprint)

    @rule(desc="Finding the `ar` binary")
    async def find_ar() -> ArBinary:
        request = BinaryPathRequest(
            binary_name="ar", search_path=SEARCH_PATHS, test=BinaryPathTest(args=["--version"])
        )
        paths = await Get(BinaryPaths, BinaryPathRequest, request)
        first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
        return ArBinary(first_path.path, first_path.fingerprint)

    @rule(desc="Finding the `curl` binary")
    async def find_curl() -> CurlBinary:
        request = BinaryPathRequest(
            binary_name="curl", search_path=SEARCH_PATHS, test=BinaryPathTest(args=["--version"])
        )
        paths = await Get(BinaryPaths, BinaryPathRequest, request)
        first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
        return CurlBinary(first_path.path, first_path.fingerprint)

    @rule(desc="Finding the `gzip` binary")
    async def find_gzip() -> GzipBinary:
        request = BinaryPathRequest(
            binary_name="gzip", search_path=SEARCH_PATHS, test=BinaryPathTest(args=["--version"])
        )
        paths = await Get(BinaryPaths, BinaryPathRequest, request)
        first_path = paths.first_path_or_raise(request, rationale="work with `json` data")
        return GzipBinary(first_path.path, first_path.fingerprint)


@dataclass(frozen=True)
class DebLayerRequest:
    target: Target


@dataclass(frozen=True)
class AnalyzeDebianPackageRequest:
    name: str
    pool: str = "main"


@dataclass(frozen=True)
class AnalyzeDebianPackageResult:
    pass


@dataclass(frozen=True)
class DebianIndexRequest:
    snapshot: str
    debian: str
    arch: str
    pool: str


@dataclass(frozen=True)
class DebianIndexResult:
    pass


@rule
async def get_index(
    req: DebianIndexRequest, curl: CurlBinary, gzip: GzipBinary, bash: BashBinary
) -> DebianIndexResult:
    # https://snapshot.debian.org/archive/debian/20240307T094241Z/dists/bookworm/main/binary-amd64/Packages.gz
    base = "https://snapshot.debian.org/archive/debian"
    relative = f"{req.snapshot}/dists/{req.debian}/{req.pool}/binary-{req.arch}/Packages.gz"

    full_url = f"{base}/{relative}"

    script = f"""
{curl.path} -L {full_url} -O
{gzip.path} -d Packages.gz
    """

    script_digest = await Get(Digest, CreateDigest([FileContent("download.sh", script.encode("utf-8"))]))

    result = await Get(
        ProcessResult,
        Process(
            argv=(bash.path, "download.sh"),
            description="Downloading debian package list",
            input_digest=script_digest,
            output_files=("Packages",),
        ),
    )

    pool_url = f"{base}/{req.snapshot}"
    result_content = await Get(DigestContents, Digest, result.output_digest)
    packages = []
    f = io.BytesIO(result_content[0].content)
    for p in deb822.Packages.iter_paragraphs(f, use_apt_pkg=False):
        if "python" in p["Package"] and "3.11" in p["Version"]:
            print(p)


@rule
async def analyze_package(
    req: AnalyzeDebianPackageRequest,
    curl: CurlBinary,
) -> AnalyzeDebianPackageResult:
    index = await Get(
        DebianIndexResult,
        DebianIndexRequest(
            "20240307T094241Z",
            "bookworm",
            "amd64",
            "main",
        ),
    )
    base_path = "https://snapshot.debian.org/archive/debian/20240307T094241Z/pool/"

    if req.name.startswith("lib"):
        prefix = req.name[:4]

    else:
        prefix = req.name[0]

    root_page = f"{base_path}/{req.pool}/{prefix}"

    pagecontent = await Get(
        ProcessResult,
        Process(
            argv=(curl.path, "-L", root_page),
            description="Downloading debian package list",
        ),
    )

    print(pagecontent.stdout.decode("utf-8"))


@rule
async def build_deb_layer(request: DebLayerRequest) -> ImageLayer:
    res = await Get(AnalyzeDebianPackageResult, AnalyzeDebianPackageRequest("python3.10"))
    root_dependencies = await Get(Targets, DependenciesRequest(request.target[Dependencies]))

    # Get all file sources from the root dependencies. That includes any non-file sources that can
    # be "codegen"ed into a file source.
    sources_request = Get(
        SourceFiles,
        SourceFilesRequest(
            sources_fields=[tgt.get(SourcesField) for tgt in root_dependencies],
            for_sources_types=(FileSourceField,),
            enable_codegen=True,
        ),
    )

    sources, ar, xz, bash = await MultiGet(
        sources_request,
        Get(ArBinary),
        Get(XzBinary),
        Get(BashBinary),
    )

    root_contents = await Get(DigestContents, Digest, sources.snapshot.digest)
    print(root_contents[0])
    deb = debfile.DebFile(fileobj=io.BytesIO(root_contents[0].content))
    print(deb.control)

    extract_script = f"""
{ar.path} xo {sources.files[0]} data.tar.xz
{xz.path} --decompress data.tar.xz
    """
    script_digest = await Get(
        Digest, CreateDigest([FileContent("extract.sh", extract_script.encode("utf-8"))])
    )

    input_digest = await Get(
        Digest,
        MergeDigests([script_digest, sources.snapshot.digest]),
    )

    process = await Get(
        ProcessResult,
        Process(
            argv=(bash.path, "extract.sh"),
            input_digest=input_digest,
            description="Extracting deb archive",
            output_files=["data.tar"],
        ),
    )

    timestamp = datetime.datetime(1970, 1, 1).isoformat() + "Z"
    config = [
        "--config.env",
        "BUILT_BY=pants.oci",
        "--author=pants_backend_oci",
        f"--created={timestamp}",
        "--no-history",
    ]

    return ImageLayer(
        request.target.address,
        process.output_digest,
        (
            "raw",
            "add-layer",
            "--history.author=pants_backend_oci",
            f"--history.created_by='Layer target: {request.target.address}'",
            f"--history.comment='Layer target: {request.target.address}'",
            f"--history.created={timestamp}",
            "--image",
            "build:build",
            "data.tar",
        ),
        (
            "config",
            *config,
            "--image",
            "build:build",
        ),
        compressed=False,
    )


def rules():
    return collect_rules()
