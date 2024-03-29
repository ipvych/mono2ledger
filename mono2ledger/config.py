import logging
import re
from collections import namedtuple
from pathlib import Path
from typing import Iterator, Optional

from pydantic import (
    BaseModel,
    Field,
    FilePath,
    RootModel,
    field_validator,
    model_validator,
)

# TODO: Can doc be generated from pydantic models?


class SettingsModel(BaseModel):
    ledger_date_format: str = "%Y/%m/%d"
    ignored_accounts: list[str] = []
    ledger_file: Optional[FilePath] = None
    transfer_payee: str = "Transfer"
    api_key_command: Optional[str] = None
    trim_leading_zeroes: bool = False
    record_cashback: bool = True
    cashback_payee: str = "Cashback"
    cashback_ledger_asset_account: str = "Assets:Mono2ledger:Cashback"
    cashback_ledger_income_account: str = "Income:Mono2ledger:Cashback"

    @field_validator("ledger_file", mode="before")
    def expand_ledger_file_path(cls, value: str):
        try:
            return Path(value).expanduser()
        except RuntimeError:
            return value

    @model_validator(mode="after")
    def check_ignored_accounts(cls, model: "SettingsModel"):
        defined_fields = model.model_fields_set
        if "ignored_accounts" not in defined_fields:
            logging.warning(
                """
                Ignored accounts are not set in config. It is recommended to set them
                so statements for unused accounts are not fetched and import is faster.
                If this is intended then this warning can be suppressed by setting value
                of 'ignored_accounts' to empty list in config.
            """
            )
        return model


class Account(BaseModel):
    ledger_account: str


class AccountsModel(RootModel):
    root: dict[str, Account] = {}

    def __getitem__(self, item):
        return self.root[item]


class MatcherValue(BaseModel):
    ignore: bool = False
    payee: Optional[str] = None  # Defaults to statementitem description
    ledger_account: Optional[str] = None
    source_ledger_account_suffix: str = ""


MatcherPredicateResult = namedtuple("MatcherPredicateResult", ["field", "result"])


class MatcherPredicate(BaseModel):
    mcc: list[int] = []
    description: Optional[str] = None

    def matches(self, statement: "StatementItem") -> Iterator[MatcherPredicateResult]:
        defined_fields = self.model_fields_set
        if "mcc" in defined_fields:
            yield MatcherPredicateResult(field="mcc", result=statement.mcc in self.mcc)
        if "description" in defined_fields:
            yield MatcherPredicateResult(
                field="description",
                result=(
                    self.description
                    and re.match(self.description, statement.description)
                ),
            )


class Matcher(BaseModel):
    value: MatcherValue = MatcherValue()
    predicate: MatcherPredicate
    submatchers: "MatchersModel" = []


class MatchersModel(RootModel):
    root: list[Matcher] = []

    def __iter__(self):
        return iter(self.root)

    def __getitem__(self, item):
        return self.root[item]


class ConfigModel(BaseModel):
    settings: SettingsModel = Field(default_factory=lambda: SettingsModel())
    accounts: AccountsModel = Field(default_factory=lambda: AccountsModel())
    matchers: MatchersModel = Field(default_factory=lambda: MatchersModel())

    def match_account(self, account_id: str, default=None) -> Account:
        """Return matching ledger account name for provided account id or default"""
        try:
            return self.accounts[account_id].ledger_account
        except KeyError:
            logging.warning(
                f"Could not find account definition for account with id {account_id}"
            )
            return default

    def _merge_values(
        self, first: dict | MatcherValue, second: dict | MatcherValue
    ) -> MatcherValue:
        # See https://stackoverflow.com/questions/60988674
        # for why check for not dict instead of MatcherValue
        if not isinstance(first, dict):
            first = first.model_dump(exclude_unset=True)
        if not isinstance(second, dict):
            second = second.model_dump(exclude_unset=True)
        return MatcherValue.model_validate(first | second)

    def _match_statement(
        self,
        statement: "StatementItem",
        matchers: list[Matcher],
        _current_value: dict | MatcherValue,
    ) -> MatcherValue:
        for matcher in matchers:
            if all(x.result for x in matcher.predicate.matches(statement)):
                current_value = self._merge_values(_current_value, matcher.value)
                if matcher.submatchers:
                    return self._match_statement(
                        statement, matcher.submatchers, current_value
                    )
                else:
                    logging.debug(
                        f"Matched statement {statement} with value {current_value}"
                    )
                    return current_value
        logging.debug(
            f"Matched statement {statement} with value {_current_value}",
        )
        return _current_value

    def match_statement(
        self, statement, default_value: Optional[dict | MatcherValue] = None
    ) -> MatcherValue:
        """
        Return MatcherValue from config that matches statement.

        Default matcher value will be provided values from which will be returned when
        no matcher is found with values to override provided ones.
        """
        return self._match_statement(
            statement, self.matchers, default_value if default_value else MatcherValue()
        )
