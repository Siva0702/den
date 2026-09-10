"""Offline regression checks: no exchange requests, Telegram sends or production writes."""
import copy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'models'))
from indicators.execution import advance_trade, LOGIC_VERSION, FEATURE_VERSION, trade_cost_pct
from indicators.risk_policy import kelly_size, dispatch_eligibility
from data.exchange_feed import BitunixWeexLiveFeed as Feed
from audit.score_model import CalibratedScoreModel as Model
from audit.score_tracker import ScoreStabilityTracker as Stability
from audit.shadow_ledger import ShadowTradeLedger as Shadow
from audit.ledger_recovery import LedgerRecovery
from audit.dispatch_ledger import DispatchLedger
from audit.decision_report import build_status, render_status
from position_monitor import ActivePositionMonitor
from alerts.telegram_bot import TelegramAlertBot

BASE = 1780000020  # minute aligned
BASE -= BASE % 60


def trade(direction='LONG'):
    return dict(ticker='BTC/USDT', direction=direction, entry=100., entry_price=100.,
                stop_loss=99. if direction == 'LONG' else 101.,
                tp_ladder=[101., 102., 103., 104.] if direction == 'LONG' else [99., 98., 97., 96.],
                opened_epoch=BASE+10, epoch_time=BASE+10,
                config_version=LOGIC_VERSION, margin=10, leverage=10,
                features={'pillar_trend': 10}, raw_score=60, factor_scores={})


def bar(minute, o=100, hi=100.5, lo=99.5, close=100):
    return dict(timestamp=(BASE+minute*60)*1000, open=o, high=hi, low=lo, close=close, volume=10)


class ExecutionTests(unittest.TestCase):
    def resolve(self, t, bars):
        return advance_trade(t, bars, now=BASE+10000)

    def test_entry_and_forming_bars_cannot_label_trade(self):
        t=trade()
        self.assertIsNone(advance_trade(t, [bar(0, hi=110, lo=90)], now=BASE+50))
        self.assertIsNone(advance_trade(t, [bar(1, hi=110, lo=90)], now=BASE+80))
        self.assertNotIn('last_bar_ts', t)

    def test_stop_wins_ambiguous_bar_long_and_short(self):
        for d in ['LONG','SHORT']:
            out=self.resolve(trade(d), [bar(1, hi=105, lo=95)])
            self.assertEqual(out['outcome'], 'SL_HIT')
            self.assertFalse(out['is_win'])

    def test_ratchet_arms_next_bar(self):
        t=trade()
        self.assertIsNone(self.resolve(t, [bar(1, hi=101.3, lo=99.5, close=101.2)]))
        out=self.resolve(t, [bar(2, o=101.2, hi=101.4, lo=100.8, close=101)])
        self.assertEqual(out['exit_price'],101)
        self.assertEqual(out['outcome'],'TP1_HIT')

    def test_old_trail_checked_before_new_target(self):
        t=trade()
        self.resolve(t,[bar(1,hi=101.3,close=101.2)])
        out=self.resolve(t,[bar(2,o=101.2,hi=103.5,lo=100.8,close=103)])
        self.assertEqual(out['exit_price'],101)
        self.assertEqual(t['tp_levels_hit'],[1])

    def test_final_target_exits_on_tagging_bar(self):
        out=self.resolve(trade(),[bar(1,hi=104.2,lo=99.5,close=104)])
        self.assertEqual(out['outcome'],'TP4_HIT')
        self.assertEqual(out['closed_epoch'], BASE+120)

    def test_short_trailing_symmetry(self):
        t=trade('SHORT')
        self.resolve(t,[bar(1,hi=100.5,lo=98.7,close=98.8)])
        out=self.resolve(t,[bar(2,o=98.8,hi=99.2,lo=98.7,close=99)])
        self.assertEqual(out['exit_price'],99)
        self.assertEqual(out['outcome'],'TP1_HIT')

    def test_gap_fills_at_worse_open(self):
        out=self.resolve(trade(),[bar(1,o=97,hi=100,lo=96,close=99)])
        self.assertEqual(out['exit_price'],97)
        self.assertLess(out['net_pnl_pct'],-3)

    def test_repeated_candle_does_not_arm_trail(self):
        t=trade();b=bar(1,hi=101.3,close=101.2)
        self.assertIsNone(self.resolve(t,[b]))
        self.assertIsNone(self.resolve(t,[b,b]))
        self.assertEqual(t['bars_observed'],1)

    def test_missing_candle_quarantines_and_can_catch_up(self):
        t=trade();self.assertIsNone(self.resolve(t,[bar(2,hi=104.5,close=104)]))
        self.assertIn('data_quality_error',t)
        out=self.resolve(t,[bar(1),bar(2,hi=104.5,close=104)])
        self.assertEqual(out['outcome'],'TP4_HIT')
        self.assertNotIn('data_quality_error',t)

    def test_nonfinite_bar_cannot_poison_state(self):
        t=trade();self.assertIsNone(self.resolve(t,[bar(1,hi=float('nan'))]))
        self.assertIn('data_quality_error',t)

    def test_replay_and_incremental_execution_identical(self):
        bars=[bar(1,hi=101.4,close=101.2),bar(2,o=101.2,hi=102.4,lo=101.1,close=102.2),
              bar(3,o=102.2,hi=102.5,lo=101.9,close=102)]
        t=trade();live=None
        for b in bars:
            live=self.resolve(t,[b])
        replay=LedgerRecovery._replay(trade(),pd.DataFrame(bars))
        for key in ['exit_price','outcome','net_pnl_pct','closed_epoch','is_win']:
            self.assertEqual(live[key],replay[key])

    def test_replay_does_not_stamp_missing_history(self):
        self.assertIsNone(LedgerRecovery._replay(trade(),pd.DataFrame([bar(2,hi=104.5,close=104)])))

    def test_scalar_shadow_snapshot_cannot_resolve(self):
        t=trade()
        with patch.object(Shadow,'load_open',return_value=[t]),patch.object(Shadow,'_atomic_write'):
            self.assertEqual(Shadow.update_prices({'BTC/USDT':105}),[])

    def test_shadow_live_and_replay_outcomes_identical(self):
        bars=[bar(1,hi=101.4,close=101.2),bar(2,o=101.2,hi=101.3,lo=100.8,close=101)]
        with patch.object(Shadow,'load_open',return_value=[trade()]),patch.object(Shadow,'load_closed',return_value=[]),patch.object(Shadow,'_atomic_write'):
            shadow=Shadow.update_prices({'BTC/USDT':{'bars':bars}})[0]
        monitor=ActivePositionMonitor(None,None)
        with patch.object(monitor,'load_positions',return_value=[trade()]),patch.object(monitor,'save_positions') as save,patch.object(DispatchLedger,'record_close',return_value=True) as close,patch('position_monitor.EngineEfficiencyTracker.record_trade_outcome'),patch('position_monitor.SignalCooldownEngine.record_outcome'),patch.object(monitor,'send_telegram_alert'):
            monitor.check_active_positions('BTC/USDT',999,1,False,bars=bars)
            self.assertEqual(close.call_args.args[1],shadow['exit_price'])
            self.assertEqual(close.call_args.args[2],shadow['outcome'])
            self.assertEqual(save.call_args.args[0],[])


class FeedTests(unittest.TestCase):
    def setUp(self):
        Feed._tf_cache={};Feed._route={}

    def frame(self, interval='1m', now=None):
        now=time.time() if now is None else now
        span=Feed.TF_SECONDS[interval]*1000
        end=int(now*1000//span)*span
        return pd.DataFrame([dict(timestamp=end-span,open=100,high=101,low=99,close=100,volume=10),
                             dict(timestamp=end,open=100,high=101,low=99,close=100,volume=10)])

    def test_all_timeframes_correct_provider_parameters(self):
        bybit={'1m':'1','5m':'5','15m':'15','1h':'60','4h':'240','1d':'D'}
        bitget={'1m':'1m','5m':'5m','15m':'15m','1h':'1H','4h':'4H','1d':'1D'}
        for interval in Feed.TF_SECONDS:
            for provider in ['binance','bybit','bitget']:
                Feed._tf_cache={};Feed._route={}
                raw=[[r.timestamp,r.open,r.high,r.low,r.close,r.volume] for r in self.frame(interval).itertuples()]
                urls=[]
                def get(url,**kwargs):
                    urls.append(url)
                    if provider not in url:
                        return Mock(status_code=503)
                    payload=raw if provider=='binance' else {'result':{'list':raw[::-1]}} if provider=='bybit' else {'data':raw[::-1]}
                    return Mock(status_code=200,json=lambda:payload)
                with patch('data.exchange_feed.requests.get',side_effect=get):
                    df,real=Feed.get_exchange_ohlcv('BTC/USDT',interval=interval)
                self.assertTrue(real,(provider,interval))
                expected=f'interval={bybit[interval] if provider=="bybit" else interval}' if provider!='bitget' else f'granularity={bitget[interval]}'
                self.assertIn(expected,urls[-1])
                self.assertTrue(df.timestamp.is_monotonic_increasing)

    def test_wrong_interval_stale_duplicate_and_nan_rejected(self):
        good=self.frame()
        for invalid in [self.frame('15m'),good.assign(close=float('nan')),pd.concat([good,good]),good.assign(timestamp=good.timestamp-600000)]:
            self.assertIsNone(Feed.validate_candles(invalid,'1m'))

    def test_unsupported_interval_raises(self):
        with self.assertRaises(ValueError):Feed.get_exchange_ohlcv('BTC/USDT',interval='2m')

    def test_live_cache_expires_in_seconds_not_entire_candle(self):
        now=time.time();df=self.frame('15m',now)
        raw=[[r.timestamp,r.open,r.high,r.low,r.close,r.volume] for r in df.itertuples()]
        response=Mock(status_code=200,json=lambda:raw)
        with patch('data.exchange_feed.time.time',return_value=now),patch('data.exchange_feed.requests.get',return_value=response) as get:
            Feed.get_exchange_ohlcv('BTC/USDT');Feed.get_exchange_ohlcv('BTC/USDT')
            self.assertEqual(get.call_count,1)
        with patch('data.exchange_feed.time.time',return_value=now+11),patch('data.exchange_feed.requests.get',return_value=response) as get:
            Feed.get_exchange_ohlcv('BTC/USDT');self.assertEqual(get.call_count,1)


class RiskTests(unittest.TestCase):
    def test_fees_can_turn_gross_edge_into_veto(self):
        self.assertFalse(kelly_size(.51,1,1000)['veto'])
        self.assertTrue(kelly_size(.51,1,1000,cost_r=.1)['veto'])

    def test_tiny_edge_never_gets_dollar_floor(self):
        k=kelly_size(.5001,1,1000)
        self.assertLessEqual(k['dollars_at_risk'],.10)
        self.assertAlmostEqual(k['risk_pct'],k['dollars_at_risk']/10)

    def test_cap_holds_for_small_accounts(self):
        for balance in [1,10,1000]:
            k=kelly_size(.9,2,balance)
            self.assertLessEqual(k['dollars_at_risk'],balance*.01)

    def test_unknown_nan_negative_zero_inputs_veto(self):
        for p in [None,float('nan'),float('inf'),-.1,0,1,1.1]:
            self.assertTrue(kelly_size(p,2,1000)['veto'])
        self.assertTrue(kelly_size(.9,2,0)['veto'])

    def candidate(self):
        return dict(model_evidence={'tradable':True},total_score=85,calibrated_win_rate=.75,
                    entry=100,sl=99,tp_ladder=[102,103],direction='LONG',model_prob=.8,
                    kelly=kelly_size(.75,2,1000),final_margin=20,rr=2,quote_fresh=True,hunt_risk={'hunt_risk_score':0})

    def test_gate_rejects_unvalidated_model_even_with_99_score(self):
        c=self.candidate();c.update(total_score=99,model_evidence={'tradable':False,'reason':'failed holdout'})
        self.assertFalse(dispatch_eligibility(c,[],1000)['allowed'])

    def test_portfolio_exposure_includes_existing_positions(self):
        c=self.candidate();p=trade();p.update(margin=499,leverage=100)
        self.assertFalse(dispatch_eligibility(c,[p],1000)['allowed'])

    def test_gate_passes_valid_case_and_rejects_news_block(self):
        c=self.candidate();self.assertTrue(dispatch_eligibility(c,[],1000)['allowed'])
        c['news']={'block_entry':True};self.assertFalse(dispatch_eligibility(c,[],1000)['allowed'])


class ModelTests(unittest.TestCase):
    def rows(self,n=500,noise=False):
        rng=np.random.default_rng(9);out=[]
        start=time.time()-20*86400
        for i in range(n):
            x=float(rng.uniform(0,20));win=(bool(rng.integers(0,2)) if noise else x>10)
            op=start+i*19*86400/n
            out.append(dict(shadow_id=str(i),ticker='BTC/USDT',direction='LONG',opened_epoch=op,
                            closed_epoch=op+120,entry=100.,stop_loss=99.,exit_price=102 if win else 99,
                            pnl_pct=2 if win else -1,is_win=win,outcome='TP1_HIT' if win else 'SL_HIT',
                            logic_version=LOGIC_VERSION,features={'feature_version':FEATURE_VERSION,'scoring_context_version':'v3-market-context','pillar_trend':x,'reward_risk':2,'sl_pct':.01}))
        return out

    def test_purged_split_excludes_overlapping_labels_and_same_timestamp(self):
        rows=self.rows(100)
        rows[0]['closed_epoch']=rows[65]['opened_epoch']
        rows[60]['closed_epoch']=rows[85]['opened_epoch']
        train,cal,test=Model.chronological_split(rows)
        self.assertNotIn(rows[0],train);self.assertNotIn(rows[60],cal)
        self.assertLess(max(r['closed_epoch'] for r in train),min(r['opened_epoch'] for r in cal))
        self.assertLess(max(r['closed_epoch'] for r in cal),min(r['opened_epoch'] for r in test))

    def test_in_sample_calibration_is_not_used(self):
        original=Model._fit_logistic
        fits=[]
        def fit(X,y,*args,**kwargs):
            fits.append(len(y));return original(X,y,*args,**kwargs)
        with patch.object(Model,'_fit_logistic',side_effect=fit):
            m=Model.build(self.rows(),force=True,persist=False)
        self.assertEqual(fits,[m['split']['train'],m['split']['calibration']])
        self.assertNotIn(m['split']['test'],fits[:1])

    def test_controlled_predictive_data_can_pass_and_noise_cannot(self):
        good=Model.build(self.rows(),force=True,persist=False)
        self.assertTrue(good['validated'],good.get('validation'))
        bad=Model.build(self.rows(noise=True),force=True,persist=False)
        self.assertFalse(bad['validated'])

    def test_legacy_labels_never_promoted(self):
        rows=self.rows()
        for r in rows:r['logic_version']='v4-trail-one-behind-1m'
        m=Model.build(rows,force=True,persist=False)
        self.assertFalse(m['validated'])
        self.assertIn('legacy',m['reason'])

    def test_breakeven_after_cost_is_not_win_and_missing_outcomes_excluded(self):
        r=self.rows(1)[0];r.update(exit_price=100,pnl_pct=0,is_win=None)
        self.assertLess(Model.net_return(r),0)
        _,y=Model._matrix([r]);self.assertEqual(y[0],0)
        r['outcome']='TIMEOUT_NO_DATA';self.assertEqual(Model.clean_rows([r]),[])

    def test_vectorise_is_finite_and_sessions_recognised(self):
        v=Model.vectorise({'rsi':float('nan'),'pillar_htf':float('inf'),'session':'NY_OVERLAP'},'LONG')
        self.assertTrue(np.isfinite(v).all())
        self.assertEqual(v[Model.feature_names().index('session=NY_OVERLAP')],1)

    def test_explicit_research_rows_do_not_replace_live_cache(self):
        sentinel={'available':False,'reason':'live'}
        with patch.object(Model,'_cache',{'built':time.time(),'model':sentinel}):
            Model.build(self.rows(),persist=False)
            self.assertIs(Model.build(),sentinel)


class StabilityAndMessagingTests(unittest.TestCase):
    def setUp(self):Stability._history={};Stability._observations={}

    def test_repeated_cached_scans_are_not_independent_confirmation(self):
        with patch('audit.score_tracker.time.time',return_value=BASE):
            for _ in range(10):Stability.record('BTC','LONG',85,observation_id=1)
            self.assertFalse(Stability.evaluate('BTC','LONG',78)['stable'])
            self.assertEqual(len(Stability.trajectory('BTC','LONG')),1)

    def test_new_minutes_confirm_but_direction_flip_resets(self):
        for i in range(3):
            with patch('audit.score_tracker.time.time',return_value=BASE+i*60):Stability.record('BTC','LONG',85,observation_id=i)
        with patch('audit.score_tracker.time.time',return_value=BASE+120):
            self.assertTrue(Stability.evaluate('BTC','LONG',78)['stable'])
            Stability.record('BTC','SHORT',85,observation_id=3)
            self.assertFalse(Stability.evaluate('BTC','LONG',78)['stable'])

    def test_telegram_has_no_fallback_credentials(self):
        bot=TelegramAlertBot(None,None)
        with patch('alerts.telegram_bot.requests.post') as post:
            self.assertIsNone(bot.send_alert('test'));self.assertEqual(bot.get_reply_updates(),[])
            post.assert_not_called()

    def test_telegram_ignores_other_chats(self):
        bot=TelegramAlertBot('test','123')
        with patch.object(bot,'get_reply_updates',return_value=[{'update_id':1,'message':{'chat':{'id':456},'text':'/status'}}, {'update_id':2,'message':{'chat':{'id':123},'text':'/status'}}]):
            msgs,cursor=bot.poll_all_messages();self.assertEqual(len(msgs),1);self.assertEqual(cursor,2)

    def test_status_does_not_present_unvalidated_candidates_as_trades(self):
        status=build_status({'validated':False,'reason':'need evidence','n':0},[])
        self.assertEqual(status['decision'],'WAIT')
        self.assertIn('WAIT means no trade',render_status(status))



class PersistenceRetryTests(unittest.TestCase):
    def test_monitor_retries_persisted_exit_without_new_bars(self):
        monitor=ActivePositionMonitor(None,None)
        state=[trade()]
        def save(rows):state[:]=rows
        with patch.object(monitor,'load_positions',side_effect=lambda:state),patch.object(monitor,'save_positions',side_effect=save),patch.object(DispatchLedger,'record_close',side_effect=[False,True]) as close,patch('position_monitor.EngineEfficiencyTracker.record_trade_outcome') as efficiency,patch('position_monitor.SignalCooldownEngine.record_outcome'),patch.object(monitor,'send_telegram_alert'):
            monitor.check_active_positions('BTC/USDT',104,1,False,bars=[bar(1,hi=104.5,close=104)])
            self.assertEqual(len(state),1)
            self.assertIn('execution_event',state[0])
            monitor.check_active_positions('BTC/USDT',104,1,False,bars=[])
            self.assertEqual(state,[])
            self.assertEqual(efficiency.call_count,1)
            self.assertEqual(close.call_count,2)

if __name__=='__main__':unittest.main()
