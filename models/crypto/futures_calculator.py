# models/crypto/futures_calculator.py

def calculate_futures_position(
    account_balance=1000.0,
    risk_percentage=3.0,     # Risk 3% of account per trade ($30)
    entry_price=0.0,
    stop_loss_price=0.0,
    take_profit_price=0.0,
    leverage=10
):
    """
    Calculates exact margin, position size in tokens, and risk-to-reward ratio.
    """
    if entry_price <= 0 or stop_loss_price <= 0 or take_profit_price <= 0:
        print("Error: Entry, SL, and TP prices must be greater than zero.")
        return

    # Dollars at risk
    risk_amount = account_balance * (risk_percentage / 100.0)
    
    # Distance to stop loss
    sl_distance_pct = abs(entry_price - stop_loss_price) / entry_price
    tp_distance_pct = abs(take_profit_price - entry_price) / entry_price
    
    # Position size required to match risk tolerance
    position_value_usd = risk_amount / sl_distance_pct
    margin_required = position_value_usd / leverage
    
    # Potential Profit
    potential_profit = position_value_usd * tp_distance_pct
    rr_ratio = potential_profit / risk_amount

    print("=" * 55)
    print("      ANTI GRAVITY FUTURES RISK & POSITION CALCULATOR    ")
    print("=" * 55)
    print(f"Account Balance    : ${account_balance:,.2f} USDT")
    print(f"Risk per Trade     : ${risk_amount:,.2f} USDT ({risk_percentage}%)")
    print(f"Leverage           : {leverage}x (Isolated)")
    print(f"Position Size (USD): ${position_value_usd:,.2f} USDT")
    print(f"Margin Required    : ${margin_required:,.2f} USDT")
    print(f"Stop Loss Distance : {sl_distance_pct * 100:.2f}%")
    print(f"Target TP Distance : {tp_distance_pct * 100:.2f}%")
    print(f"Risk/Reward Ratio  : 1:{rr_ratio:.2f}")
    print(f"Potential Profit   : +${potential_profit:,.2f} USDT")
    print("=" * 55)

if __name__ == "__main__":
    # Example Trade: SOL Long Entry at $140, SL at $136, TP at $152 with 10x leverage
    calculate_futures_position(
        account_balance=1000.0,
        risk_percentage=4.0,
        entry_price=140.0,
        stop_loss_price=136.0,
        take_profit_price=152.0,
        leverage=10
    )
