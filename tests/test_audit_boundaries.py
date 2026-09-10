import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'models'))
from indicators.risk_policy import kelly_size, candidate_rank
from indicators.confluence_engine import SureShotConfluenceEngine as Confluence
from indicators.correlation_defense import CorrelationDefenseEngine as Correlation
from audit.redis_state_sync import UpstashRedisStateSync as Sync
from audit.dispatch_ledger import DispatchLedger as Dispatch
from news.event_calendar import ScheduledEventCalendar as Calendar
from news.news_intelligence import PerAssetNewsIntelligence as News
from audit.engine_efficiency import EngineEfficiencyTracker as Efficiency


class BoundaryTests(unittest.TestCase):
    def test_high_win_rate_with_tiny_payoffs_can_have_no_edge(self):
        self.assertTrue(kelly_size(.9,2,1000,win_payoff_r=.05,loss_payoff_r=1)['veto'])

    def test_growth_rank_accounts_for_reward_and_risk(self):
        high_accuracy={'kelly':kelly_size(.8,1,1000)}
        higher_growth={'kelly':kelly_size(.7,3,1000)}
        self.assertGreater(candidate_rank(higher_growth),candidate_rank(high_accuracy))

    def test_observed_loss_tail_is_included_in_kelly(self):
        k=kelly_size(.8,2,1000,loss_payoff_r=3)
        self.assertEqual(k['loss_payoff_r'],3)
        self.assertAlmostEqual(k['expected_net_R'],1)

    def test_bos_uses_latest_confirmed_swing_not_window_extreme(self):
        df=pd.DataFrame({'close':[100]*30,'high':[101]*30,'low':[99]*30})
        df.loc[29,'close']=104;df.loc[29,'high']=105
        with patch('indicators.confluence_engine.LiquidityMapEngine._swing_points',return_value=([(3,110),(20,103)],[(10,95)])):
            self.assertEqual(Confluence._detect_bos(df)['bos'],'BULLISH')

    def test_flat_price_rsi_is_neutral(self):
        self.assertEqual(Confluence._calculate_rsi(pd.DataFrame({'close':[100]*100}))[0],50)

    def test_correlation_checks_existing_broad_exposure(self):
        ok,_=Correlation.check_pending('NVDA/USDT','LONG',[{'ticker':'QQQ/USDT','direction':'LONG'}])
        self.assertFalse(ok)

    def test_stale_calendar_is_not_reported_as_safe(self):
        with patch.object(Calendar,'all_events',return_value=[]),patch.object(Calendar,'_memory',{'events':[],'fetched_at':0}):
            self.assertFalse(Calendar.assess('BTC/USDT')['available'])

    def test_generic_event_headline_does_not_create_permanent_blackout(self):
        headlines=[{'title':'Bitcoin awaits CPI data','pub_dt':datetime.now(timezone.utc),'source':'test'}]
        with patch.object(News,'_fetch_headlines',return_value=headlines),patch('news.learned_sentiment.LearnedNewsSentiment.score_headlines',return_value={'score':0,'learned_fraction':0,'terms':[]}):
            self.assertFalse(News.analyze('BTC/USDT')['block_entry'])

    def test_restore_rejects_invalid_json_and_smaller_closed_ledger(self):
        for remote in ['not json', '[{"shadow_id":"one"}]', '"wrong type"']:
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'models/audit/shadow_closed.json';p.parent.mkdir(parents=True)
                original=[{'shadow_id':'one'},{'shadow_id':'two'}];p.write_text(json.dumps(original))
                with patch.object(Sync,'enabled',return_value=True),patch.object(Sync,'_repo_root',return_value=tmp),patch.object(Sync,'STATE_FILES',{'models/audit/shadow_closed.json':'test'}),patch.object(Sync,'_redis_cmd',side_effect=lambda cmd:(True,remote) if cmd[0]=='GET' else (False,None)):
                    Sync.pull_on_startup()
                self.assertEqual(json.loads(p.read_text()),original)

    def test_dispatch_ledger_preserves_gap_fill_and_exact_reason(self):
        pos={'ticker':'BTC/USDT','direction':'LONG','entry_price':100,'stop_loss':99,
             'epoch_time':time.time()-300,'margin':10,'leverage':10,'dispatch_id':'a'}
        rows=[{'dispatch_id':'a','ticker':'BTC/USDT','direction':'LONG','entry':100,'status':'OPEN'}]
        with patch.object(Dispatch,'load',return_value=rows),patch.object(Dispatch,'_atomic_write'):
            self.assertTrue(Dispatch.record_close(pos,97,'SL_HIT'))
        self.assertEqual(rows[0]['exit_price'],97)
        self.assertLess(rows[0]['r_multiple'],-3)

    def test_dispatch_identity_prevents_closing_another_trade(self):
        pos={'ticker':'BTC/USDT','direction':'LONG','entry_price':100,'stop_loss':99,
             'epoch_time':time.time()-300,'margin':10,'leverage':10,'dispatch_id':'a'}
        rows=[{'dispatch_id':i,'ticker':'BTC/USDT','direction':'LONG','entry':100,'status':'OPEN'} for i in ['a','b']]
        with patch.object(Dispatch,'load',return_value=rows),patch.object(Dispatch,'_atomic_write'):
            Dispatch.record_close(pos,102,'TP1_HIT')
        self.assertEqual(rows[0]['status'],'CLOSED');self.assertEqual(rows[1]['status'],'OPEN')

    def test_efficiency_retry_does_not_double_count(self):
        data={'history':[{'trade_id':'a'}],'total_wins':1}
        with patch.object(Efficiency,'load_efficiency_data',return_value=data):
            result=Efficiency.record_trade_outcome('BTC','LONG',100,101,'WIN',1,trade_id='a')
        self.assertEqual(result['total_wins'],1)



class ResearchBoundaryTests(unittest.TestCase):
    def test_historical_htf_bars_align_to_clock(self):
        from audit.backtester import WalkForwardBacktester as Backtest
        start=1780002000//3600*3600+900
        frame=pd.DataFrame([dict(timestamp=(start+i*900)*1000,open=100,high=101,low=99,close=100,volume=10) for i in range(210)])
        out=Backtest._resample(frame,4)
        self.assertIsNotNone(out)
        self.assertTrue((out.timestamp % 3600000 == 0).all())
        self.assertTrue(out.timestamp.is_monotonic_increasing)

    def test_backtest_uses_market_time_and_disables_live_learned_weights(self):
        from audit.backtester import WalkForwardBacktester as Backtest
        start=1780002000//900*900
        frame=pd.DataFrame([dict(timestamp=(start+i*900)*1000,open=100,high=101,low=99,close=100,volume=10) for i in range(500)])
        signal={'direction':'LONG','total_score':65,'atr':1,'feature_snapshot':{}}
        sim={'mae_pct':0,'mfe_pct':1,'tp_hit':[1],'outcome':'TP1_HIT','is_win':True,'exit':101,'bars_held':1}
        with patch('data.exchange_feed.BitunixWeexLiveFeed.get_exchange_ohlcv',return_value=(frame,True)),patch('audit.backtester.SureShotConfluenceEngine.evaluate_setup',return_value=signal) as score,patch('audit.backtester.LiquidityMapEngine.safe_stop_loss',return_value={'stop_loss':99,'sl_pct':.01}),patch.object(Backtest,'_simulate',return_value=sim):
            records=Backtest.backtest_asset('BTC/USDT')
        self.assertTrue(records)
        self.assertFalse(score.call_args.kwargs['learned_adjustments'])
        self.assertEqual(records[0]['opened_epoch'],start+Backtest.WARMUP*900)
        self.assertEqual(records[0]['closed_epoch'],records[0]['opened_epoch']+900)
        self.assertFalse(records[0]['eligible_for_live_calibration'])

if __name__=='__main__':unittest.main()
