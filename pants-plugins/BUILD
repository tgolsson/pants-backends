pants_requirements(name="pants", resolve="pants-plugins")

files(name="register_files", sources=["*/pants_backend_*/**/register.py", "!odin/**"])

python_tests(
    name="tests",
    sources=["target_help_test.py"],
    dependencies=[
        ":register_files",
        "pants-plugins/bitwarden/pants_backend_bitwarden/register.py",
        "pants-plugins/cargo/pants_backend_cargo/register.py",
        "pants-plugins/cargo/pants_backend_cargo/backends/clippy/register.py",
        "pants-plugins/cc/pants_backend_cc/register.py",
        "pants-plugins/cc/pants_backend_cc/coverage/register.py",
        "pants-plugins/cc/pants_backend_cc/generate/register.py",
        "pants-plugins/cc/pants_backend_cc/lint/clang_tidy/register.py",
        "pants-plugins/cmake/pants_backend_cmake/register.py",
        "pants-plugins/distroless/pants_backend_distroless/register.py",
        "pants-plugins/generate/pants_backend_generate/register.py",
        "pants-plugins/k8s/pants_backend_k8s/register.py",
        "pants-plugins/kustomize/pants_backend_kustomize/register.py",
        "pants-plugins/mdbook/pants_backend_mdbook/register.py",
        "pants-plugins/oci/pants_backend_oci/register.py",
        "pants-plugins/oci/pants_backend_oci/docker/register.py",
        "pants-plugins/oci/pants_backend_oci/utility/mirror/register.py",
        "pants-plugins/platforms/pants_backend_platforms/register.py",
        "pants-plugins/secrets/pants_backend_secrets/register.py",
    ],
)
