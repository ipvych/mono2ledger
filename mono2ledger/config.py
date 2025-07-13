import contextlib
import logging
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Iterator

_Unset = object


@dataclass(slots=True)
class Matcher:
    ledger_account: str | None = None
    payee: str | None = None
    source_ledger_account_suffix: str = ""
    mcc_match: int | list[int] | None = field(default_factory=list)
    description_regex: str | list[str] | None = field(default_factory=list)
    ignore: bool = False


@dataclass()
class Config:
    ledger_date_format: str = "%Y/%m/%d"
    ignored_accounts: list[str] | _Unset = _Unset
    ledger_file: str | None = None
    transfer_payee: str = "Transfer"
    api_key_command: str | None = None
    trim_leading_zeroes: bool = False
    record_cashback: bool = True
    cashback_payee: str = "Cashback"
    cashback_ledger_asset_account: str = "Assets:Mono2ledger:Cashback"
    cashback_ledger_income_account: str = "Income:Mono2ledger:Cashback"
    accounts: dict[str, str] = field(default_factory=dict)
    matchers: list[Matcher] = field(default_factory=list)

    @cached_property
    def api_key(self):
        if command := os.getenv("MONO2LEDGER_API_KEY_COMMAND", self.api_key_command):
            proc = subprocess.run(command, text=True, capture_output=True)
            if proc.returncode != 0:
                logging.error("Could not retrieve API key using provided command.")
                exit(1)
            return proc.stdout.split("\n")[0]


def get_config() -> Config:
    config_dir = os.getenv("XDG_CONFIG_HOME", "~/.config")
    config_file = Path(config_dir, "mono2ledger/config.toml").expanduser()
    if not config_file.exists():
        logging.error("Config file for mono2ledger does not exist")
        exit(1)
    with config_file.open("rb") as c:
        parsed = tomllib.load(c)

    config = Config(**parsed.get("config", {}))
    config.accounts = parsed.get("accounts", {})
    config.matchers = list(
        parse_matchers(parsed.get("categories", {}), parsed.get("match", []))
    )
    # Expand ledger file to full location if it is set
    if config.ledger_file:
        with contextlib.suppress(RuntimeError):
            config.ledger_file = Path(config.ledger_file).expanduser()
    if config.ignored_accounts == _Unset:
        logging.warning(
            "'ignored_accounts' is not set in config."
            " It is recommended to set this value so statements for unused accounts"
            " are not fetched making import is faster. If this is intended then this"
            " warning can be suppressed by setting value of 'ignored_accounts' to empty"
            " list in config."
        )
        config.ignored_accounts = []
    return config


def parse_matchers(
    categories: dict[str, dict], matchers: list[dict]
) -> Iterator[Matcher]:
    for m in matchers:
        if "category" in m:
            c = m["category"]
            del m["category"]
            v = Matcher(**categories[c] | m)
        else:
            v = Matcher(**m)
        v.mcc_match = ensure_list(v.mcc_match)
        v.description_regex = ensure_list(v.description_regex)
        for i, regex in enumerate(v.description_regex):
            try:
                v.description_regex[i] = re.compile(regex)
            except re.PatternError:
                logging.error(f"Invalid regex in matcher {m}")
                exit(1)
        yield v


def ensure_list(v: Any | list[Any]) -> list[Any]:
    return v if isinstance(v, list) else [v]
