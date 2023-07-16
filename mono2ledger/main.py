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

# TODO: Implement cache. Fetching takes too much time. Caching should be visible by
# user by showing a message with instruction on how to ignore cache as needed. Config
# option for this would also be quite nice
from pycountry import currencies

from .cli import err, info, warn
from .config import ConfigModel, MatcherValue

Currency = list(currencies)[0].__class__


@cache
def get_config() -> ConfigModel:
    config_dir = os.getenv("XDG_CONFIG_HOME", "~/.config")
    config_file = Path(config_dir, "mono2ledger/config.yaml").expanduser()
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
            err("Could not retrieve API key using provided command.")
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
        return datetime.strptime(result, get_config().settings.ledger_date_format)
    return default


def fetch(endpoint: str) -> dict:
    url = urljoin("https://api.monobank.ua", endpoint)
    request = Request(url, headers={"X-Token": get_api_key()})
    response = urlopen(request)
    return json.load(response.fp)


def fetch_accounts() -> list[Account]:
    return [
        Account(account)
        for account in fetch("/personal/client-info")["accounts"]
        if account["id"] not in get_config().settings.ignored_accounts
    ]


def fetch_statements(
    accounts: list[Account], from_time: datetime, to_time: datetime
) -> list[StatementItem]:
    intervals = []
    if (to_time - from_time).days <= 30:
        intervals.append((from_time, to_time))
    else:
        current_interval = to_time - from_time
        current_time = from_time
        # TODO: These is probably a nicer way to do this with recursion
        while current_interval.days > 30:
            # This can do requests with current timestampt in the future but API
            # does not seem to care and neither do I
            interval = current_time + timedelta(days=30)
            intervals.append((current_time, interval))
            current_interval -= 30
            current_time = interval

    datefmt = "%Y-%m-%d"
    combinations = list(itertools.product(accounts, intervals))
    for account, interval in combinations:
        from_time, to_time = interval
        try:
            response = fetch(
                "/personal/statement"
                f"/{account.id}"
                f"/{int(from_time.timestamp())}"
                f"/{int(to_time.timestamp())}"
            )
            # TODO: Better error handler - in particular throttling should be
            # handled well
        except HTTPError as e:
            print(e.read().decode())
            raise e
        logging.debug(
            f"Fetched statement for account {account} with response being {response}"
        )
        yield from (StatementItem(x, account=account) for x in response)
        info(
            f"Fetched statements for account {account.id}"
            f" from {from_time.strftime(datefmt)} to {to_time.strftime(datefmt)}."
        )
        if (account, interval) != combinations[-1]:
            info(
                "Waiting 60 seconds before fetching another statement"
                " to obey API rate limit."
            )
            time.sleep(60)


def get_ledger_account_for_account(account: Account) -> str:
    match = get_config().match_account(account.id)
    if not match:
        warn(
            "Could not find matching account definition for account with id", account.id
        )
        return f"Assets:Mono2ledger:{account.id}"
    return match


def format_transaction(
    date: datetime,
    payee: str,
    from_account: str,
    to_account: str,
    amount: float,
    currency: str,
    exchange_amount: float,
    exchange_currency: str,
) -> str:
    config = get_config()

    def format_amount(amount: float, pad: bool = True) -> str:
        if config.settings.trim_leading_zeroes and amount % 1 == 0:
            return f"{int(amount):8}" if pad else str(int(amount))
        return f"{amount:8.2f}" if pad else f"{amount:.2f}"

    if amount < 0:
        to_account, from_account = from_account, to_account
        amount = -amount

    exchange = (
        f" @@ {format_amount(-exchange_amount, pad=False)} {exchange_currency}"
        if exchange_amount and exchange_currency
        else ""
    )

    return (
        f"{date.strftime(config.settings.ledger_date_format)} {payee}\n"
        f"\t{to_account:60} {format_amount(amount)} {currency}"
        f"{exchange}\n"
        f"\t{from_account}\n"
    )


def format_ledger_transaction(
    statement: StatementItem, source_statement: Optional[StatementItem] = None
) -> str:
    exchange_amount = None
    exchange_currency = None
    if source_statement:
        payee = get_config().settings.transfer_payee
        to_account = get_ledger_account_for_account(statement.account)
        from_account = source_statement.account
        amount = statement.amount
        if source_statement.amount != source_statement.operation_amount:
            exchange_amount = source_statement.amount / 100
            exchange_currency = currencies.get(
                numeric=str(source_statement.account.currency_code)
            ).alpha_3
    else:
        match: MatcherValue = get_config().match_statement(statement)
        payee = match.payee if match.payee else statement.description
        from_account = statement.account
        to_account = (
            match.ledger_account
            if match.ledger_account
            else f"Expenses:Mono2ledger:{statement.account.id}:{statement.id}"
        )
        amount = -statement.amount
        if statement.amount != statement.operation_amount:
            exchange_amount = statement.amount / 100
            exchange_currency = currencies.get(
                numeric=str(statement.currency_code)
            ).alpha_3

    from_account = get_ledger_account_for_account(from_account)
    currency = currencies.get(numeric=str(statement.currency_code)).alpha_3
    return format_transaction(
        datetime.fromtimestamp(statement.time),
        payee,
        from_account,
        to_account,
        amount / 100,
        currency,
        exchange_amount,
        exchange_currency,
    )


def setup_logging(level: str) -> None:
    logging.basicConfig(format="%(levelname)s: %(message)s")
    logging.getLogger().setLevel(level)


def _main():
    parser = argparse.ArgumentParser(prog="mono2ledger")
    parser.add_argument("input", type=argparse.FileType("r"))
    parser.add_argument("output", type=argparse.FileType("w"), nargs="?")
    parser.add_argument("-l", "--log_level", type=str, required=False, default="INFO")
    args = parser.parse_args(sys.argv[1:])

    setup_logging(args.log_level)

    now = datetime.now()
    last_transaction_date = get_last_transaction_date(
        args.input, now - timedelta(days=30)
    )

    header_datetime = now.strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n;; Begin mono2ledger output\n;; Date and time: {header_datetime}\n"
    footer = ";; End mono2ledger output\n"

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

                yield format_ledger_transaction(statement, current_statement)
            else:
                yield format_ledger_transaction(statement)

    statements = fetch_statements(accounts, last_transaction_date, now)
    ledger_entries = list(create_ledger_entries(statements))

    print(header)
    print("\n".join(reversed(ledger_entries)))
    print(footer)


def main():
    try:
        _main()
    except KeyboardInterrupt as e:
        exit("Received interrupt, exiting")
        if logging.getLogger().level <= logging.DEBUG:
            raise e
