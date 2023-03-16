pants_requirements(name="pants", resolve="pants-plugins")

name_to_constraint = {
    "pants-214": ">=2.14.0a0,<2.15.0",
    "pants-215": ">=2.15.0a0,<2.16.0",
    "pants-216": "==2.16.0a0",
}

for name, constraint in name_to_constraint.items():
    python_requirement(
        name=name,
        requirements=(
            f"pantsbuild.pants.testutil{constraint}",
            f"pantsbuild.pants{constraint}",
        ),
        modules=("pants.testutil", "pants"),
        resolve=name,
    )

all_targets = (*tuple(name_to_constraint.keys()), "pants-plugins")
__defaults__(
    {
        python_sources: dict(resolve=parametrize(*all_targets)),
        python_tests: dict(
            resolve=parametrize(*all_targets[1:])
        ),  # we don't parametrize test for host... assume test with ::
    }
)
