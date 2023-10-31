pants_requirements(name="pants", resolve="pants-plugins")

name_to_constraint = {
    "pants-previous": ">=2.16.0a0,<2.17.0",
    "pants-current": ">=2.17.0a0,<2.18.0",
    "pants-next": "==2.18.0a0",
}

for name, constraint in name_to_constraint.items():
    python_requirement(
        name=name,
        requirements=(
            f"pantsbuild.pants.testutil{constraint}",
            f"pantsbuild.pants{constraint}",
            "pytest",
        ),
        modules=("pants.testutil", "pants", "pytest"),
        resolve=name,
    )

all_targets = ("pants-plugins", *tuple(name_to_constraint.keys()))

__defaults__(
    {
        python_sources: dict(resolve=parametrize(*all_targets)),
        python_tests: dict(
            resolve=parametrize(*all_targets[1:]),
        ),
    }
)
