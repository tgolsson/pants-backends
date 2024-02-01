pants_requirements(name="pants", resolve="pants-plugins")

name_to_constraint = {
    "pants-previous": ">=2.18.0a0,<2.19.0",
    "pants-current": ">=2.19.0a0,<2.20.0",
    "pants-next": "==2.20.0.dev7",
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

__defaults__({
    python_sources: dict(resolve=parametrize(*all_targets)),
    python_tests: dict(
        resolve=parametrize(*all_targets[1:]),
    ),
})
