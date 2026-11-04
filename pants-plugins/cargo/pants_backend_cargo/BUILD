python_sources(sources=["*.py", "!testing.py", "!*_test.py", "!test_*.py", "!tests.py", "!conftest.py"])

# The rule runner the tests share; kept out of the distribution.
python_test_utils(name="test_utils", sources=["testing.py"])

python_tests(
    name="tests",
)
