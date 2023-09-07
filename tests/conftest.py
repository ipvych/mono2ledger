import random
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import factory
import pytest
import yaml
from faker import Faker
from mono2ledger.main import Account, StatementItem
from pytest_factoryboy import register as register_factory

fake = Faker()


@pytest.fixture
def config():
    @contextmanager
    def wrapper(config=None):
        from mono2ledger.main import get_config

        get_config.cache_clear()
        with tempfile.TemporaryDirectory(prefix="mono2ledger-") as config_dir:
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
def ledger_file():
    ledger_file = tempfile.NamedTemporaryFile("w", prefix="mono2ledger-")

    def wrapper(last_transaction_date="", extra=""):
        transaction_date = last_transaction_date or fake.date()
        transaction = extra + (
            f"\n{transaction_date} Payee\n"
            "\tExpenses:Foo  100 UAH\n"
            "\tAssets:Bar\n\n"
        )
        ledger_file.write(transaction)
        return ledger_file.name

    try:
        yield wrapper
    finally:
        pass
        # ledger_file.close()


@pytest.fixture
def fetcher():
    @contextmanager
    def wrapper(accounts: list[Account] = [], statements: list[StatementItem] = []):
        # NOTE 2023-09-07: This ignores ignored_accounts settings
        with mock.patch(
            "mono2ledger.main.fetch_accounts", return_value=accounts
        ), mock.patch("mono2ledger.main.fetch_statements", return_value=statements):
            yield

    return wrapper


@pytest.fixture
def main():
    def wrapper(args=[]):
        import sys

        from mono2ledger.main import main as real_main

        old_argv = sys.argv
        try:
            sys.argv = ["executable"] + args
            real_main()
        finally:
            sys.argv = old_argv

    return wrapper


def make_account(_):
    return {
        "id": fake.sha1(raw_output=False),
        "currencyCode": 970,
        "cashbackType": "UAH",
        "iban": fake.iban(),
    }


def make_statement(_):
    mcc = random.randint(1000, 9999)
    amount = random.randint(int(-1e6), int(1e6))

    return {
        "id": fake.sha1(raw_output=False),
        "time": fake.unix_time(),
        "description": fake.sentence(),
        "mcc": mcc,
        "originalMcc": mcc,
        "hold": False,
        "amount": amount,
        "operationAmount": amount,
        "currencyCode": random.choice((840, 980)),
        "commissionRate": 0,
        "cashbackAmount": random.randint(0, 50),
        "balance": amount,
        "counterIban": fake.iban(),
    }


@register_factory
class AccountFactory(factory.Factory):
    class Meta:
        model = Account

    json_data = factory.LazyAttribute(make_account)


@register_factory
class StatementFactory(factory.Factory):
    class Meta:
        model = StatementItem

    account = factory.LazyAttribute(lambda _: AccountFactory())
    json_data = factory.LazyAttribute(make_statement)
