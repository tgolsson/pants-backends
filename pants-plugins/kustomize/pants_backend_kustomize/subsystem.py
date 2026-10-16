from __future__ import annotations

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.option.option_types import BoolOption
from pants.util.strutil import softwrap


class KustomizeTool(ExternalTool):
    options_scope = "kustomize-tool"
    help = "Wrapper for kustomize."

    default_version = "v5.8.3"
    default_known_versions = [
        "v5.8.3|linux_x86_64|cb9e31198d3f63b44848bd0afc4c8efc56e6cfef9c93a4a39a211940e6decd61|5059714",
        "v5.8.3|linux_arm64|9867b76482cfc6d25b546abc1d2b5a32bd137505aba640649b4b378f64f6d68f|4550157",
        "v5.8.3|macos_x86_64|d08fb85846c3d977f0e2a66e7ff45f1b5264c294685e5e726e1a8bdfe0da8dee|5132020",
        "v5.8.3|macos_arm64|4cbb08e209d8fff47cd60bfb6407daaf5ee699f9b9295849a1065024fac2d1ef|4726414",
        "v5.7.1|linux_x86_64|ea375e7372f9aa029129d4b2d16c66b7750b7f1213c4f66f910d981c895818d8|5805549",
        "v5.7.1|linux_arm64|4261a040217df3bd6896597c3986d1465925726e4f22a945304b5233a4dcdbda|5303342",
        "v5.7.1|macos_x86_64|4a0dff80c5644df6bc8f51b342842969004cb6ba5f94dddaabbea7483493273d|5902481",
        "v5.7.1|macos_arm64|073e9d16d5a235e2ff83e62d6b76edb5d962adbc33be1e4860c4b3f1f39b33b9|5516432",
        "v5.0.3|linux_x86_64|c627b1575c3fecbc7ad1c181c23a7adcacf19732dab627eb57e89a7bc4c1e929|5887075",
        "v5.0.3|linux_arm64|c92e9b4ad3ccd10077566eddddfc58748aa19ccc2b0fe17600fd57f6472b8bfe|5384859",
        "v5.0.3|macos_x86_64|a3300ccc81ed8e7df415f3537b49e70d89f985a28c9ade8a885ebf6f1689b4e0|10869215",
        "v5.0.3|macos_arm64|ecb15ba64356507f8c73796acbe79b445c17f637963b05be72a905c05f6abfc1|10470691",
        "v4.5.7|linux_x86_64|701e3c4bfa14e4c520d481fdf7131f902531bfc002cb5062dcf31263a09c70c9|5757484",
        "v4.5.7|linux_arm64|65665b39297cc73c13918f05bbe8450d17556f0acd16242a339271e14861df67|5291095",
        "v4.5.7|macos_x86_64|6fd57e78ed0c06b5bdd82750c5dc6d0f992a7b926d114fe94be46d7a7e32b63a|10496583",
        "v4.5.7|macos_arm64|3c1e8b95cef4ff6e52d5f4b8c65b8d9d06b75f42d1cb40986c1d67729d82411a|10169842",
    ]

    tailor = BoolOption(
        default=True,
        help=softwrap(""" If true, add `kustomization` targets with the `tailor` goal."""),
        advanced=True,
    )

    def generate_url(self, plat: Platform) -> str:
        platform_mapping = {
            "macos_arm64": "darwin_arm64",
            "macos_x86_64": "darwin_amd64",
            "linux_arm64": "linux_arm64",
            "linux_x86_64": "linux_amd64",
        }
        plat_str = platform_mapping[plat.value]
        base = "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize"
        return f"{base}/{self.version}/kustomize_{self.version}_{plat_str}.tar.gz"

    def generate_exe(self, _: Platform) -> str:
        return "./kustomize"
