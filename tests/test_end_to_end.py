import pytest
from tests.fixtures.ohlc_all_tfs import synthetic_all
from src import fetch as fetch_mod
from src import main as main_mod
from src import telegram_notify
from src import derivatives as derivatives_mod

@pytest.mark.asyncio
async def test_full_pipeline_produces_notion_payload_and_calls_telegram(monkeypatch):
    # Stub Binance
    async def fake_fetch_all():
        return synthetic_all()
    monkeypatch.setattr(fetch_mod, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(main_mod, "fetch_all", fake_fetch_all)

    # Capture Notion write
    captured_payload = {}
    async def fake_notion(payload):
        captured_payload.update(payload)
        return "https://notion.so/fake-page-id"
    monkeypatch.setattr(main_mod, "write_to_notion", fake_notion)

    # Stub Telegram
    sent = []
    async def fake_send(msg):
        sent.append(msg)
    monkeypatch.setattr(telegram_notify, "send", fake_send)
    monkeypatch.setattr(main_mod, "send_telegram", fake_send)

    # Stub derivatives
    async def fake_derivatives_fetch_all():
        return {
            "status": "ok",
            "open_interest_usd": 13_000_000_000.0,
            "open_interest_change_24h_pct": 2.5,
            "funding_rate_8h_pct": 0.01,
            "funding_rate_annualized_pct": 10.95,
            "predicted_funding_rate_8h_pct": 0.008,
            "liquidations_24h": {"long_usd": 1_000_000.0, "short_usd": 500_000.0, "dominant_side": "long"},
            "liquidation_clusters_72h": [],
            "venues_used": ["A", "6", "3"],
        }
    monkeypatch.setattr(derivatives_mod, "fetch_all", fake_derivatives_fetch_all)
    monkeypatch.setattr(main_mod.derivatives_mod, "fetch_all", fake_derivatives_fetch_all)

    exit_code = await main_mod.run()

    assert exit_code == 0
    assert "BTC Swings" in captured_payload["title"]
    assert captured_payload["parent_page_id"] == "345b7f28c0448048b7dce766dada1c29"
    assert len(sent) == 1
    assert "notion.so/fake-page-id" in sent[0]
    assert "derivatives" in captured_payload
    assert captured_payload["derivatives"]["status"] == "ok"
    assert captured_payload["derivatives"]["open_interest_usd"] == 13_000_000_000.0
