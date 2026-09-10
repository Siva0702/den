"""Entire scan-to-dispatch loop with network and storage boundaries replaced."""
from contextlib import ExitStack, redirect_stdout
import importlib
import io
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'models'))
with patch('dotenv.load_dotenv'):
    scanner=importlib.import_module('auto_scanner')


class ScannerFlowTests(unittest.TestCase):
    def run_scan(self, delivered=123, validated=True, probability=.8, fresh=True):
        now=time.time();minute=int(now//60)*60
        df=pd.DataFrame([dict(timestamp=(minute-(99-i)*60)*1000,open=100,high=100.3,low=99.7,close=100,volume=10) for i in range(100)])
        df.attrs['fetched_at']=now
        frames={'ticker':'BTC/USDT','ok':True,'item':{'ticker':'BTC/USDT'},
                'df_15m':df,'df_1h':df,'df_4h':df,'df_1d':df}
        signal=dict(direction='LONG',total_score=80,pillar_score=65,atr=1,timeframe_alignment=3,
                    feature_snapshot={'rsi':50},recommendation_label='QUALIFIED',market_regime='RANGING',
                    pillar_breakdown={},hunt_risk={'hunt_risk_score':0},liquidity={},factors_passed=[],
                    factors_failed=[])
        model=dict(available=True,tradable=validated,score=88,prob=.85,prob_lower=probability,
                   version='test-only',reason='validated' if validated else 'failed chronological validation')
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            def mock(target,**kw):return stack.enter_context(patch(target,**kw))
            mock('requests.sessions.Session.request',side_effect=AssertionError('unexpected real network request'))
            mock('auto_scanner.__file__',new=str(Path(tmp)/'auto_scanner.py'))
            mock('auto_scanner.PRELIM_CACHE',new={});mock('auto_scanner.signal_timestamps',new=[])
            mock('auto_scanner.last_digest_time',new=now);mock('auto_scanner.dispatched_message_ids',new={})
            mock('auto_scanner.scanner_state',new=dict(scanner.scanner_state,total_scans=0,total_signals_sent=0))
            mock('auto_scanner.DynamicMarketUniverse.get_full_hunting_universe',return_value=[{'ticker':'BTC/USDT'}])
            mock('auto_scanner.fetch_asset_frames',return_value=frames)
            mock('auto_scanner.BitunixWeexLiveFeed.get_exchange_ohlcv',return_value=(df,True) if fresh else (None,False))
            monitor=mock('auto_scanner.monitor');monitor.load_positions.return_value=[]
            mock('auto_scanner.EngineEfficiencyTracker.load_efficiency_data',return_value={})
            mock('auto_scanner.ShadowTradeLedger.load_open',return_value=[])
            mock('auto_scanner.ShadowTradeLedger.update_prices',return_value=[])
            shadow=mock('auto_scanner.ShadowTradeLedger.open_shadow_trade',return_value=True)
            mock('auto_scanner.SureShotConfluenceEngine.evaluate_setup',return_value=signal)
            mock('auto_scanner.DerivativesIntelligence.analyze',return_value={})
            mock('auto_scanner.DerivativesIntelligence.snapshot')
            mock('auto_scanner.PerAssetNewsIntelligence.analyze',return_value={})
            mock('auto_scanner.PerAssetNewsIntelligence._fetch_headlines',return_value=[])
            mock('news.learned_sentiment.LearnedNewsSentiment.observe')
            mock('news.learned_sentiment.LearnedNewsSentiment.settle',return_value=0)
            mock('auto_scanner.ScheduledEventCalendar.assess',return_value={'available':True})
            mock('auto_scanner.EventOutcomeLearner.track')
            mock('auto_scanner.EventOutcomeLearner.sample',return_value=0)
            mock('auto_scanner.EventVolatilityEngine.analyze',return_value={})
            mock('auto_scanner.WinRateCalibrator.build_model',return_value={'status':'UNCALIBRATED','total_samples':0})
            mock('auto_scanner.WinRateCalibrator.calibrated_win_rate',return_value={'status':'UNCALIBRATED','win_rate':None})
            mock('auto_scanner.LiquidityMapEngine.safe_stop_loss',return_value={'stop_loss':99.,'sl_pct':.01,'rationale':[]})
            mock('auto_scanner.build_tp_ladder',return_value=[102.,103.,104.,105.])
            mock('auto_scanner.CalibratedScoreModel.score',return_value=model)
            mock('auto_scanner.CalibratedScoreModel.build',return_value={'validated':validated,'reason':model['reason'],'n':500})
            mock('auto_scanner.ExchangeLeverageEngine.get_calibrated_leverage',return_value={'recommended_leverage':5})
            mock('auto_scanner.ScoreStabilityTracker.evaluate',return_value={'stable':True})
            mock('auto_scanner.ScoreStabilityTracker.record')
            mock('auto_scanner.SignalCooldownEngine.check',return_value=(True,'clear'))
            cooldown=mock('auto_scanner.SignalCooldownEngine.record_signal_sent')
            send=mock('auto_scanner.telegram.send_alert',return_value=delivered)
            ledger=mock('auto_scanner.DispatchLedger.record_dispatch')
            status=mock('auto_scanner.save_status')
            output=io.StringIO()
            with redirect_stdout(output):scanner.run_continuous_quant_hunter()
            self.assertNotIn('Enrichment error',output.getvalue())
            result={'sent':send.call_count,'ledger':ledger.call_count,'cooldown':cooldown.call_count,
                    'positions':monitor.save_positions.call_count,'shadow':shadow.call_count,
                    'count':scanner.scanner_state['total_signals_sent'],
                    'status':status.call_args.args[0],
                    'candidate':shadow.call_args.args[0] if shadow.called else None}
            return result

    def test_validated_positive_edge_can_complete_scan_and_dispatch(self):
        r=self.run_scan()
        self.assertEqual((r['sent'],r['ledger'],r['positions'],r['count']),(1,1,1,1))
        self.assertEqual(r['status']['decision'],'SIGNAL SENT')
        self.assertLessEqual(r['candidate']['exact_loss_usd'],10)

    def test_failed_send_does_not_create_trade_or_consume_quota(self):
        r=self.run_scan(delivered=None)
        self.assertEqual((r['sent'],r['ledger'],r['positions'],r['count'],r['cooldown']),(1,0,0,0,0))
        self.assertEqual(r['status']['decision'],'WAIT')

    def test_unvalidated_model_keeps_learning_without_dispatch(self):
        r=self.run_scan(validated=False)
        self.assertEqual((r['sent'],r['ledger'],r['shadow']),(0,0,1))
        self.assertEqual(r['status']['decision'],'WAIT')

    def test_negative_edge_stays_shadow_only(self):
        r=self.run_scan(probability=.2)
        self.assertEqual((r['sent'],r['shadow']),(0,1))
        self.assertEqual(r['candidate']['kelly']['dollars_at_risk'],0)

    def test_failed_execution_feed_does_not_open_at_cached_15m_price(self):
        r=self.run_scan(fresh=False)
        self.assertEqual((r['sent'],r['shadow'],r['ledger']),(0,0,0))


if __name__=='__main__':unittest.main()
