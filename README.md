# mono2ledger
This is python script that I use to convert bank statement from
[monobank](https://monobank.ua/) to [ledger-cli](https://www.ledger-cli.org/)
entries (hledger works as well).

## Usage:
Invoke by running:
```sh
MONO2LEDGER_API_KEY_COMMAND="echo yourapikey" ./mono2ledger.py <your ledger file>
```
`MONO2LEDGER_API_KEY_COMMAND` should be a command that when executed will print
your api key to stdout in first line.

Behavior, including providing defaults for command above can be configured using
config file in `$XDG_CONFIG_HOME/mono2ledger/config.yaml` or
`~/.config/mono2ledger/config.yaml` if `XDG_CONFIG_HOME` environment variable is
not set. Sample config file with comments describing what each option does can
be found in [](config.toml)
