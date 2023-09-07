import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest
import yaml


@pytest.fixture
def config():
    @contextmanager
    def wrapper(config):
        from mono2ledger.main import get_config

        get_config.cache_clear()
        with tempfile.TemporaryDirectory(prefix="mono2ledger") as config_dir:
            with mock.patch(
                "mono2ledger.main.os.getenv", return_value=config_dir
            ) as patch:
                config_file = Path(config_dir, "mono2ledger/config.yaml")
                config_file.parent.mkdir(parents=True)
                with config_file.open("w") as file:
                    file.write(yaml.dump(config))
                yield patch
        get_config.cache_clear()

    return wrapper


@pytest.fixture
def account_fetcher():
    # NOTE 2023-09-07: This ignores ignored_accounts settings
    @contextmanager
    def wrapper(accounts):
        with mock.patch("mono2ledger.main.fetch_accounts", return_value=accounts):
            yield

    return wrapper


@pytest.fixture
def statement_fetcher():
    # NOTE 2023-09-07: StatementItem's should have account set
    @contextmanager
    def wrapper(statements: list["StatementItem"]):
        with mock.patch("mono2ledger.main.fetch_statements", return_value=statements):
            yield

    return wrapper


@pytest.fixture
def main():
    def wrapper(args):
        import sys

        from mono2ledger.main import main as real_main

        old_argv = sys.argv
        try:
            sys.argv = ["executable"] + args
            real_main()
        finally:
            sys.argv = old_argv

    return wrapper
