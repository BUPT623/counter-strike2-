"""Pricing formulas for the CS2 arbitrage analyzer."""
from typing import Any, Dict

import pandas as pd


PricingConfig = Dict[str, Any]

DEFAULT_PRICING_CONFIG: PricingConfig = {
    "min_exit_coefficient": 0.10,
    "max_exit_coefficient": 0.45,
    "base_exit_coefficient": 0.30,
    "strong_buy_depth": 50.0,
    "heavy_sell_supply": 300.0,
    "wide_spread_rate": 0.35,
    "buy_depth_weight": 0.15,
    "sell_pressure_weight": 0.10,
    "spread_penalty_weight": 0.15,
}


def add_pricing_columns(
    merged: pd.DataFrame,
    exchange_rate_hkd_to_rmb: float,
    config: PricingConfig = DEFAULT_PRICING_CONFIG,
) -> pd.DataFrame:
    """
    Add cost and profit columns for two exit modes.

    Listing mode keeps the existing dynamic fill-coefficient formula. Instant
    mode assumes immediate sale into the current UU buy order.
    """
    priced = merged.copy()
    priced["exchange_rate_hkd_to_rmb"] = exchange_rate_hkd_to_rmb
    priced["cost_rmb"] = priced["min_price"] * exchange_rate_hkd_to_rmb

    priced["uu_spread_rmb"] = priced["uu_sell_price"] - priced["uu_buy_price"]
    priced["uu_spread_rate"] = priced["uu_spread_rmb"] / priced["uu_sell_price"]

    buy_depth_score = (
        priced["uu_buy_num"] / float(config["strong_buy_depth"])
    ).clip(lower=0.0, upper=1.0)
    sell_pressure_score = (
        priced["uu_sell_num"] / float(config["heavy_sell_supply"])
    ).clip(lower=0.0, upper=1.0)
    spread_penalty = (
        priced["uu_spread_rate"] / float(config["wide_spread_rate"])
    ).clip(lower=0.0, upper=1.0)

    coefficient = (
        float(config["base_exit_coefficient"])
        + float(config["buy_depth_weight"]) * buy_depth_score
        - float(config["sell_pressure_weight"]) * sell_pressure_score
        - float(config["spread_penalty_weight"]) * spread_penalty
    )
    priced["listing_fill_coefficient"] = coefficient.clip(
        lower=float(config["min_exit_coefficient"]),
        upper=float(config["max_exit_coefficient"]),
    )

    priced["listing_expected_exit_price"] = (
        priced["uu_buy_price"]
        + priced["listing_fill_coefficient"] * priced["uu_spread_rmb"]
    )
    priced["listing_profit_rate"] = (
        (priced["listing_expected_exit_price"] - priced["cost_rmb"]) / priced["cost_rmb"]
    )

    priced["instant_expected_exit_price"] = priced["uu_buy_price"]
    priced["instant_profit_rate"] = (
        (priced["instant_expected_exit_price"] - priced["cost_rmb"]) / priced["cost_rmb"]
    )

    # Backward-compatible primary profit columns. The report treats listing
    # mode as the default arbitrage mode and shows instant mode beside it.
    priced["exit_fill_coefficient"] = priced["listing_fill_coefficient"]
    priced["expected_exit_price"] = priced["listing_expected_exit_price"]
    priced["raw_profit_rate"] = priced["listing_profit_rate"]
    return priced
