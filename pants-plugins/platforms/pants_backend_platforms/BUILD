python_sources(
    sources=["*.py", "!testutil.py", "!*_test.py", "!test_*.py", "!tests.py", "!conftest.py"]
)

# The toy backend the tests build with; kept out of the distribution.
python_test_utils(name="test_utils", sources=["testutil.py"])

python_tests(name="tests")
