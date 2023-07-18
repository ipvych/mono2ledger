# mono2ledger
This is python script that I use to convert bank statement from
[monobank](https://monobank.ua/) to [ledger-cli](https://www.ledger-cli.org/)
entries (hledger works as well).

## Usage:
Call command with ledger file which contains your transactions as
input, mono2ledger will obtain date of last transaction from that file
and then fetch ledger statements for all accounts from API from date
of last transaction up untill now and print them to stdout as ledger
journal entries with cashback being recorded as separate entry and
cross card statements being recorded as transfer from one account to
another.

API key can be provided via environment variable
`MONO2LEDGER_API_KEY_COMMAND` which should be a command that when run
will return a text separated with newlines from which first line will
be used as API key.

The way mono2ledger converts statement items returned by API to ledger
journal entries as well as generall quality of life settings like
setting API key and ledger file location once to not repeat it all the
time can be set in YAML config file located at
`$XDG_CONFIG_HOME/mono2ledger/config.yaml` or
`~/.config/mono2ledger/config.yaml` if `XDG_CONFIG_HOME` is not
defined. For example on how to write config file with documentation
of available options see example config file at
[](doc/config.yaml.example)
