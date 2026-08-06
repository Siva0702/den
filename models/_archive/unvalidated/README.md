# Unvalidated modules

Built, tested against resolved outcomes, and found NOT to discriminate.
Kept for reference so the same idea is not rebuilt from scratch.

## micro_entry.py — 1m entry quality (v41.1, rejected)

Hypothesis: setups scoring 78+ fail because 15m confirmation arrives late, so 1m
data at the entry moment should reveal extension/exhaustion the 15m chart hides.

Tested on 77 resolved trades (38 wins / 39 losses), scoring the 1m bars leading
into each entry:

    WINNERS  avg penalty 2.42 | extension +0.15 ATR | decaying 21.1% | flagged 34.2%
    LOSERS   avg penalty 1.80 | extension -1.23 ATR | decaying 20.5% | flagged 33.3%

No separation. Losers scored marginally BETTER. The apparent 3/3 hit on the
78+ losers was chance: `momentum_decaying` fires on ~21% of all trades regardless
of outcome.

If revisited, the failure was likely the metrics, not the premise — extension from
1m VWAP and EMA5 deceleration may simply be the wrong measurements. Order-flow
delta or 1m volume profile at entry would be different hypotheses worth testing.
