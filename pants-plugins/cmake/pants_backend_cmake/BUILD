python_sources(
    sources=["*.py", "!testutil.py", "!*_test.py", "!test_*.py", "!tests.py", "!conftest.py"]
)

python_tests(
    name="tests",
)

# Shared by the integration tests; kept out of the distribution.
python_test_utils(name="test_utils", sources=["testutil.py"])
