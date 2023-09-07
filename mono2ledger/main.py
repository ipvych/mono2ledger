import argparse
import io
import itertools
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Iterator, Optional, TextIO
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import yaml
from dateutil.relativedelta import relativedelta
from pycountry import currencies

from .config import ConfigModel, MatcherValue

Currency = list(currencies)[0].__class__


@cache
def get_config() -> ConfigModel:
    config_dir = os.getenv("XDG_CONFIG_HOME", "~/.config")
    config_file = Path(config_dir, "mono2ledger/config.yaml").expanduser()
    if not config_file.exists():
        logging.fatal("Config file for mono2ledger does not exist")
        exit(1)
    with config_file.open("rb") as file:
        return ConfigModel.model_validate(yaml.load(file, Loader=yaml.Loader))


@cache
def get_api_key() -> str:
    config_command = get_config().settings.api_key_command
    if command := (os.getenv("MONO2LEDGER_API_KEY_COMMAND") or config_command):
        proc = subprocess.Popen(
            shlex.split(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            logging.fatal("Could not retrieve API key using provided command.")
            exit(1)
        return stdout.decode().split("\n")[0]


class JSONObject:
    json: dict

    def __init__(self, json_data: str | dict | TextIO, **kwargs):
        if isinstance(json_data, dict):
            self.json = json_data
        elif isinstance(json_data, io.IOBase):
            self.json = json.load(json_data)
        else:
            self.json = json.loads(json_data)

        self.json |= kwargs

    def __getattr__(self, item: str):
        if "_" in item:
            items = item.split("_")
            items = [items[0]] + [x.capitalize() for x in items[1:]]
            return self.json["".join(items)]
        return self.json[item]

    def get(self, item, default=None):
        try:
            return self.__getattr__(item)
        except KeyError:
            return default

    def __repr__(self):
        return str(self.json)


class Account(JSONObject):
    pass


class StatementItem(JSONObject):
    account = None

    def __init__(self, json_data, account=None, **kwargs):
        self.account = account
        super().__init__(json_data, **kwargs)


def get_last_transaction_date(file: TextIO, default=None) -> datetime:
    """
    Return date of the last ledger transaction in file.
    """
    pattern = re.compile(r"\d{4}[/|-]\d{2}[/|-]\d{2}")
    comment_pattern = re.compile(r"^\s*[;#*]+")
    inside_comment = False
    result = None
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
        date_format = get_config().settings.ledger_date_format
        try:
            return datetime.strptime(result, date_format)
        except ValueError:
            logging.fatal(
                "Could not match date in ledger file using format set in config. "
                "Date is {result}, format is {date_format}"
            )
            exit(1)
    return default


def fetch(endpoint: str) -> dict:
    url = urljoin("https://api.monobank.ua", endpoint)
    request = Request(url, headers={"X-Token": get_api_key()})
    response = urlopen(request)
    return json.load(response.fp)


def fetch_accounts() -> list[Account]:
    response = fetch("/personal/client-info")
    logging.debug(f"Fetched accounts with response {response}")
    return [
        Account(account)
        for account in response["accounts"]
        if account["id"] not in get_config().settings.ignored_accounts
    ]


def date_range(
    start: datetime, end: datetime, interval: timedelta | relativedelta
) -> Iterator[tuple[datetime, datetime]]:
    delta = end - start
    if delta.days > 30:
        yield from date_range(start, end - interval, interval)
    yield end - delta, end


def fetch_statements(
    accounts: list[Account], from_time: datetime, to_time: datetime
) -> list[StatementItem]:
    intervals = date_range(from_time, to_time, relativedelta(months=1))
    combinations = list(itertools.product(accounts, intervals))
    for account, interval in combinations:
        _from_time, _to_time = interval
        try:
            response = fetch(
                "/personal/statement"
                f"/{account.id}"
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
                logging.fatal(
                    "Got unexpected response when fetching statement for account "
                    f"{account}. Response has status code {e.code} with content "
                    f"{e.read().decode()}"
                )
                exit(1)
        logging.debug(
            f"Fetched statement for account {account} with response {response}"
        )
        logging.info(
            f"Fetched statements for account {account.id}"
            f" from {_from_time.isoformat()} to {_to_time.isoformat()}."
            " Waiting 60 seconds before fetching another statement"
            " to obey API rate limit."
        )
        time.sleep(60)
        if len(response) < 500:
            yield from (StatementItem(x, account=account) for x in response)
        else:
            # NOTE 2023-07-16: This was never tested but in theory should work as
            # intended
            # TODO 2023-07-16: Actually test this with unit test
            period = timedelta(days=(_from_time - _to_time).days / 2)
            yield from fetch_statements(accounts, _from_time, _to_time - period)
            yield from fetch_statements(accounts, _from_time + period, _to_time)


def get_ledger_account_for_account(account: Account) -> str:
    return get_config().match_account(account.id, f"Assets:Mono2ledger:{account.id}")


def format_ledger_transaction(
    statement: StatementItem, source_statement: Optional[StatementItem] = None
) -> Iterator[str]:
    """Yield ledger transactions for statement that possibly came from source"""

    def format_amount(amount: float, pad: bool = True) -> str:
        amount = amount / 100
        if config.settings.trim_leading_zeroes and amount % 1 == 0:
            return f"{int(amount):8}" if pad else str(int(amount))
        return f"{amount:8.2f}" if pad else f"{amount:.2f}"

    config = get_config()
    exchange_amount = None
    exchange_currency = None
    if source_statement:
        payee = get_config().settings.transfer_payee
        to_account = get_ledger_account_for_account(statement.account)
        from_account = get_ledger_account_for_account(source_statement.account)
        amount = statement.amount
        if source_statement.amount != source_statement.operation_amount:
            exchange_amount = source_statement.amount
            exchange_currency = currencies.get(
                numeric=str(source_statement.account.currency_code)
            ).alpha_3
    else:
        match: MatcherValue = get_config().match_statement(statement)
        payee = match.payee if match.payee else statement.description
        from_account = (
            get_ledger_account_for_account(statement.account)
            + match.source_ledger_account_suffix
        )
        to_account = (
            match.ledger_account
            if match.ledger_account
            else f"Expenses:Mono2ledger:{statement.account.id}:{statement.id}"
        )
        amount = -statement.amount
        if statement.amount != statement.operation_amount:
            exchange_amount = statement.amount
            exchange_currency = currencies.get(
                numeric=str(statement.currency_code)
            ).alpha_3

    currency = currencies.get(numeric=str(statement.currency_code)).alpha_3
    if amount < 0:
        to_account, from_account = from_account, to_account
        amount = -amount
        if exchange_amount:
            exchange_amount = -exchange_amount

    transaction_date = datetime.fromtimestamp(statement.time).strftime(
        config.settings.ledger_date_format
    )

    exchange = (
        f"@@ {format_amount(-exchange_amount, pad=False)} {exchange_currency}"
        if exchange_amount and exchange_currency
        else ""
    )

    yield (
        f"{transaction_date} {payee}\n"
        f"\t{to_account:60} {format_amount(amount)} {currency} {exchange}\n"
        f"\t{from_account}"
    )

    if config.settings.record_cashback and (
        cashback_amount := statement.cashback_amount
    ):
        cashback_type = statement.account.cashback_type
        yield (
            f"{transaction_date} {config.settings.cashback_payee}\n"
            f"\t{config.settings.cashback_ledger_asset_account:60}"
            f" {format_amount(cashback_amount)} {cashback_type}\n"
            f"\t{config.settings.cashback_ledger_income_account}"
        )


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
    if debug:
        logging.root.setLevel(logging.DEBUG)
    else:
        logging.root.setLevel(logging.INFO)


def _main():
    parser = argparse.ArgumentParser(prog="mono2ledger")
    parser.add_argument(
        "input",
        type=argparse.FileType("r"),
        help="ledger file to obtain date of last transaction from",
    )
    parser.add_argument(
        "-D", "--debug", action="store_true", help="enable printing of debugging info"
    )
    args = parser.parse_args(sys.argv[1:])

    setup_logging(args.debug)

    now = datetime.now()
    last_transaction_date = get_last_transaction_date(
        args.input, now - timedelta(days=30)
    )

    accounts = fetch_accounts()

    def is_cross_card_statement(statement: StatementItem) -> bool:
        # 4829 is the MCC mono uses for card transfers
        if statement.mcc != 4829:
            return False
        if counter_iban := statement.get("counter_iban"):
            return counter_iban in (x.iban for x in accounts)

        description = statement.description
        has_card_type = False
        has_currency = False
        if "ФОП" in description or re.match(r".*(чорн|біл)(у|ої).*", description):
            has_card_type = True
        if re.match(r".*(гривне|євро|долар)(вий|вого).*", description):
            has_currency = True
        return has_card_type or has_currency

    def create_ledger_entries(statements: list[StatementItem]) -> Iterator[str]:
        """
        Given list of statements sort them by chronological order from newest to latest
        and yield ledger entry for each one with taking cross card statements into
        account by grouping them into single statement.

        Note that because of ordering before displaying returned value it needs to be
        reversed first.
        """

        def get_next(lst, index):
            try:
                return lst[index + 1]
            except IndexError:
                return None

        statements = sorted(statements, key=lambda x: (x.time, x.amount), reverse=True)
        for index, statement in enumerate(statements):
            if is_cross_card_statement(statement):
                current_statement = statement
                next_statement = get_next(statements, index)
                while (
                    next_statement
                    and current_statement.operation_amount
                    == -next_statement.operation_amount
                    and current_statement.currency_code == next_statement.currency_code
                    and current_statement.mcc == next_statement.mcc
                ):
                    current_statement = next_statement
                    del statements[index + 1]
                    next_statement = get_next(statements, index)

                # Yield from reversed because list will get reversed again messing up
                # order returned by format_ledger_transaction...
                yield from reversed(
                    list(format_ledger_transaction(statement, current_statement))
                )
            else:
                yield from reversed(list(format_ledger_transaction(statement)))

    statements = fetch_statements(accounts, last_transaction_date, now)
    ledger_entries = list(create_ledger_entries(statements))

    header_datetime = now.strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n;; Begin mono2ledger output\n;; Date and time: {header_datetime}\n"
    footer = "\n;; End mono2ledger output\n"

    print(header)
    print("\n\n".join(reversed(ledger_entries)))
    print(footer)


def main():
    try:
        _main()
    except KeyboardInterrupt as e:
        exit("Received interrupt, exiting")
        if logging.getLogger().level <= logging.DEBUG:
            raise e
