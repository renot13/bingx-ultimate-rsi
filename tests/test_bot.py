import unittest
from unittest.mock import patch, MagicMock
import numpy as np
import pandas as pd

from bot import HOUR, catchup_open_times, closed_frame, eligible, rma, ultimate_rsi, tokyo_day, send_photo, signal_caption, tradingview_url, DeliveryUnknown, GitHubState, StateError, is_active_usdt_perpetual, volume_ranks
import requests


class IndicatorTests(unittest.TestCase):
    def test_wilder_seed(self):
        result = rma(pd.Series([np.nan, 1., 2., 3., 6.]), 3)
        self.assertTrue(np.isnan(result.iloc[2]))
        self.assertEqual(result.iloc[3], 2.)
        self.assertAlmostEqual(result.iloc[4], 10 / 3)

    def test_monotonic_series(self):
        for values, expected in [(range(1, 301), 100), (range(301, 1, -1), 0)]:
            arsi, signal = ultimate_rsi(pd.Series(values, dtype=float))
            self.assertAlmostEqual(arsi.iloc[-1], expected)
            self.assertAlmostEqual(signal.iloc[-1], expected)

    def test_flat_is_undefined_not_fake_oversold(self):
        arsi, _ = ultimate_rsi(pd.Series([10.] * 300))
        self.assertTrue(arsi.isna().all())

    def test_reference_scalar_pine_formula(self):
        close = np.random.default_rng(4).uniform(20, 100, 300)
        upper, lower, changes = [], [], []
        for i, value in enumerate(close):
            upper.append(max(close[max(0, i-13):i+1]))
            lower.append(min(close[max(0, i-13):i+1]))
            if i == 0:
                changes.append(np.nan)
            elif upper[i] > upper[i-1]:
                changes.append(upper[i]-lower[i])
            elif lower[i] < lower[i-1]:
                changes.append(lower[i]-upper[i])
            else:
                changes.append(value-close[i-1])
        numerator = sum(changes[1:15])/14
        denominator = sum(abs(x) for x in changes[1:15])/14
        expected = numerator/denominator*50+50
        ema = expected
        for value in changes[15:]:
            numerator += (value-numerator)/14
            denominator += (abs(value)-denominator)/14
            expected = numerator/denominator*50+50
            ema += (expected-ema)*2/15
        arsi, signal = ultimate_rsi(pd.Series(close))
        self.assertAlmostEqual(arsi.iloc[-1], expected)
        self.assertAlmostEqual(signal.iloc[-1], ema)


class CandleTests(unittest.TestCase):
    def rows(self, count):
        return [[i*HOUR, i+10, i+12, i+9, i+11, 100] for i in range(count)]

    def test_exclude_open_and_keep_closed_last(self):
        for count in (250, 251):
            frame = closed_frame(self.rows(count), 250*HOUR+60_000)
            self.assertEqual(len(frame), 250)
            self.assertEqual(frame.Timestamp.iloc[-1], 249*HOUR)

    def test_reject_stale_or_gapped(self):
        with self.assertRaises(ValueError):
            closed_frame(self.rows(249), 250*HOUR)
        rows = self.rows(250)
        del rows[100]
        with self.assertRaises(ValueError):
            closed_frame(rows, 250*HOUR)

    def test_tokyo_midnight(self):
        before = int(pd.Timestamp('2026-09-20T14:59:59Z').timestamp()*1000)
        self.assertEqual(tokyo_day(before), '2026-09-20')
        self.assertEqual(tokyo_day(before+1000), '2026-09-21')

    def test_daily_limit_including_pending(self):
        for status in ('pending', 'sent'):
            state = {'ABC': {'day': '2026-09-20', 'status': status}}
            self.assertFalse(eligible(state, 'ABC', '2026-09-20'))
            self.assertTrue(eligible(state, 'ABC', '2026-09-21'))
            self.assertTrue(eligible(state, 'XYZ', '2026-09-20'))

    def test_telegram_timeout_not_retried(self):
        from pathlib import Path
        import tempfile
        with tempfile.NamedTemporaryFile() as file:
            with patch('bot.requests.post', side_effect=requests.Timeout) as post:
                with self.assertRaises(DeliveryUnknown):
                    send_photo('dummy', 'dummy', Path(file.name), 'test')
                self.assertEqual(post.call_count, 1)

    def test_caption_has_copyable_ticker_and_bingx_chart(self):
        caption = signal_caption('ON/USDT:USDT', 0.1456, 18.6659, '2026-09-21 20:00 JST', 2)
        self.assertIn('│ 🪙 <b>Pair:</b> ON/USDT', caption)
        self.assertIn('│ 🌐 <b>Market:</b> BingX USDT-M Perpetual', caption)
        self.assertIn('│ 📊 <b>Peringkat volume 24j:</b> #2', caption)
        self.assertIn('│ ⏱ <b>TF:</b> 1h', caption)
        self.assertIn('│ 🕒 <b>Candle:</b> 2026-09-21 20:00 JST', caption)
        self.assertIn('│ 💵 <b>Price:</b> <b>0.1456 USDT</b>', caption)
        self.assertIn('symbol=BINGX%3AONUSDT.P&interval=60', caption)
        self.assertIn('│ 📉 <b>RSI:</b> <b>18.6659</b> (&lt;20)', caption)
        with self.assertRaises(ValueError):
            tradingview_url('ON/USDT')

    def test_all_active_usdt_perpetuals_including_commodities_are_kept(self):
        def market(base, **extra):
            return {'active': True, 'swap': True, 'linear': True, 'quote': 'USDT',
                    'settle': 'USDT', 'base': base, 'type': 'swap', 'spot': False,
                    'contract': True, 'symbol': f'{base}/USDT:USDT', **extra}

        self.assertTrue(is_active_usdt_perpetual(market('BTC')))
        self.assertTrue(is_active_usdt_perpetual(market('XAU', baseName='Gold')))
        self.assertTrue(is_active_usdt_perpetual(market('XAUUSD', name='Gold perpetual')))
        self.assertFalse(is_active_usdt_perpetual(market('BTC', active=False)))

    def test_volume_rank_is_global_and_stable(self):
        ordered, ranks = volume_ranks(['AAA/USDT:USDT', 'BTC/USDT:USDT', 'CCC/USDT:USDT'], {
            'AAA/USDT:USDT': {'quoteVolume': 100},
            'BTC/USDT:USDT': {'quoteVolume': 300},
            'CCC/USDT:USDT': {'quoteVolume': 200},
        })
        self.assertEqual(ordered, ['BTC/USDT:USDT', 'CCC/USDT:USDT', 'AAA/USDT:USDT'])
        self.assertEqual(ranks['CCC/USDT:USDT'], 2)

    def test_catchup_candles_are_ordered_and_bounded(self):
        self.assertEqual(catchup_open_times(None, 10 * HOUR), [10 * HOUR])
        self.assertEqual(catchup_open_times(7 * HOUR, 10 * HOUR),
                         [8 * HOUR, 9 * HOUR, 10 * HOUR])
        self.assertEqual(catchup_open_times(10 * HOUR, 10 * HOUR), [])
        self.assertEqual(len(catchup_open_times(0, 100 * HOUR)), 24)
        with self.assertRaises(StateError):
            catchup_open_times(11 * HOUR, 10 * HOUR)


class StateTests(unittest.TestCase):
    def test_load_persisted_pending(self):
        import base64, json
        payload = {'ABC': {'day': '2026-09-20', 'status': 'pending'}}
        content = base64.b64encode(json.dumps(payload).encode()).decode()
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': 'test/test', 'GITHUB_TOKEN': 'fake'}):
            with patch.object(GitHubState, 'call', side_effect=[{}, {'sha': 'abc', 'content': content}]):
                state = GitHubState()
                self.assertFalse(eligible(state.data, 'ABC', '2026-09-20'))

    def test_corrupt_state_fails_closed(self):
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': 'test/test', 'GITHUB_TOKEN': 'fake'}):
            with patch.object(GitHubState, 'call', side_effect=[{}, {'sha': 'abc', 'content': 'bad!'}]):
                with self.assertRaises(StateError):
                    GitHubState()

    def test_conflict_does_not_overwrite(self):
        state = GitHubState.__new__(GitHubState)
        state.base = 'https://api.github.com/repos/test/test'
        state.branch, state.sha, state.data = 'bot-state', 'oldsha', {}
        state.session = MagicMock()
        state.session.request.return_value.status_code = 409
        state.session.request.return_value.ok = False
        with self.assertRaises(StateError):
            state.save()
        self.assertEqual(state.session.request.call_count, 1)
        self.assertEqual(state.sha, 'oldsha')


if __name__ == '__main__':
    unittest.main()
