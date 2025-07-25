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

fake = Faker("uk_UA")


@pytest.fixture(scope="session", autouse=True)
def faker_session_locale():
    return ["uk_UA"]


@pytest.fixture(scope="session", autouse=True)
def faker_session_seed():
    return random.randint(0, 999999)


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


@register_factory
class AccountFactory(factory.Factory):
    class Meta:
        model = Account

    id = factory.Faker("sha1", raw_output=False)
    currencyCode = 970
    cashbackType = "UAH"
    iban = factory.Faker("iban")


@register_factory
class StatementFactory(factory.Factory):
    class Meta:
        model = StatementItem

    account = factory.LazyAttribute(lambda _: AccountFactory())
    id = factory.Faker("sha1", raw_output=False)
    time = factory.Faker("unix_time")
    description = factory.Faker("sentence")
    mcc = factory.Faker("random_int", min=1000, max=9999)
    originalMcc = factory.SelfAttribute("mcc")
    amount = factory.Faker("random_int", min=int(-1e6), max=int(1e6))
    operationAmount = factory.SelfAttribute("amount")
    currencyCode = factory.Faker("random_element", elements=[840, 980])
    cashbackAmount = factory.Faker("random_int", min=0, max=50)
    counterIban = factory.Faker("iban")
    # TODO: Not handled
    # hold = False
    # commissionRate = 0
