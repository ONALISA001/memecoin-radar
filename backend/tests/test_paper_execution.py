"""Paper-trading cost model: fills must always cost the trader, never pay them."""
import pytest

from app.paper.execution import buy_fill, fee_usd, sell_fill, slippage_pct

CFG = {"base_slippage_pct": 1.0, "half_spread_pct": 0.0, "impact_weight": 0.6,
       "impact_cap_pct": 6.0, "vol_weight": 0.02, "vol_cap_pct": 2.0, "fee_pct": 0.3}


def test_fee_is_percent_of_notional():
    assert fee_usd(1000, CFG) == pytest.approx(3.0)


def test_buy_pays_above_reference_and_fee_reduces_qty():
    f = buy_fill(1.0, 100.0, 500_000, 0.0, CFG)
    assert f["exec_price"] > 1.0
    assert f["fee"] == pytest.approx(0.3)
    assert f["qty"] == pytest.approx((100.0 - 0.3) / f["exec_price"])


def test_sell_receives_below_reference():
    f = sell_fill(1.0, 100.0, 500_000, 0.0, CFG)
    assert f["exec_price"] < 1.0
    assert f["proceeds"] < 100.0


def test_round_trip_at_flat_price_always_loses():
    b = buy_fill(2.0, 500.0, 200_000, 5.0, CFG)
    s = sell_fill(2.0, b["qty"], 200_000, 5.0, CFG)
    assert s["proceeds"] < 500.0


def test_thinner_pool_means_more_slippage():
    deep = slippage_pct(1000, 1_000_000, 0, "BUY", CFG)
    thin = slippage_pct(1000, 20_000, 0, "BUY", CFG)
    tiny = slippage_pct(1000, 5_000, 0, "BUY", CFG)
    assert deep < thin < tiny


def test_slippage_is_capped():
    assert slippage_pct(1e9, 1, 10_000, "BUY", CFG) <= 12.0


def test_zero_or_missing_liquidity_does_not_crash():
    assert slippage_pct(100, 0, None, "BUY", CFG) > 0
    assert buy_fill(1.0, 100, 0, 0, CFG)["qty"] > 0
