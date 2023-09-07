import re
from datetime import datetime, timedelta

import pytest


def assert_transaction_by_id(stdout, statement, account, amount_str):
    regex = re.compile(
        f"\n[0-9]{{,4}}[-|/][0-9]{{,2}}[-|/][0-9]{{,2}} .*\n"
        f"\tAssets:Mono2ledger:{account.id} +{amount_str}\n"
        f"\tExpenses:Mono2ledger:{account.id}:{statement.id}\n"
    )
    assert regex.search(stdout)


def test_ledger_account_required_without_config(config, main, caplog):
    with config({}), caplog.at_level("ERROR"), pytest.raises(SystemExit):
        main([""])
    assert (
        "You need to set location of ledger file in config"
        " or provide it in command line."
    ) in caplog.text


def test_statement_with_exchange(
    capsys, config, main, fetcher, ledger_file, account_factory, statement_factory
):
    now = datetime.now()
    last_transaction_date = now.date() - timedelta(days=1)
    account = account_factory(currencyCode=980)
    statement = statement_factory(
        account=account,
        currencyCode=840,  # USD
        time=now.timestamp(),
        amount=1000,
        operationAmount=100,
    )
    with (
        config({"settings": {"ledger_file": ledger_file(last_transaction_date)}}),
        fetcher(accounts=[account], statements=[statement]),
    ):
        main()

    assert_transaction_by_id(
        capsys.readouterr().out, statement, account, "10.00 UAH @@ 1.00 USD"
    )
