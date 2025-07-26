import argparse
import itertools
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Iterator, Optional
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from mono2ledger.config import Matcher, get_config

try:
    from pycountry import currencies
except ImportError:
    logging.warning(
        "pycountry optional dependency is not installed."
        " Some currencies may not be resolved correctly."
    )

    class currencies:
        @staticmethod
        def get(*, numeric: str):
            alpha = {
                "980": "UAH",
                "978": "EUR",
                "840": "USD",
            }.get(numeric, numeric)
            return type("currency", (), {"alpha_3": alpha})


# Globals set inside main function
config = None


# Subclasses from dict for typing purposes
class Account(dict):
    pass


class StatementItem(dict):
    # Set manually to refer to account which this statement is part of
    account: Account


def get_last_transaction_date(file_path: str, default=None) -> datetime:
    """
    Return date of the last ledger transaction in file.
    """
    pattern = re.compile(r"\d{4}[/|-]\d{2}[/|-]\d{2}")
    comment_pattern = re.compile(r"^\s*[;#*]+")
    inside_comment = False
    result = None
    with open(file_path, "r") as file:
        for line in file.readlines():
            # Exclude hledger multi-line comments
            if not inside_comment and line == "comment\n":
                inside_comment = True
            elif inside_comment and line == "end comment\n":
                inside_comment = False
            if (
                not inside_comment
                and not comment_pattern.match(line)
                and (match := pattern.findall(line))
            ):
                result = match[0]

    if result:
        date_format = config.ledger_date_format
        try:
            return datetime.strptime(result, date_format)
        except ValueError:
            logging.error(
                "Could not match date in ledger file using format set in config. "
                "Date is {result}, format is {date_format}"
            )
            exit(1)
    return default


def fetch(endpoint: str) -> dict:
    url = urljoin("https://api.monobank.ua", endpoint)
    request = Request(url, headers={"X-Token": config.api_key})
    logging.debug("Making request to URL %s", url)
    with urlopen(request) as response:
        data = json.loads(response.fp.read().decode("utf-8"))
        logging.debug("Got JSON response %s", data)
        return data


def fetch_accounts() -> list[Account]:
    response = fetch("/personal/client-info")
    return [
        Account(**account)
        for account in response["accounts"]
        if account["id"] not in config.ignored_accounts
    ]


def date_range(
    start: datetime, end: datetime, interval: timedelta
) -> Iterator[tuple[datetime, datetime]]:
    while start + interval < end:
        yield start, start + interval
        start = start + interval
    yield start, end


def fetch_statements(
    accounts: list[Account], from_time: datetime, to_time: datetime
) -> list[StatementItem]:
    intervals = date_range(from_time, to_time, timedelta(days=31))
    combinations = list(itertools.product(accounts, intervals))
    for account, interval in combinations:
        _from_time, _to_time = interval
        try:
            response = fetch(
                "/personal/statement"
                f"/{account["id"]}"
                f"/{int(_from_time.timestamp())}"
                f"/{int(_to_time.timestamp())}"
            )
        except HTTPError as e:
            if e.code == 429:
                logging.info(
                    "Encountered rate limit while fetching statement for account"
                    f" {account} from {from_time.isoformat()} to {to_time.isoformat()}."
                    " Retrying after 60 seconds"
                )
                time.sleep(60)
                yield from fetch_statements(accounts, from_time, to_time)
            else:
                logging.error(
                    "Got unexpected response when fetching statement for account "
                    f"{account}. Response has status code {e.code} with content "
                    f"{e.read().decode()}"
                )
                exit(1)
        logging.info(
            f"Fetched statements for account {account["id"]}"
            f" from {_from_time.date().isoformat()} to {_to_time.date().isoformat()}."
            " Waiting 60 seconds before fetching another statement"
            " to obey API rate limit."
        )
        time.sleep(60)
        if len(response) < 500:
            yield from (StatementItem(**x | {"account": account}) for x in response)
        else:
            # NOTE 2023-07-16: This was never tested but in theory should work as
            # intended
            # TODO 2023-07-16: Actually test this with unit test
            period = timedelta(days=(_from_time - _to_time).days / 2)
            yield from fetch_statements(accounts, _from_time, _to_time - period)
            yield from fetch_statements(accounts, _from_time + period, _to_time)


def get_ledger_account_for_account(account: Account) -> str:
    return config.accounts.get(account["id"], f"Assets:Mono2ledger:{account["id"]}")


def match_statement(statement: StatementItem) -> Matcher:
    rv = Matcher()
    for matcher in config.matchers:
        if (
            any(x.match(statement["description"]) for x in matcher.description_regex)
            or statement["mcc"] in matcher.mcc_match
        ):
            rv = matcher
            break

    if rv.ledger_account is None:
        rv.ledger_account = (
            f"Expenses:Mono2ledger:{statement["account"]["id"]}:{statement["id"]}"
        )
    if rv.payee is None:
        rv.payee = statement["description"]
    return rv


def format_ledger_transaction(statement: StatementItem) -> Iterator[str]:
    """Return ledger transaction for provided statement."""

    def format_amount(amount: float, pad: bool = True) -> str:
        amount = amount / 100
        if config.trim_leading_zeroes and amount % 1 == 0:
            return f"{int(amount):8}" if pad else str(int(amount))
        return f"{amount:8.2f}" if pad else f"{amount:.2f}"

    exchange_amount = None
    exchange_currency = None
    if source_statement := statement.get("start_statement"):
        payee = config.transfer_payee
        to_account = get_ledger_account_for_account(statement["account"])
        from_account = get_ledger_account_for_account(source_statement["account"])
        amount = statement["amount"]
        if source_statement["amount"] != source_statement["operationAmount"]:
            exchange_amount = source_statement["amount"]
            exchange_currency = currencies.get(
                numeric=str(source_statement["account"]["currencyCode"])
            ).alpha_3
    else:
        match = match_statement(statement)
        payee = match.payee
        from_account = (
            get_ledger_account_for_account(statement["account"])
            + match.source_ledger_account_suffix
        )
        to_account = match.ledger_account
        amount = -statement["amount"]
        if statement["amount"] != statement["operationAmount"]:
            exchange_amount = statement["operationAmount"]
            exchange_currency = currencies.get(
                numeric=str(statement["currencyCode"])
            ).alpha_3

    if amount < 0:
        to_account, from_account = from_account, to_account
        amount = -amount
        if exchange_amount:
            exchange_amount = -exchange_amount

    transaction_date = datetime.fromtimestamp(statement["time"]).strftime(
        config.ledger_date_format
    )

    exchange = (
        f" @@ {format_amount(-exchange_amount, pad=False)} {exchange_currency}"
        if exchange_amount and exchange_currency
        else ""
    )

    currency = currencies.get(
        numeric=str(
            statement["account"]["currencyCode"]
            if exchange
            else statement["currencyCode"]
        )
    ).alpha_3

    rv = (
        f"{transaction_date} {payee}\n"
        f"\t{to_account:60} {format_amount(amount)} {currency}{exchange}\n"
        f"\t{from_account}"
    )

    if config.record_cashback and (cashback_amount := statement["cashbackAmount"]):
        cashback_type = statement["account"]["cashbackType"]
        rv += (
            "\n\n"
            f"{transaction_date} {config.cashback_payee}\n"
            f"\t{config.cashback_ledger_asset_account:60}"
            f" {format_amount(cashback_amount)} {cashback_type}\n"
            f"\t{config.cashback_ledger_income_account}"
        )
    return rv


def merge_cross_card_statements(
    statements: list[StatementItem],
) -> Iterator[StatementItem]:
    """Sort statements by time and yield them, merging multiple statements that are
    between accounts into a single statement.

    Merged statement has `start_statement` key set containing starting statement
    from which info like originating currency & amount can be deduced.
    """
    start_statement: StatementItem = None
    end_statement: StatementItem = None
    # If description & mcc matches receiving end and there is no iban/name then
    # it is a end transaction
    # If mcc & iban/name match existing account then it is sending transaction and
    # some heuristic using description is used to determine which one is beginning
    # one
    for statement in sorted(statements, key=lambda x: x["time"]):
        # 4829 is the MCC used for card transfers
        if statement["mcc"] == 4829:
            description = statement["description"]
            if "counterIban" not in statement:
                if re.match(
                    "З (гривне|євро|доларо)вого рахунку ФОП", description
                ) or re.match("З (чорн|біл)ої картки", description):
                    end_statement = statement
                    # TODO: Maybe match counterIban by account list. For now it is fine
                    # without it but might be useful when I need to handle non-FOP
                    # transfers
            # TODO: This does not match non-FOP currency cards
            elif re.match(
                "На гривневий рахунок ФОП для переказу на картку", description
            ):
                start_statement = statement
            elif re.match("На (чорн|біл)у картку", description):
                if not end_statement:
                    end_statement = statement
            # Transitive statement that should be skipped
            elif re.match(
                "З (гривне|євро|доларо)вого рахунку ФОП для переказу на картку",
                description,
            ):
                pass
            # When nothing matches this is likely a transfer to outside card which
            # uses 4829 MCC as well
            else:
                yield statement
        else:
            if start_statement and end_statement:
                end_statement["start_statement"] = start_statement
                yield end_statement
                start_statement = end_statement = None
            yield statement
    # Handle case where last statement is cross statement
    if start_statement and end_statement:
        end_statement["start_statement"] = start_statement
        yield end_statement


def setup_logging(debug: bool = False) -> None:
    class Formatter(logging.Formatter):
        def format(self, record):
            if record.levelno == logging.INFO:
                fmt = "%(message)s"
            else:
                color = {
                    logging.WARNING: 33,
                    logging.ERROR: 31,
                    logging.FATAL: 31,
                    logging.DEBUG: 36,
                }.get(record.levelno)
                fmt = f"\033[{color}m%(levelname)s\033[0m: %(message)s"
            formatter = logging.Formatter(fmt)
            return formatter.format(record)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(Formatter())
    logging.root.addHandler(handler)


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="mono2ledger")
    parser.add_argument(
        "input",
        help="ledger file to obtain date of last transaction from",
        default=config.ledger_file,
        nargs="?",
    )
    parser.add_argument(
        "-D",
        "--debug",
        action="store_true",
        help="print JSON responses received from API",
    )
    args = parser.parse_args(argv[1:])
    if not args.input:
        logging.error(
            "You need to set location of ledger file in config"
            " or provide it in command line."
        )
        exit(1)
    return args


def run(argv):
    setup_logging()
    global config
    config = get_config()
    args = parse_args(argv)
    logging.root.setLevel(logging.DEBUG if args.debug else logging.INFO)

    now = datetime.now()
    last_transaction_date = get_last_transaction_date(
        args.input, now - timedelta(days=30)
    )
    statements = fetch_statements(fetch_accounts(), last_transaction_date, now)

    header_datetime = now.strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n;; Begin mono2ledger output\n;; Date and time: {header_datetime}\n"
    footer = "\n;; End mono2ledger output\n"

    print(header)
    for statement in merge_cross_card_statements(statements):
        print(format_ledger_transaction(statement), end="\n\n")
    print(footer)


def main():
    try:
        run(sys.argv)
    except KeyboardInterrupt as e:
        exit("Received interrupt, exiting")
        if logging.getLogger().level <= logging.DEBUG:
            raise e


if __name__ == "__main__":
    main()
