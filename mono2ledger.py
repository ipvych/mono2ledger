#!/usr/bin/env python3

import hashlib
import os
import textwrap
import re
import configparser
import csv
import sys
import argparse
import datetime
from pathlib import Path
from collections import namedtuple

# TODO: StatementItem -> StatementEntry


# Datetime formed used in statement
STATEMENT_DATETIME_FORMAT = "%d.%m.%Y %H:%M:%S"
# Date format used by ledger
LEDGER_DATE_FORMAT = "%Y/%m/%d"
# Datetime format used in header
HEADER_DATETIME_FORMAT = "%Y/%m/%d %H:%M:%S"
# Datetime format used in backup file name
FILE_DATETIME_FORMAT = "%Y%m%d-%H%M%S"

INCOME_KEY = "income"
OUTCOME_KEY = "outcome"
PAYEE_KEY = "payee"

# Get config file from xdg config dir
config_file = os.path.join(
    os.getenv("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "mono2ledger/config.ini",
)

config = configparser.ConfigParser()
config.read(config_file)

cashback_account = config["DEFAULT"]["cashback_account"]
card_account = config["DEFAULT"]["card_account"]
ledger_file = os.path.expanduser(config["DEFAULT"]["ledger_file"])
backup_dir = os.path.expanduser(config["DEFAULT"]["backup_dir"])

# Create backup dir if it does not exist
if not os.path.isdir(backup_dir):
    os.makedirs(backup_dir)


def get_statement_item_config(item):
    """
    Return config data for provided statement item.
    Raise exception when there is none
    """
    for key in config:
        # Default key only holds settings, not interested in it
        if key == "DEFAULT":
            continue

        mcc = key.split("/")[0]
        regexp = "".join(key.split("/")[1:])

        if item.mcc == mcc and re.match(regexp, item.description):
            return config[key]

    raise ValueError(
        f"There is no matching config for entry {item.mcc} {item.description}"
    )


class StatementItem:
    """
    This represents single row in statement.
    """

    def account_name(self):
        """
        Return account name associated with this item.
        """
        config = get_statement_item_config(self)
        return (
            config[INCOME_KEY]
            if self.currency_amount > 0
            else config[OUTCOME_KEY]
        )

    def payee(self):
        """
        Return payee associated with this item.
        """
        payee = get_statement_item_config(self).get(PAYEE_KEY)
        if payee is None:
            payee = "UNKNOWN"
            print(f"Unknown payee for entry {self.mcc} {self.description}")
        return payee

    def _check_fields(self):
        """
        Assert that not yet supported fields dont have unexpected values
        """
        assert self.currency_amount == self.operation_amount
        assert self.exchange_rate == None
        assert self.currency == "UAH"

    def to_ledger(self):
        """
        Return ledger representation of this entry.
        """
        account = self.account_name()
        payee = self.payee()
        date = self.datetime.strftime(LEDGER_DATE_FORMAT)

        ret = "".join(
            (
                f"{date} * {payee}\n",
                f"    {account:60} {self.currency_amount:8} {self.currency}\n",
                f"    {cashback_account:60} {self.cashback:8} {self.currency}\n"
                if self.cashback
                else "",
                f"    {card_account}\n",
            )
        )

        return ret

    def __init__(self, row):
        def get_col(pos):
            """
            Return properly typed value from column, None if column is empty
            """
            col = row[pos]
            return col if col != "—" else None

        def float_if_not_none(val):
            """
            Return value converted to float if it is not None, None otherwise.
            """
            if val:
                return float(val)

        self.datetime = datetime.datetime.strptime(
            row[0], STATEMENT_DATETIME_FORMAT
        )
        self.description = get_col(1)
        self.mcc = get_col(2)
        self.currency_amount = float(get_col(3))
        self.operation_amount = float(get_col(4))
        self.currency = get_col(5)
        self.exchange_rate = float_if_not_none(get_col(6))
        self.commission = float_if_not_none(get_col(7))
        self.cashback = float_if_not_none(get_col(8))

        self._check_fields()

    def __str__(self):
        return "{} | {:30} | {:4} | {:6} | {:6} | {:2} | {:2} | {:4} | {:4}".format(
            self.datetime,
            self.description,
            self.mcc,
            self.currency_amount,
            self.operation_amount,
            self.currency,
            self.exchange_rate or "-",
            self.commission or "-",
            self.cashback or "-",
        )


def sha256_checksum(path, block_size=65536):
    "Return sha256 checksum of file at path"
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            sha256.update(block)
    return sha256.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert monobank csv statement to ledger transactions.",
    )
    parser.add_argument(
        "files",
        metavar="PATH",
        nargs="+",
        help="Path to file containing downloaded statement from monobank.",
        type=argparse.FileType(),
    )

    files = parser.parse_args().files
    for file in files:
        current_datetime = datetime.datetime.now()
        header_datetime = current_datetime.strftime(HEADER_DATETIME_FORMAT)
        file_datetime = current_datetime.strftime(FILE_DATETIME_FORMAT)
        file_path = os.path.abspath(file.name)
        sha256_sum = sha256_checksum(file_path)

        backup_file = f"{backup_dir}/backup-{Path(ledger_file).stem}-{file_datetime}.ledger"

        # Header and footer inserted before and after converter output
        header = (
            f"\n;; Begin converter output\n"
            f";; Date and time: {header_datetime}\n"
            f";; File: {file_path}\n"
            f";; SHA256: {sha256_sum}\n\n"
        )
        footer = ";; End converter output\n"

        reader = csv.reader(file)
        next(reader)  # Skip field titles
        items = [StatementItem(row) for row in reader]
        if items:
            # Backup ledger file
            with open(f"{backup_file}", "w") as bak:
                with open(f"{ledger_file}", "r") as orig:
                    print(f"Writing backup to {backup_file}")
                    bak.write(orig.read())

            with open(f"{ledger_file}", "a") as ledger:
                print(f"Writing entries to {ledger_file}")

                ledger.write(header)
                for item in items:
                    ledger.write(item.to_ledger() + "\n")

                ledger.write(footer)
