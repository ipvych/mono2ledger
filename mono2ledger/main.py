import argparse
import io
import itertools
import json
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
from .config import ConfigModel

Currency = list(currencies)[0].__class__

TRANSFER_MCC = 4829
now = datetime.now()


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


def fetch(endpoint: str) -> dict:
    url = urljoin("https://api.monobank.ua", endpoint)
    request = Request(url, headers={"X-Token": get_api_key()})
    response = urlopen(request)
    return json.load(response.fp)


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

    def __repr__(self):
        return str(self.json)


class StatementItem(JSONObject):
    account: "Account" = None

    @property
    def is_send(self):
        return self.operation_amount < 0

    @property
    def is_receive(self):
        return self.operation_amount > 0


class Account(JSONObject):
    _statements: Optional[list] = None

    def fetch_statements(self, from_time: datetime, to_time: datetime):
        if self._statements is not None:
            raise ValueError(
                "Statements are already fetched."
                " Fetching multiple times with different dates is not supported"
            )
        intervals = []
        if (to_time - from_time).days <= 30:
            intervals.append([from_time, to_time])
        else:
            current_interval = to_time - from_time
            current_time = from_time
            # TODO: These is probably a nicer way to do this with recursion
            while current_interval.days > 30:
                # This can do requests with current timestampt in the future but API
                # does not seem to care and neither do I
                interval = current_time + timedelta(days=30)
                intervals.append([current_time, interval])
                current_interval -= 30
                current_time = interval
        self._statements = []
        for from_time, to_time in intervals:
            try:
                response = fetch(
                    "/personal/statement"
                    f"/{self.id}"
                    f"/{int(from_time.timestamp())}"
                    f"/{int(to_time.timestamp())}"
                )
                # TODO: Better error handler - in particular throttling should be
                # handled well
            except HTTPError as e:
                print(e.read().decode())
                raise e
            self._statements += [StatementItem(x, account=self) for x in response]

    @property
    def statements(self) -> list[StatementItem]:
        if self._statements is None:
            raise ValueError(
                "You need to fetch statements by calling 'fetch_statements' first"
            )
        return self._statements

    def filter_statements(
        self, from_time: datetime = None, to_time: datetime = None, **kwargs
    ) -> Iterator[StatementItem]:
        if self._statements is None:
            raise ValueError(
                "You need to fetch statements by calling 'fetch_statements' first"
            )

        def matcher(x):
            predicates: list[bool] = [
                all(getattr(x, key) == value for key, value in kwargs.items())
            ]
            if from_time:
                predicates.append(x.time >= from_time)
            if to_time:
                predicates.append(x.time <= to_time)

            return all(predicates)

        return filter(matcher, self._statements)


def get_last_transaction_date(file: TextIO) -> datetime:
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
    if not result:
        warn(
            "Could not get last transaction date from file. "
            "Fetching transactions for last 30 days"
        )
        return now - timedelta(30)

    return datetime.strptime(result, get_config().settings.ledger_date_format)


def fetch_accounts() -> list[Account]:
    return [
        Account(account)
        for account in fetch("/personal/client-info")["accounts"]
        if account["id"] not in get_config().settings.ignored_accounts
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=argparse.FileType("r"))
    parser.add_argument("output", type=argparse.FileType("w"), nargs="?")
    args = parser.parse_args(sys.argv[1:])
    last_transaction_date = get_last_transaction_date(args.input)
    header_datetime = now.strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n;; Begin mono2ledger output\n;; Date and time: {header_datetime}\n"
    footer = ";; End mono2ledger output\n"

    accounts = fetch_accounts()
    for account in accounts:
        # TODO: Add indication that we are waiting. Just printing is kinda ugly
        # ngl, I want something better than this for loop
        account.fetch_statements(from_time=last_transaction_date, to_time=now)
        print(f"Fetched statement for {account.id}. Waiting 60 seconds for throttle")
        # TODO: Is there a cleaner way to do the same thing without accessing list?
        if account != accounts[-1]:
            time.sleep(60)

    def get_matching_account(**kwargs):
        return next(
            filter(
                lambda x: all(
                    getattr(x, key) == value for key, value in kwargs.items()
                ),
                accounts,
            ),
            None,
        )

    def guess_account(statement):
        description = statement.description
        # TODO: Just use conunterIban instead of coming up with new names
        if statement.recepient_iban:
            return get_matching_account(iban=statement.recepient_iban)
        if "ФОП" in description:
            card_type = "fop"
        elif matches := re.match(r".*(чорн|біл)(у|ої).*", description):
            matched_card = matches[1]
            if matched_card.startswith("чорн"):
                card_type = "black"
            elif matched_card.startswith("біл"):
                card_type = "white"

        if matches := re.match(r".*(гривне|євро|долар)(вий|вого).*", description):
            matched_currency = matches[1]
            currency = None
            if matched_currency.startswith("гривн"):
                currency = currencies.get(alpha_3="UAH")
            elif matched_currency.startswith("євро"):
                currency = currencies.get(alpha_3="EUR")
            elif matched_currency.startswith("долар"):
                currency = currencies.get(alpha_3="USD")
            if currency:
                return get_matching_account(currency=currency, type=card_type)

    def format_ledger_transaction(statement, source_statement=None):
        # TODO: get_account whihc will return account funds came from based on
        # account, maybe from config
        # TODO: get_to_account which will return appropriate account for expense
        # transaction. Make sure to take into account that there are not only expenses

        config = get_config()

        def int_if_no_decimal(num: float) -> int | float:
            """
            Convert num to integer if it's decimal point == 0 else return it unmodified
            """
            return int(num) if num % 1 == 0.0 else num

        exchange_amount = None
        exchange_currency = None
        if source_statement:
            payee = config["transfer_payee"]
            to_account = config["account"][statement.account.id]["name"]
            from_account = config["account"][source_statement.account.id]["name"]
            amount = statement.amount
            if source_statement.amount != source_statement.operation_amount:
                exchange_amount = source_statement.amount
                exchange_currency = source_statement.account.currency
        else:
            payee = f"{statement.description}"
            from_account = config["account"][statement.account.id]["name"]
            to_account = f"statement_id:{statement.id}"
            amount = -statement.amount
            if statement.amount != statement.operation_amount:
                exchange_amount = statement.amount
                exchange_currency = statement.currency

        if statement.is_receive:
            to_account, from_account = from_account, to_account
            amount = -amount
            exchange_amount = -exchange_amount

        exchange = (
            f" @@ {-exchange_amount} {exchange_currency.alpha_3}"
            if exchange_amount and exchange_currency
            else ""
        )

        # TODO: Trim zero does not have signed digits
        # TODO: When the transaction is income swap recepiend and from account
        return (
            f"{statement.time.strftime(config.settings.ledger_date_format)} {payee}\n"
            f"\t{to_account:60} {amount:8} {statement.account.currency.alpha_3}"
            f"{exchange}\n"
            f"\t{from_account}\n"
        )

    def find_source(statement):
        if statement.mcc == TRANSFER_MCC:
            if statement.is_send:
                filters = {"from_time": statement.time - timedelta(minutes=5)}
            else:
                filters = {"to_time": statement.time + timedelta(minutes=5)}

            filters |= {
                "mcc": TRANSFER_MCC,
                "currency": statement.currency,
                "operation_amount": -statement.operation_amount,
            }

            if account := (
                statement.account if statement.is_send else guess_account(statement)
            ):
                matched_statement = next(
                    account.filter_statements(**filters), statement
                )
                if matched_statement != statement:
                    return find_source(matched_statement)
        return statement

    def get_source(statement):
        if statement.mcc == TRANSFER_MCC and (account := guess_account(statement)):
            return find_source(statement, account)
        return statement

    statements = sorted(
        (statement for account in accounts for statement in account.statements),
        key=lambda x: (x.time, x.amount),
    )
    cross_card_statements = []
    normal_statements = []

    for statement in statements:
        account = guess_account(statement)
        source = find_source(statement)
        if source != statement:
            # Due to the way statements are sorted we know that the next matching
            # statement is the destination, and the source is always the same
            if result := next(
                filter(
                    lambda x: source in x[1] or statement in x[1],
                    enumerate(cross_card_statements),
                ),
                None,
            ):
                index = result[0]
                cross_card_statements[index].append(statement)
            else:
                cross_card_statements.append([statement, source])
        elif not account:
            normal_statements.append(statement)

    all_statements = sorted(
        itertools.chain(cross_card_statements, normal_statements),
        key=(
            lambda x: (x.time, x.amount)
            if not isinstance(x, list)
            else (x[0].time, x[0].amount)
        ),
    )
    print(header)

    for statement in all_statements:
        if isinstance(statement, list):
            sorted_cross_card_statement = sorted(
                statement, key=lambda x: (x.time, x.amount), reverse=True
            )
            print(
                format_ledger_transaction(
                    sorted_cross_card_statement[0], sorted_cross_card_statement[-1]
                )
            )
            pass
        else:
            print(format_ledger_transaction(statement))

    print(footer)
