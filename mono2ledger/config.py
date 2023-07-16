import logging
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

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
    transfer_payee: Optional[str] = "Transfer"
    api_key_command: Optional[str] = None
    trim_leading_zeroes: bool = True
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
        provided_fields = model.model_fields_set
        if "ignored_accounts" not in provided_fields:
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
    id: str
    ledger_account: str


class AccountsModel(RootModel):
    root: dict[str, Account] = {}

    def __getattr__(self, item):
        return self.root.get(item)

    def __getitem__(self, item):
        return self.root[item]

    def __iter__(self):
        return iter(self.root)

    def values(self):
        return self.root.values()

    def items(self):
        return self.root.items()


class MatcherValue(BaseModel):
    ignore: bool = False
    payee: Optional[str] = None  # Defaults to statementitem description
    # TODO: Should have a sane fallback when not provided by user
    ledger_account: Optional[str] = None


class MatcherPredicate(BaseModel):
    mcc: Optional[list[int]] = None
    description: Optional[str] = None
    from_time: Optional[datetime] = None
    to_time: Optional[datetime] = None

    @field_validator("from_time", mode="before")
    def validate_from_time(cls, value: str | datetime):
        try:
            return cls._validate_datetime(value, date.min, time.min)
        except ValueError as e:
            raise ValueError(f"from_time: {e}")

    @field_validator("to_time", mode="after")
    def validate_to_time(cls, value: str | datetime):
        try:
            return cls._validate_datetime(value, date.max, time.max)
        except ValueError as e:
            raise ValueError(f"to_time: {e}")

    @staticmethod
    def _validate_datetime(
        datetime_str: str | datetime, min_date: datetime, min_time: datetime
    ) -> datetime:
        if isinstance(datetime_str, datetime):
            return datetime_str
        period_date, *period_time = datetime_str.split("T")
        period_time = period_time[0] if period_time else None
        if period_date:
            year, month, day = re.search(
                r"([\d*]+)?-?([\d*]+)?-?([\d*]+)?", period_date
            ).groups()
            period_date = date(
                year=int(year) if year and year != "*" else min_date.year,
                month=int(month) if month and month != "*" else min_date.month,
                day=int(day) if day and day != "*" else min_date.day,
            )
        else:
            period_date = min_date
        if period_time:
            hour, minute = re.search(r"([\d*]+)?:?([\d*]+)?", period_time).groups()
            period_time = time(
                int(hour) if hour and hour != "*" else min_time.hour,
                int(minute) if minute and minute != "*" else min_time.minute,
            )
        else:
            period_time = min_time
        return datetime.combine(period_date, period_time)


class Matcher(BaseModel):
    value: Optional[MatcherValue] = None
    predicate: MatcherPredicate
    submatchers: Optional["MatchersModel"] = None


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

    def match_account(self, account_id, default=None) -> Account:
        """Return matching ledger account name for provided account id or default"""
        for key, value in self.accounts.items():
            if value.id == account_id:
                return value.ledger_account
        logging.warning(f"Could not find matching account with id {account_id}")
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
        # TODO: Fix ruff warning that annotation is invalid
        statement: "StatementItem",
        matchers: list[Matcher],
        _current_value: dict | MatcherValue,
    ) -> MatcherValue:
        for matcher in matchers:
            predicate = matcher.predicate
            if (
                (predicate.mcc and statement.mcc in predicate.mcc)
                or (
                    predicate.from_time
                    and statement.time >= predicate.from_time.timestamp()
                )
                or (
                    predicate.to_time
                    and statement.time <= predicate.to_time.timestamp()
                )
                or (
                    predicate.description
                    and re.match(predicate.description, statement.description)
                )
            ):
                current_value = self._merge_values(_current_value, matcher.value)
                if matcher.submatchers:
                    return self._match_statement(
                        statement, matcher.submatchers, current_value
                    )
                else:
                    logging.debug(
                        f"Statement {statement} was matched with value {current_value}"
                    )
                    return current_value
        logging.debug(
            f"Statement {statement} was matched with value {_current_value}",
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
