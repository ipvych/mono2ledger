import re
from datetime import datetime

import pytest


def assert_transaction_printed(stdout, account_from, account_to, amount_str):
    regex = re.compile(
        f"\n[0-9]{{,4}}[-|/][0-9]{{,2}}[-|/][0-9]{{,2}} .*\n"
        f"\t{re.escape(account_from)} +{re.escape(amount_str)}\n"
        f"\t{re.escape(account_to)}\n"
    )
    if not regex.search(stdout):
        pytest.fail(
            f"Stdout did not match regexp\nStdout: {stdout}\nRegex: {regex.pattern}"
        )


def test_ledger_file_must_be_set(caplog, main):
    with pytest.raises(SystemExit) as excinfo:
        main(ledger_file=None)
    assert excinfo.value.code == 1
    assert (
        "You need to set location of ledger file in config"
        " or provide it in command line."
    ) in caplog.text


def test_statement_with_exchange(
    faker, capsys, main, account_factory, statement_factory
):
    account = account_factory(currencyCode=980)  # UAH
    amount = faker.pyint()
    currency_amount = faker.pyint()
    statement = statement_factory(
        account=account,
        currencyCode=840,  # USD
        amount=amount,
        operationAmount=currency_amount,
    )
    main(ledger_file="", accounts=[account], statements=[statement])

    assert_transaction_printed(
        capsys.readouterr().out,
        f"Assets:Mono2ledger:{account["id"]}",
        f"Expenses:Mono2ledger:{account["id"]}:{statement["id"]}",
        f"{statement["amount"] / 100:.2f} UAH @@ {statement["operationAmount"] / 100:.2f} USD",
    )


def test_cross_card_statement(faker, capsys, main, account_factory, statement_factory):
    now = datetime.now().timestamp()
    account_source = account_factory(currencyCode=978)  # EUR
    account_transitive = account_factory(currencyCode=980)  # UAH
    account_destination = account_factory(currencyCode=980)  # UAH
    # amount is amount in currency of account
    # operationAmount is amount in currency of transaction
    # currencyCode is currency of destination account when sending or of source account
    # when receiving
    amount = faker.pyint()
    currency_amount = faker.pyint()
    statement_source = statement_factory(
        time=now - 1,
        description="На гривневий рахунок ФОП для переказу на картку",
        mcc=4829,
        account=account_source,
        currencyCode=account_transitive["currencyCode"],
        amount=-currency_amount,
        operationAmount=-amount,
    )
    statement_transitive_in = statement_factory(
        time=now - 2,
        description="З єврового рахунку ФОП для переказу на картку",
        mcc=4829,
        account=account_transitive,
        amount=amount,
        operationAmount=currency_amount,
    )
    statement_transitive_out = statement_factory(
        time=now - 3,
        description="На чорну картку",
        mcc=4829,
        account=account_transitive,
        currencyCode=account_destination["currencyCode"],
        amount=-amount,
        operationAmount=-amount,
    )
    statement_destination = statement_factory(
        time=now - 4,
        description="З гривневого рахунку ФОП",
        mcc=4829,
        account=account_destination,
        currencyCode=account_transitive["currencyCode"],
        amount=amount,
        operationAmount=amount,
    )
    # destination statement does not have counterIban in my observation
    del statement_destination["counterIban"]
    main(
        ledger_file="",
        accounts=[account_source, account_transitive, account_destination],
        statements=[
            statement_source,
            statement_transitive_in,
            statement_transitive_out,
            statement_destination,
        ],
    )
    assert_transaction_printed(
        capsys.readouterr().out,
        f"Assets:Mono2ledger:{account_destination["id"]}",
        f"Assets:Mono2ledger:{account_source["id"]}",
        f"{statement_destination["amount"] / 100:.2f} UAH @@ {-statement_source["amount"] / 100:.2f} EUR",
    )
