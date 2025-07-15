import re
from datetime import datetime, timedelta

import pytest


def assert_transaction_by_id(stdout, statement, account, amount_str):
    regex = re.compile(
        f"\n[0-9]{{,4}}[-|/][0-9]{{,2}}[-|/][0-9]{{,2}} .*\n"
        f"\tAssets:Mono2ledger:{account["id"]} +{amount_str}\n"
        f"\tExpenses:Mono2ledger:{account["id"]}:{statement["id"]}\n"
    )
    assert regex.search(stdout)


def test_ledger_file_must_be_set(caplog, main):
    with pytest.raises(SystemExit) as excinfo:
        main(ledger_file=None)
    assert excinfo.value.code == 1
    assert (
        "You need to set location of ledger file in config"
        " or provide it in command line."
    ) in caplog.text


# def test_statement_with_commission(
#     capsys, main, account_factory, statement_factory
# ):
#     account = account_factory(currencyCode=980)
#     statement = statement_factory(account=account, commission=100)
#     with (
#         config({"settings": {"ledger_file": ledger_file(last_transaction_date)}}),
#         fetcher(accounts=[account], statements=[statement]),
#     ):
#         main()
#     assert_transaction_by_id(
#         capsys.readouterr().out, statement, account, "10.00 UAH @@ 1.00 USD"
#     )


def test_statement_with_exchange(capsys, main, account_factory, statement_factory):
    account = account_factory(currencyCode=980)  # UAH
    statement = statement_factory(
        account=account,
        currencyCode=840,  # USD
        amount=1000,
        operationAmount=100,
    )
    main(ledger_file="", accounts=[account], statements=[statement])

    assert_transaction_by_id(
        capsys.readouterr().out,
        statement,
        account,
        f"{statement["amount"] / 100:.2f} UAH @@ {statement["operationAmount"] / 100:.2f} USD",
    )
