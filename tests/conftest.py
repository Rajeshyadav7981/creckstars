def pytest_addoption(parser):
    parser.addoption("--base-url", action="store", default="http://localhost:7981")
