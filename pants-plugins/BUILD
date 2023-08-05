pants_requirements(name="pants", resolve="pants-plugins")

name_to_constraint = {
    "pants-215": ">=2.15.0a0,<2.16.0",
    "pants-216": ">=2.16.0a0,<2.17.0",
    "pants-217": "==2.17.0rc1",
}

# for name, constraint in name_to_constraint.items():
#     python_requirement(
#         name=name,
#         requirements=(
#             f"pantsbuild.pants.testutil{constraint}",
#             f"pantsbuild.pants{constraint}",
#             "pytest",
#         ),
#         modules=("pants.testutil", "pants", "pytest"),
#         resolve=name,
#     )

pants_requirements(name="pants-215", resolve="pants-215", version="2.15.0")
pants_requirements(name="pants-216", resolve="pants-216", version="2.16.0")
pants_requirements(name="pants-217", resolve="pants-217", version="2.17.0rc1")

all_targets = ("pants-plugins", *tuple(name_to_constraint.keys()))

__defaults__(
    {
        python_sources: dict(resolve=parametrize(*all_targets)),
        python_tests: dict(
            resolve=parametrize(*all_targets[1:]),
        ),
    }
)
