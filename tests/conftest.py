import random
import tempfile
from unittest import mock

import factory
import pytest
from faker import Faker
from mono2ledger.config import Config
from mono2ledger.main import Account, StatementItem
from mono2ledger.main import main as real_main
from pytest_factoryboy import register as register_factory

fake = Faker()


@pytest.fixture
def main():
    def wrapper(
        *argv,
        config=None,
        accounts=None,
        statements=None,
        ledger_file=None,
    ):
        config_obj = Config(**config if config else {})
        if statements is None:
            statements = []
        if accounts is None:
            accounts = []

        def raise_valueerror():
            raise ValueError

        with (
            mock.patch("mono2ledger.main.get_config", return_value=config_obj),
            mock.patch("mono2ledger.main.fetch_accounts", return_value=accounts),
            mock.patch("mono2ledger.main.fetch_statements", return_value=statements),
            # Make sure fetch mocks above are overwriting real fetch call
            mock.patch("mono2ledger.main.fetch", side_effect=raise_valueerror),
            mock.patch("sys.argv", ("mono2ledger",) + argv),
        ):

            if ledger_file is not None:
                with tempfile.NamedTemporaryFile("w") as f:
                    f.write(ledger_file)
                    config_obj.ledger_file = f.name
                    real_main()
            else:
                real_main()

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
