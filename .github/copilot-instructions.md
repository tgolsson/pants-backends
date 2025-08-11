# Pants Backends Repository

This repository contains custom Pants build system backends for container images (OCI), Kubernetes (k8s), documentation (mdbook), configuration management (kustomize), secrets management (bitwarden/secrets), and the Odin programming language.

**Always reference these instructions first and fallback to search or bash commands only when you encounter unexpected information that does not match the info here.**

## Working Effectively

### Initial Setup and Bootstrap
- Install Pants: `./get-pants.sh` -- takes ~25 seconds on first run. Downloads Pants launcher to ~/bin/pants.
- Add to PATH: `export PATH=/home/runner/bin:$PATH` or use full path `/home/runner/bin/pants`
- Verify installation: `pants --version` -- takes ~25 seconds on first run as it downloads Python interpreters and Pants runtime. **NEVER CANCEL: Set timeout to 60+ minutes.**

### Core Development Commands
- List all targets: `pants list ::` -- takes <1 second after initial setup
- List specific targets: `pants list pants-plugins/secrets::` or `pants list examples/`
- Inspect target details: `pants peek <target>` -- shows target configuration and metadata
- View available goals: `pants help goals` -- lists all available build goals
- View enabled backends: `pants help backends` -- shows active and available backends
- Run tests: `pants test ::` -- **NEVER CANCEL: Can take 15+ minutes. Set timeout to 30+ minutes.**
- Package all targets: `pants package ::` -- **NEVER CANCEL: Can take 30+ minutes. Set timeout to 60+ minutes.**
- Lint code: `pants lint ::` -- **NEVER CANCEL: Can take 10+ minutes. Set timeout to 30+ minutes.**
- Format code: `pants fmt ::` -- **NEVER CANCEL: Can take 10+ minutes. Set timeout to 30+ minutes.**
- Check code: `pants check ::` -- may not work as check goal is not activated for all backends

### Network and Download Issues
- **CRITICAL**: External downloads can be extremely slow (5-10+ minutes) or fail entirely due to network limitations
- Downloads include: Odin compiler (~101MB), ruff linter (~10MB), pex tool (~4MB), mdbook (~5MB), scc tool (~2MB)
- Commands that trigger downloads: `lint`, `fmt`, `update-build-files`, `count-loc`, `package` (depending on targets)
- Safe commands without downloads: `list`, `peek`, `help`, `tailor --check` (sometimes)
- If downloads hang or fail, this is NORMAL in sandboxed environments
- **NEVER CANCEL long-running downloads** - they may eventually succeed after 10+ minutes
- Document any consistent download failures in your changes (e.g., "ruff download fails due to network limitations")
- Use `pants list` and `pants peek` for inspection without triggering downloads

### Pre-commit Workflow
- Always run before committing: `./pre-commit.sh` -- runs the following sequence:
  1. `pants update-build-files ::`
  2. `pants tailor ::`
  3. `pants fix ::`
  4. `pants fmt ::`
  5. `pants lint ::`
  6. `pants check ::`
  7. `pants package ::`
  8. `pants test ::`

## Validation

### Manual Testing Scenarios
- **Safe Commands (No Downloads)**: Start with `pants list ::`, `pants peek <target>`, `pants help goals`
- **Documentation**: Test mdbook builds by running `pants package docs:docs` and verify output in `dist/docs/book/`
- **Container Images**: Test OCI builds in examples/oci/ directory with `pants package examples/oci::`
- **Kubernetes**: Test k8s configurations in examples/k8s/ directory  
- **Python Plugins**: Run tests for each backend in pants-plugins/ subdirectories
- **Secrets Management**: Test decrypt functionality: `pants decrypt examples/bitwarden::`
- **Target Inspection**: Use `pants peek <target>` to verify target configuration and dependencies
- **Backend Verification**: Run `pants help backends` to confirm all custom backends are loaded correctly

### CI/CD Validation
- Always run full CI validation commands before submitting changes:
  - `pants update-build-files --check lint ::` -- validates linting and build file updates
  - `pants tailor --check ::` -- validates tailor files are up to date
- CI runs on Python 3.9 and 3.11 with Pants versions 2.23.0 and 2.24.0
- **CRITICAL**: Timeout all CI-equivalent commands to 60+ minutes minimum

### Build and Test Infrastructure
- **Python Requirements**: Python 3.9-3.11 (configured in pants.toml)
- **Resolves**: Multiple Python resolves for different components (pants-plugins, pants-current, pants-previous, etc.)
- **Lock Files**: Located in pants-plugins/*.lock and locks/ directory
- **Build Files**: BUILD and BUILD_ROOT files throughout the repository structure

## Repository Structure

### Key Directories
```
pants-plugins/          # Custom Pants backend implementations
├── bitwarden/         # Bitwarden secrets integration
├── k8s/              # Kubernetes target types and rules (kubectl-based)
├── kustomize/        # Kustomize configuration management
├── mdbook/           # mdbook documentation builder 
├── oci/              # Container image building (umoci, runc, skopeo)
├── odin/             # Odin programming language support
└── secrets/          # General secrets management

examples/              # Example configurations and usage
├── bitwarden/        # Bitwarden integration examples
├── k8s/             # Kubernetes YAML examples
├── kustomize/       # Kustomize examples
├── mdbook/          # Documentation examples
├── oci/             # Container image build examples
└── odin/            # Odin language examples

docs/                 # Project documentation (mdbook source)
.github/workflows/    # CI/CD pipeline definitions
locks/                # Additional dependency lock files
```

### Important Files
- `pants.toml` -- Main Pants configuration with backend packages and Python settings
- `pyproject.toml` -- Python project configuration for formatting and linting
- `get-pants.sh` -- Pants installation script (takes ~25 seconds)
- `pre-commit.sh` -- Pre-commit validation workflow (runs full CI pipeline locally)
- `.github/workflows/main.yml` -- Primary CI pipeline
- `.github/workflows/docs.yml` -- Documentation building and deployment
- Lock files: `pants-plugins/*.lock` (pants.lock, current.lock, previous.lock, next.lock)
- Backend READMEs: Each pants-plugins subdirectory contains detailed documentation

## Common Tasks

### Adding New Backends
- Create new directory under `pants-plugins/`
- Add backend package to `backend_packages` in `pants.toml`
- Add to `pythonpath` in `pants.toml`
- Create appropriate lock files in `pants-plugins/` or `locks/`

### Troubleshooting
- **Download Failures**: Network timeouts are common - retry or document as limitation
- **Hanging Commands**: If a command hangs on downloads, wait 10+ minutes before canceling
- **Pants Process Conflicts**: If you get "Another pants invocation is running", kill with `pkill -f pants`
- **Lock File Issues**: Run `pants generate-lockfiles` to update dependencies
- **Build File Issues**: Run `pants tailor` to auto-generate BUILD files
- **Formatting Issues**: Run `pants fmt ::` to auto-fix formatting
- **Backend Loading Issues**: Run `pants help backends` to verify backends are properly loaded
- **Target Discovery**: Use `pants list <path>::` to find targets in specific directories

### Testing Changes
- **Unit Tests**: `pants test pants-plugins/<backend>::` for specific backend tests
- **Integration Tests**: `pants test examples/<backend>::` for example-based testing
- **Full Validation**: Run complete `./pre-commit.sh` workflow

## Backend-Specific Tools and Limitations

### OCI Backend (Container Images)
- **Tools**: umoci (image manipulation), runc (execution), skopeo (registry operations)
- **Limitations**: skopeo doesn't support MacOS, preventing pull/push on Mac
- **Note**: Multi-platform builds and tag pulling not fully supported for determinism

### Kubernetes Backend
- **Tool**: kubectl for cluster operations
- **Targets**: k8s_source, k8s_object, k8s_objects for raw YAML management
- **Usage**: Apply/delete/describe operations on Kubernetes resources

### MDBook Backend  
- **Tool**: mdbook for static documentation generation
- **Output**: HTML documentation in `dist/docs/book/`
- **Source**: Documentation sources in `docs/` directory

### Odin Backend
- **Tool**: Odin compiler for the Odin programming language
- **Large Download**: ~101MB compiler download required
- **Functionality**: Build, test, format, and lint Odin source code

### Secrets Backends
- **Bitwarden**: Integration with Bitwarden vault for secret management
- **Secrets**: General secret handling and encryption/decryption workflows
- **Goals**: decrypt goal available for secret operations

## Time Expectations
- **Initial Setup**: 25-60 seconds (pants installation + first run)
- **Dependency Downloads**: 5-10+ minutes (external tools and libraries)
- **Full Test Suite**: 15-30 minutes 
- **Full Package Build**: 30-60 minutes
- **Linting/Formatting**: 10-30 minutes
- **Documentation Build**: 5-15 minutes

**ALWAYS set timeouts to at least 2x the expected time and NEVER cancel long-running operations.**