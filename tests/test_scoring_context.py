"""Prospective scoring, provenance and recovery regression tests. No live integrations."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, Mock
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'models'))
from audit.scoring_context import names, vector, CONTEXT_VERSION
from audit.score_model import CalibratedScoreModel as Model
from audit.ledger_recovery import LedgerRecovery as Recovery
from audit.shadow_ledger import ShadowTradeLedger as Shadow
from data.exchange_feed import BitunixWeexLiveFeed as Feed
from data.market_clock import market_clock_features
from indicators.execution import advance_trade
from test_engine_audit import trade, bar
import test_engine_audit as fixtures


class ContextTests(unittest.TestCase):
    def test_missing_is_not_measured_zero(self):
        absent=vector({},'LONG');zero=vector({'range_1h_pct':0},'LONG')
        self.assertNotEqual(absent,zero)
        self.assertEqual(absent[names().index('range_1h_pct_missing')],1)
        self.assertEqual(zero[names().index('range_1h_pct_missing')],0)

    def test_direction_and_range_units(self):
        f={'taker_buy_sell_ratio':3,'bid_ask_imbalance':.4,'long_account_pct':75,
           'funding_annualised_pct':10,'range_1h_pct':1,'sl_pct':.01,'is_weekend':True}
        long=vector(f,'LONG');short=vector(f,'SHORT')
        self.assertEqual(long[names().index('flow_with_direction')],.5)
        self.assertEqual(short[names().index('flow_with_direction')],-.5)
        self.assertEqual(long[names().index('range_to_stop')],1)
        self.assertEqual(long[names().index('weekend_range_to_stop')],1)
        self.assertEqual(len(Model.feature_names()),len(Model.vectorise(f,'LONG')))

    def test_context_can_learn_signal_without_pillar_shortcut(self):
        rows=fixtures.ModelTests().rows()
        for r in rows:
            f=r['features'];f['range_1h_pct']=f.pop('pillar_trend')
        good=Model.build(rows,persist=False)
        self.assertTrue(good['validated'],good['reason'])
        rng=np.random.default_rng(90)
        for r in rows:r['features']['range_1h_pct']=float(rng.uniform(0,20))
        self.assertFalse(Model.build(rows,persist=False)['validated'])

    def test_legacy_context_never_promotes(self):
        rows=fixtures.ModelTests().rows()
        for r in rows:r['features'].pop('scoring_context_version')
        self.assertFalse(Model.build(rows,persist=False)['validated'])

    def test_provider_mismatch_cannot_label_trade(self):
        r=trade();r['entry_provenance']={'provider':'bybit'}
        event=advance_trade(r,[dict(bar(1,hi=105,close=104),provider='binance')])
        self.assertIsNone(event);self.assertEqual(r['data_quality_error'],'execution provider mismatch')

    def test_pinned_provider_cannot_fall_back(self):
        Feed._tf_cache={};Feed._route={}
        with patch('data.exchange_feed.requests.get',return_value=Mock(status_code=503)) as get:
            _,ok=Feed.get_exchange_ohlcv('BTC/USDT',interval='1m',provider='bybit')
        self.assertFalse(ok);self.assertEqual(get.call_count,1)
        self.assertIn('api.bybit.com',get.call_args.args[0])

    def test_invalid_ladder_never_persisted(self):
        c=dict(ticker='BTC/USDT',direction='LONG',entry=100,sl=99,tp_ladder=[101,101],adjusted_score=90)
        with patch.object(Shadow,'_atomic_write') as write:
            self.assertFalse(Shadow.open_shadow_trade(c));write.assert_not_called()

    def test_recovery_counts_only_actual_replays(self):
        good=trade();good.update(outcome='SL_HIT')
        recovered=dict(good,outcome='TP1_HIT',pre_recovery_outcome='SL_HIT',is_win=True)
        with patch.object(Recovery,'recover_result',side_effect=[{'status':'resolved','record':recovered},{'status':'no history','record':None},{'status':'unresolved','record':None}]),patch.object(Shadow,'_atomic_write') as write:
            result=Recovery.run([good,copy.deepcopy(good),copy.deepcopy(good)])
            self.assertEqual(result['recovered'],1);self.assertEqual(result['preserved_originals'],2)
            self.assertEqual(result['returned_to_open'],0);write.assert_not_called()

    def test_calendar_missing_does_not_fabricate_rth(self):
        with patch('data.market_clock._calendar',side_effect=ImportError):
            f=market_clock_features(datetime(2026,9,7,15,tzinfo=timezone.utc))
        self.assertIsNone(f['is_rth']);self.assertFalse(f['calendar_available'])

    def test_calendar_real_holiday_dst_and_early_close(self):
        try:import exchange_calendars
        except ImportError:self.skipTest('Install requirements to test real calendar')
        # Labor Day, winter pre-open, summer open and Thanksgiving Friday early close.
        cases=[((2026,9,7,15),False),((2026,1,6,14),False),((2026,7,7,14),True),((2026,11,27,19),False)]
        for args,expected in cases:
            f=market_clock_features(datetime(*args,tzinfo=timezone.utc))
            self.assertTrue(f['calendar_available'],f);self.assertEqual(f['is_rth'],expected,args)

if __name__=='__main__':unittest.main()
