# models/crypto/kelly_calculator.py

def calculate_kelly_position(
    account_balance: float = 1000.0,
    win_rate: float = 0.55,          # e.g. 55% win rate
    reward_to_risk: float = 2.0,     # e.g. 1:2 R:R ratio
    entry_price: float = 0.0,
    stop_loss_price: float = 0.0,
    take_profit_price: float = 0.0,
    is_scalp: bool = False           # False = Half-Kelly (Key), True = Quarter-Kelly (Scalp)
):
    """
    Calculates Kelly Criterion position sizing for Crypto Futures.
    """
    # 1. Calculate Full Kelly Percentage (f*)
    full_kelly = win_rate - ((1.0 - win_rate) / reward_to_risk)
    
    if full_kelly <= 0:
        print(f"[!] NO EDGE: Full Kelly is {full_kelly*100:.2f}%. Do NOT enter this trade.")
        return

    # 2. Apply Kelly Fraction
    fraction_multiplier = 0.25 if is_scalp else 0.50
    trade_type = "QUARTER-KELLY (SCALP)" if is_scalp else "HALF-KELLY (KEY POSITION)"
    
    allocated_kelly_pct = full_kelly * fraction_multiplier
    dollars_at_risk = account_balance * allocated_kelly_pct
    
    # 3. Derive Position Size & Leverage from Stop Loss Distance
    sl_distance_pct = abs(entry_price - stop_loss_price) / entry_price
    tp_distance_pct = abs(take_profit_price - entry_price) / entry_price
    
    notional_position_usd = dollars_at_risk / sl_distance_pct
    
    # Target 10% max margin allocation per trade to keep liquid buffer
    suggested_leverage = round(notional_position_usd / (account_balance * 0.10))
    margin_required = notional_position_usd / max(suggested_leverage, 1)

    # 4. Expected Value
    ev = (win_rate * tp_distance_pct) - ((1 - win_rate) * sl_distance_pct)

    print("=" * 60)
    print(f"      ANTI GRAVITY QUANT KELLY ENGINE | {trade_type}      ")
    print("=" * 60)
    print(f"Account Balance        : ${account_balance:,.2f} USDT")
    print(f"Assumed Win Rate (W)   : {win_rate * 100:.1f}%")
    print(f"Reward-to-Risk (R)     : {reward_to_risk:.2f}:1")
    print(f"Full Kelly (f*)        : {full_kelly * 100:.2f}%")
    print(f"Selected Kelly Risk    : {allocated_kelly_pct * 100:.2f}% (${dollars_at_risk:,.2f} USDT)")
    print("-" * 60)
    print(f"Entry Price            : ${entry_price:,.4f}")
    print(f"Stop Loss / TP Distance: SL {sl_distance_pct*100:.2f}% | TP {tp_distance_pct*100:.2f}%")
    print(f"Expected Value (EV)    : +{ev*100:.2f}% per unit")
    print("-" * 60)
    print(f"Notional Position Size : ${notional_position_usd:,.2f} USDT")
    print(f"Suggested Leverage     : {suggested_leverage}x (Isolated)")
    print(f"Required Isolated Margin: ${margin_required:,.2f} USDT")
    print("=" * 60)

if __name__ == "__main__":
    # Example 1: SOL Key Position Breakout (Half-Kelly)
    print("\n--- KEY POSITION EXAMPLE ---")
    calculate_kelly_position(
        account_balance=1000.0,
        win_rate=0.55,
        reward_to_risk=2.5,
        entry_price=150.0,
        stop_loss_price=145.0, # 3.33% SL
        take_profit_price=162.5, # 8.33% TP
        is_scalp=False
    )

    # Example 2: BTC Scalp Trade (Quarter-Kelly)
    print("\n--- SCALP POSITION EXAMPLE ---")
    calculate_kelly_position(
        account_balance=1000.0,
        win_rate=0.60,
        reward_to_risk=1.8,
        entry_price=65000.0,
        stop_loss_price=64350.0, # 1.0% SL
        take_profit_price=66170.0, # 1.8% TP
        is_scalp=True
    )
