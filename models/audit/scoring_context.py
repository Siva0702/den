"""Frozen context for learned scoring. Optional measurements stay explicitly missing."""
import math
from indicators.execution import finite

CONTEXT_VERSION = 'v3-market-context'
NUMERIC = ['range_1h_pct', 'hours_to_us_open', 'long_account_pct',
           'funding_annualised_pct', 'oi_change_12bar_pct', 'taker_buy_sell_ratio',
           'bid_ask_imbalance', 'liq_intensity', 'cascade_risk']
BOOLEAN = ['is_weekend', 'is_rth', 'is_us_holiday']
INTERACTIONS = ['flow_with_direction', 'book_with_direction', 'crowding_with_direction',
                'funding_with_direction', 'news_with_direction', 'range_to_stop',
                'weekend_range_to_stop', 'rth_range_to_stop', 'regime_with_direction']


def names():
    return NUMERIC + [k+'_missing' for k in NUMERIC] + BOOLEAN + [k+'_missing' for k in BOOLEAN] + INTERACTIONS


def vector(features, direction):
    f = features or {}
    sign = 1 if direction.upper() == 'LONG' else -1
    values = [finite(f.get(k)) for k in NUMERIC]
    result = [v if v is not None else 0.0 for v in values]
    result += [float(v is None) for v in values]
    result += [float(bool(f.get(k))) for k in BOOLEAN]
    result += [float(f.get(k) is None) for k in BOOLEAN]
    ratio = finite(f.get('taker_buy_sell_ratio'))
    flow = (ratio-1)/(ratio+1) if ratio is not None and ratio >= 0 else 0
    crowd = finite(f.get('long_account_pct'))
    stop = finite(f.get('sl_pct'), 0)
    # range is percent; stop is a fraction. Preserve that unit conversion.
    movement = finite(f.get('range_1h_pct'), 0)/(100*stop) if stop > 0 else 0
    news = {'BULLISH':1, 'BEARISH':-1}.get(str(f.get('news_bias')).upper(), 0)
    regime = {'BULLISH':1, 'BEARISH':-1, 'BULL':1, 'BEAR':-1}.get(str(f.get('regime_direction')).upper(), 0)
    result += [sign*flow, sign*finite(f.get('bid_ask_imbalance'), 0),
               sign*(crowd-50)/50 if crowd is not None else 0,
               sign*finite(f.get('funding_annualised_pct'), 0), sign*news,
               movement, movement*bool(f.get('is_weekend')), movement*bool(f.get('is_rth')), sign*regime]
    return [x if math.isfinite(x) else 0.0 for x in result]
