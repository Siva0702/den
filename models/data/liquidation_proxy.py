# models/data/liquidation_proxy.py


class LiquidationProxy:
    """
    Liquidation cascade detection without a paid feed.

    Coinglass charges for aggregated liquidation prints. It is not needed, because a
    liquidation is a FORCED CLOSE and a forced close necessarily destroys open interest.
    So the cascade is already visible in data the engine fetches anyway:

        OI collapses + price falls   ->  LONGS were liquidated
        OI collapses + price rises   ->  SHORTS were liquidated (squeeze)
        OI rises     + price moves   ->  new positions opening, NOT a liquidation

    That last row is the one that matters. Price moving hard on RISING OI is real
    conviction and tends to continue. The identical move on COLLAPSING OI is forced
    supply, which exhausts the moment the liquidation queue clears — which is the
    "positive news, price makes new lows" case: the move is unwinding, not informed.

    Nothing here is asserted into the score. It is emitted as features so the calibrated
    model can measure whether these states actually predict anything, exactly like the
    weekend and range features. If they carry no signal they will earn zero weight.
    """

    OI_COLLAPSE = -1.5          # % change over the OI window that counts as forced
    OI_SURGE = 1.5
    MOVE_MIN = 0.4              # % price move; below this the OI change is just noise

    @classmethod
    def analyze(cls, oi: dict, price_change_pct: float, crowding: dict = None) -> dict:
        """
        oi                 -> get_open_interest_delta() result
        price_change_pct   -> % price change over roughly the same window
        crowding           -> get_crowding() result, optional; sharpens the read
        """
        out = {
            "available": False,
            "liq_state": "NONE",
            "liq_side": None,
            "liq_intensity": 0.0,
            "cascade_risk": 0.0,
            "notes": [],
        }
        if not isinstance(oi, dict) or not oi.get("available"):
            return out

        try:
            d_oi = float(oi.get("oi_change_12bar_pct") or 0.0)
            move = float(price_change_pct or 0.0)
        except (TypeError, ValueError):
            return out

        out["available"] = True
        out["oi_change_pct"] = round(d_oi, 3)
        out["price_change_pct"] = round(move, 3)

        # Intensity scales with how much OI vanished against how far price travelled.
        intensity = min(abs(d_oi) / 5.0, 1.0) * min(abs(move) / 2.0, 1.0)

        if abs(move) < cls.MOVE_MIN:
            out["liq_state"] = "QUIET"
            return out

        if d_oi <= cls.OI_COLLAPSE:
            # Forced supply. The side that lost is the side price moved against.
            out["liq_state"] = "LIQUIDATION_CASCADE"
            out["liq_side"] = "LONGS" if move < 0 else "SHORTS"
            out["liq_intensity"] = round(intensity, 3)
            out["notes"].append(
                f"OI {d_oi:+.2f}% on a {move:+.2f}% move — {out['liq_side'].lower()} "
                f"being forced out; move is unwinding, not accumulation")
        elif d_oi >= cls.OI_SURGE:
            out["liq_state"] = "POSITION_BUILDING"
            out["liq_side"] = "LONGS" if move > 0 else "SHORTS"
            out["liq_intensity"] = round(intensity, 3)
            out["notes"].append(
                f"OI {d_oi:+.2f}% on a {move:+.2f}% move — new {out['liq_side'].lower()} "
                f"opening into it; conviction, not forced flow")
        else:
            out["liq_state"] = "NEUTRAL"

        # Forward-looking: a crowded book that has NOT yet been flushed is the fuel.
        # Crowded + OI still intact = the cascade has not happened yet.
        if isinstance(crowding, dict) and crowding.get("available"):
            try:
                long_pct = float(crowding.get("long_account_pct") or 50.0)
            except (TypeError, ValueError):
                long_pct = 50.0
            lean = abs(long_pct - 50.0) / 50.0
            unflushed = 1.0 if d_oi > cls.OI_COLLAPSE else 0.3
            out["cascade_risk"] = round(lean * unflushed, 3)
            out["crowded_side"] = ("LONGS" if long_pct >= 60 else
                                   "SHORTS" if long_pct <= 40 else None)
            if out["cascade_risk"] >= 0.4 and out["crowded_side"]:
                out["notes"].append(
                    f"{long_pct:.1f}% of accounts {out['crowded_side'].lower()} with OI intact — "
                    f"stop pool not yet flushed")
        return out

    @classmethod
    def features(cls, liq: dict) -> dict:
        """Flat, model-ready. Missing data must read as absent, never as a false zero."""
        if not isinstance(liq, dict) or not liq.get("available"):
            return {"liq_state": None, "liq_side": None,
                    "liq_intensity": None, "cascade_risk": None}
        return {
            "liq_state": liq.get("liq_state"),
            "liq_side": liq.get("liq_side"),
            "liq_intensity": liq.get("liq_intensity"),
            "cascade_risk": liq.get("cascade_risk"),
        }
