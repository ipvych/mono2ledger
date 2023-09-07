import pytest


def test_ledger_account_required_without_config(config, main, caplog):
    with config({}), caplog.at_level("ERROR"), pytest.raises(SystemExit):
        main([""])
    assert (
        "You need to set location of ledger file in config"
        " or provide it in command line."
    ) in caplog.text
