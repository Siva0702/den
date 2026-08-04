# models/indicators/trailing_sl.py

class DynamicBreakevenTrailingEngine:
    """
    Den Engine v18.0 Risk-Free Breakeven Trailing Engine:
    When a position reaches 50% progress to TP, automatically trails Stop Loss to Breakeven (+0.10% buffer)
    to guarantee 100% Risk-Free Trade Protection!
    """

    @staticmethod
    def evaluate_trailing_sl(
        entry: float, 
        current_price: float, 
        sl: float, 
        tp: float, 
        direction: str
    ) -> tuple:
        tp_distance = abs(tp - entry)
        current_distance = abs(current_price - entry)
        
        is_50_pct_progress = (current_distance / max(tp_distance, 0.0001)) >= 0.50

        if is_50_pct_progress:
            # Move SL to Breakeven + 0.10% fee buffer
            fee_buffer = entry * 0.0010
            new_sl = round(entry + fee_buffer if direction == "LONG" else entry - fee_buffer, 2)
            
            # Only trail if new SL is better than existing SL
            if (direction == "LONG" and new_sl > sl) or (direction == "SHORT" and new_sl < sl):
                return True, new_sl

        return False, sl
