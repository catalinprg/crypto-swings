import pytest
from src.config import load_config


def test_btc_config_has_expected_values():
    cfg = load_config("btc")
    assert cfg.asset == "btc"
    assert cfg.display_name == "BTC"
    assert cfg.symbol == "BTCUSDT"
    assert cfg.coinalyze_symbols == ("BTCUSDT_PERP.A", "BTCUSDT.6", "BTCUSDT_PERP.3")
    assert cfg.notion_parent_id == "345b7f28c0448048b7dce766dada1c29"


def test_eth_config_has_expected_values():
    cfg = load_config("eth")
    assert cfg.asset == "eth"
    assert cfg.display_name == "ETH"
    assert cfg.symbol == "ETHUSDT"
    assert cfg.coinalyze_symbols == ("ETHUSDT_PERP.A", "ETHUSDT.6", "ETHUSDT_PERP.3")
    assert cfg.notion_parent_id == "345b7f28c044805db2b9ea60a67be096"


def test_unknown_asset_raises():
    with pytest.raises(ValueError, match="unsupported ASSET"):
        load_config("doge")


def test_case_and_whitespace_tolerant():
    assert load_config("BTC").asset == "btc"
    assert load_config("  eth  ").asset == "eth"
