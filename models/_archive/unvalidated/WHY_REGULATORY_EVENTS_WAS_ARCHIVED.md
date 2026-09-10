# regulatory_events.py — archived 2026-09-10

Archived under Rule 9: tested against resolved outcomes, found non-discriminating.

## The measurement

    regulatory_multiplier distribution over 1903 resolved shadow trades:
        {0.65: 1903}

It returned 0.65 on every single record and never any other value. A detector with a
100% fire rate carries zero information — it is a constant wearing a signal's clothes.

## Why it always fired

    if len(recess_matches) >= 2 or ("clarity act" in full_text and ...)

with recess_keywords containing "recess", "delayed", "uncertainty", "deadline looms".
Any two of those appear in essentially any week of Google News political headlines,
for any asset.

## What it actually cost

Nothing in scoring — a previous fix had already neutralised the multiplier inside
confluence_engine ("retained in the signature only for backward compatibility and is
deliberately unused"). What it did cost:

  - a permanent "BEARISH REGULATORY HEADWIND: Congressional Recess & Clarity Act
    Delay" banner on every digest ever sent
  - a hardcoded FAKE headline: "Regulatory enforcement actions or legislative friction
    creating market resistance" was a literal string, never a real news item
  - an HTTP call per scan
  - a hardcoded anecdote (BTC $97K -> $61K) presented as precedent

## What replaces it

Nothing needs to. Regulatory events already enter through the event calendar as
ordinary scheduled events, weighted by real proximity and real asset relevance. And
news itself is handled per-asset by PerAssetNewsIntelligence, whose signal the
calibrated model learned from outcomes:

    news_bias=BULLISH   +0.1437
    news_bias=BEARISH   +0.0650
    news_blocked        -0.0896

Kept here so the same idea is not rebuilt.
