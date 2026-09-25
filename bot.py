"""BingX hourly screener. Ultimate RSI © LuxAlgo, adapted under CC BY-NC-SA 4.0.
See NOTICE.md. No order execution or exchange credentials are used.
"""
from __future__ import annotations

import argparse
import base64
from html import escape
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote

import ccxt
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd
import requests

LOG = logging.getLogger('screener')
TOKYO = ZoneInfo('Asia/Tokyo')
HOUR = 3_600_000
MAX_CATCHUP_CANDLES = 24


def rma(series: pd.Series, length: int) -> pd.Series:
    """Pine RMA: seed with SMA of first length non-NA values, then Wilder."""
    result = []
    seed = []
    previous = math.nan
    for value in series:
        if pd.notna(value):
            if math.isnan(previous):
                seed.append(float(value))
                if len(seed) == length:
                    previous = sum(seed) / length
            else:
                previous = (previous * (length - 1) + float(value)) / length
        result.append(previous)
    return pd.Series(result, index=series.index)


def ultimate_rsi(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    # Pine highest/lowest include available initial history; source is CLOSE.
    upper = close.rolling(14, min_periods=1).max()
    lower = close.rolling(14, min_periods=1).min()
    spread = upper - lower
    diff = close.diff()
    diff = diff.where(~(lower < lower.shift()), -spread)
    # Upper breakout has priority, matching the nested Pine ternary.
    diff = diff.where(~(upper > upper.shift()), spread)
    denominator = rma(diff.abs(), 14)
    arsi = rma(diff, 14) / denominator.replace(0, np.nan) * 50 + 50
    signal = arsi.ewm(span=14, adjust=False, ignore_na=True).mean()
    return arsi, signal


def closed_frame(rows: list, now_ms: int) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
    frame = frame.drop_duplicates('Timestamp').sort_values('Timestamp')
    frame = frame.loc[frame.Timestamp + HOUR <= now_ms].copy()
    if len(frame) < 200:
        raise ValueError('Riwayat kurang dari 200 candle tertutup')
    expected = (now_ms // HOUR - 1) * HOUR
    if int(frame.Timestamp.iloc[-1]) != expected:
        raise ValueError('Candle terbaru tertinggal; tidak mengirim sinyal lama')
    if not (frame.Timestamp.diff().dropna() == HOUR).all():
        raise ValueError('Ada gap dalam riwayat candle')
    values = frame[['Open', 'High', 'Low', 'Close', 'Volume']].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, :4] <= 0).any():
        raise ValueError('Data harga tidak valid')
    if (frame.High < frame[['Open', 'Close', 'Low']].max(axis=1)).any() or (frame.Low > frame[['Open', 'Close', 'High']].min(axis=1)).any():
        raise ValueError('OHLC tidak konsisten')
    frame.index = pd.to_datetime(frame.Timestamp, unit='ms', utc=True).dt.tz_convert(TOKYO)
    frame['ARSI'], frame['Signal'] = ultimate_rsi(frame.Close)
    if not math.isfinite(float(frame.ARSI.iloc[-1])):
        raise ValueError('Ultimate RSI belum terdefinisi')
    return frame


def tokyo_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TOKYO).date().isoformat()


def eligible(state: dict, symbol: str, day: str) -> bool:
    return state.get(symbol, {}).get('day') != day


def is_active_usdt_perpetual(market: dict) -> bool:
    """Include all active linear USDT swaps, including commodities and indexes."""
    return (market.get('active') is True and market.get('swap') is True
            and market.get('linear') is True and market.get('quote') == 'USDT'
            and market.get('settle') == 'USDT' and bool(market.get('base'))
            and market.get('type') == 'swap' and market.get('spot') is not True
            and market.get('contract') is True)


def volume_ranks(symbols: list[str], tickers: dict) -> tuple[list[str], dict[str, int]]:
    """Order candidate USDT markets by 24h volume, with stable tie-breaks."""
    def quote_volume(symbol: str) -> float:
        ticker = tickers.get(symbol, {})
        value = ticker.get('quoteVolume')
        if value is None:
            value = (ticker.get('baseVolume') or 0) * (ticker.get('last') or 0)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) and value >= 0 else 0.0

    ordered = sorted(symbols, key=lambda symbol: (-quote_volume(symbol), symbol))
    return ordered, {symbol: rank for rank, symbol in enumerate(ordered, start=1)}


def tradingview_url(symbol: str) -> str:
    """Link directly to the BingX USDT-M perpetual chart, not the spot pair."""
    if not symbol.endswith('/USDT:USDT'):
        raise ValueError('Bukan pasangan BingX USDT-M perpetual')
    ticker = symbol.split('/', 1)[0] + 'USDT.P'
    return 'https://www.tradingview.com/chart/?symbol=' + quote('BINGX:' + ticker, safe='') + '&interval=60'


def signal_caption(symbol: str, close: float, arsi: float, close_time: str,
                   volume_rank: int) -> str:
    pair = symbol.split('/', 1)[0] + '/USDT'
    url = tradingview_url(symbol)
    return (f'<b>▣ 📊 MARKET INFO</b>\n'
            f'│ 🪙 <b>Pair:</b> <code>{escape(pair)}</code>\n'
            f'│ 🌐 <b>Market:</b> BingX USDT-M Perpetual\n'
            f'│ 📊 <b>Peringkat volume 24j:</b> #{volume_rank}\n'
            f'│ ⏱ <b>TF:</b> 1h\n'
            f'│ 🕒 <b>Candle:</b> {escape(close_time)}\n\n'
            f'<b>▣ 🎯 TECHNICAL TRIGGER</b>\n'
            f'│ ⚠️ <b>Status:</b> <b>OVERSOLD</b>\n'
            f'│ 💵 <b>Price:</b> <b>{close:.10g} USDT</b>\n'
            f'│ 📉 <b>RSI:</b> <b>{arsi:.4f}</b> (&lt;20)\n'
            f'│ 🔗 <a href="{url}">Buka chart BingX di TradingView</a>\n'
            f'└ © <a href="https://creativecommons.org/licenses/by-nc-sa/4.0/">LuxAlgo · adaptasi Python · CC BY-NC-SA 4.0</a>')


def catchup_open_times(last_open_ms: int | None, latest_open_ms: int) -> list[int]:
    """Process missed closed candles in order, bounded to one day of history."""
    if last_open_ms is None:
        return [latest_open_ms]
    if last_open_ms > latest_open_ms or last_open_ms % HOUR:
        raise StateError('Posisi pemindaian tidak valid; hentikan agar tidak melewatkan sinyal')
    first = max(last_open_ms + HOUR, latest_open_ms - (MAX_CATCHUP_CANDLES - 1) * HOUR)
    return list(range(first, latest_open_ms + 1, HOUR))


def render_chart(frame: pd.DataFrame, symbol: str, path: Path) -> None:
    visible = frame.tail(100).copy()
    # Local naive dates avoid mplfinance converting the axis back to UTC.
    visible.index = visible.index.tz_localize(None)
    market_colors = mpf.make_marketcolors(up='#b8b8b8', down='#626262',
                                         edge='#4b4b4b', wick='#4b4b4b')
    style = mpf.make_mpf_style(base_mpf_style='classic', marketcolors=market_colors,
                              facecolor='white', figcolor='white',
                              rc={'axes.grid': False, 'font.size': 9, 'axes.labelcolor': '#555555',
                                  'xtick.color': '#555555', 'ytick.color': '#555555'})
    close_time = datetime.fromtimestamp((int(frame.Timestamp.iloc[-1]) + HOUR) / 1000, TOKYO).strftime('%Y-%m-%d %H:%M JST')
    title = f'{symbol} | BingX USDT-M | 1h\nClosed: {close_time}'
    fig, axes = mpf.plot(visible, type='candle', style=style,
                         volume=False, figsize=(16, 7),
                         update_width_config={'candle_width': 0.52, 'candle_linewidth': 0.8},
                         ylabel='', datetime_format='%m-%d %H:%M',
                         xrotation=0, returnfig=True, tight_layout=False)
    fig.suptitle(title, y=0.97, color='#333333', fontsize=11)
    # Use the full plotting area for price candles; RSI stays in the alert only.
    for axis in axes:
        axis.set_ylabel('')
        axis.set_xlabel('')
        axis.grid(False, which='both', axis='both')
        axis.tick_params(labelsize=8)
        for spine in axis.spines.values():
            spine.set_visible(False)
    for spine in axes[0].spines.values():
        spine.set_visible(True)
        spine.set_color('#333333')
        spine.set_linewidth(0.65)
    try:
        fig.savefig(path, dpi=130, bbox_inches='tight', facecolor=fig.get_facecolor())
    finally:
        plt.close(fig)


def retry_read(function, *args, **kwargs):
    for attempt in range(3):
        try:
            return function(*args, **kwargs)
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


class StateError(RuntimeError):
    pass


class GitHubState:
    """Durable state with optimistic concurrency. Fail closed on any error."""
    def __init__(self):
        repo = os.environ['GITHUB_REPOSITORY']
        self.branch = os.getenv('STATE_BRANCH', 'bot-state')
        self.base = f'https://api.github.com/repos/{repo}'
        self.session = requests.Session()
        self.session.headers.update({'Authorization': f'Bearer {os.environ["GITHUB_TOKEN"]}',
                                     'Accept': 'application/vnd.github+json',
                                     'X-GitHub-Api-Version': '2022-11-28'})
        self.sha = None
        self.data = {}
        branch = self.call('GET', f'/git/ref/heads/{self.branch}', allow404=True)
        if branch is None:
            ref = self.call('GET', '')['default_branch']
            head = self.call('GET', f'/git/ref/heads/{ref}')['object']['sha']
            self.call('POST', '/git/refs', json={'ref': f'refs/heads/{self.branch}', 'sha': head})
        content = self.call('GET', '/contents/state.json', params={'ref': self.branch}, allow404=True)
        if content is not None:
            self.sha = content['sha']
            try:
                self.data = json.loads(base64.b64decode(content['content']))
                if not isinstance(self.data, dict):
                    raise ValueError('Invalid state')
                meta = self.data.get('__meta__', {})
                if (not isinstance(meta, dict)
                    or (meta and (not isinstance(meta.get('last_open_ms'), int)
                                  or meta['last_open_ms'] < 0))
                    or any(
                        not isinstance(v, dict) or not isinstance(v.get('day'), str)
                        or v.get('status') not in ('pending', 'sent')
                        for k, v in self.data.items() if k != '__meta__'
                    )):
                    raise ValueError('Invalid state')
            except Exception:
                raise StateError('Riwayat rusak; hentikan agar tidak mengirim duplikat') from None

    def call(self, method, path, allow404=False, **kwargs):
        try:
            response = self.session.request(method, self.base + path, timeout=(10, 30), **kwargs)
            if allow404 and response.status_code == 404:
                return None
            if not response.ok:
                raise StateError(f'GitHub state HTTP {response.status_code}')
            return response.json()
        except requests.RequestException:
            raise StateError('GitHub state tidak dapat diakses') from None

    def save(self):
        payload = {'message': 'Update screener delivery state', 'branch': self.branch,
                   'content': base64.b64encode(json.dumps(self.data, indent=2).encode()).decode()}
        if self.sha:
            payload['sha'] = self.sha
        result = self.call('PUT', '/contents/state.json', json=payload)
        self.sha = result['content']['sha']


class DeliveryUnknown(RuntimeError):
    pass


def send_photo(token: str, chat: str, path: Path, caption: str) -> int:
    # Never automatically retry a timed-out POST: Telegram may have accepted it.
    for attempt in range(3):
        try:
            with path.open('rb') as photo:
                response = requests.post(f'https://api.telegram.org/bot{token}/sendPhoto',
                    data={'chat_id': chat, 'caption': caption, 'parse_mode': 'HTML'},
                    files={'photo': photo}, timeout=(10, 60))
            result = response.json()
        except (requests.RequestException, ValueError):
            raise DeliveryUnknown('Status Telegram tidak pasti; reservasi dipertahankan') from None
        if result.get('ok'):
            return result['result']['message_id']
        if response.status_code >= 500:
            raise DeliveryUnknown('Telegram server error; reservasi dipertahankan')
        delay = result.get('parameters', {}).get('retry_after', 2)
        if result.get('error_code') == 429 and attempt < 2 and 0 <= delay <= 30:
            time.sleep(delay + 1)
            continue
        # Explicit rejection: message was not accepted; safe to remove reservation.
        raise ValueError(f'Telegram menolak pengiriman: kode {result.get("error_code", response.status_code)}')
    raise DeliveryUnknown('Pengiriman tidak terkonfirmasi')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int, default=int(os.getenv('MAX_COINS', '200')))
    parser.add_argument('--preview-dir', type=Path, help='Simpan contoh chart saat dry-run')
    args = parser.parse_args()
    dry = args.dry_run or os.getenv('DRY_RUN') == '1'
    if args.limit < 0:
        raise ValueError('Limit harus >= 0; 0 berarti semua pasar aktif')
    if not dry and (not os.getenv('TG_TOKEN') or not os.getenv('TG_CHAT_ID')):
        raise ValueError('Isi GitHub Secrets TG_TOKEN dan TG_CHAT_ID terlebih dahulu')
    state = None if dry else GitHubState()
    exchange = ccxt.bingx({'enableRateLimit': True, 'timeout': 20000,
                           'options': {'defaultType': 'swap'}})
    # Only load the USDT-M swap catalog, avoiding unnecessary spot endpoints.
    markets = retry_read(exchange.fetch_swap_markets, {})
    exchange.set_markets(markets)
    symbols = sorted(m['symbol'] for m in markets if is_active_usdt_perpetual(m))
    if not symbols:
        raise RuntimeError('Tidak ada pasar BingX USDT-M aktif yang ditemukan')
    tickers = retry_read(exchange.fetch_tickers, symbols, {'type': 'swap'})
    ranked_symbols, volume_rank = volume_ranks(symbols, tickers)
    symbols = ranked_symbols[:args.limit] if args.limit else ranked_symbols
    now_ms = retry_read(exchange.fetch_time)
    if not isinstance(now_ms, int):
        raise RuntimeError('Waktu server BingX tidak valid')
    target_day = tokyo_day(now_ms)
    latest_open_ms = (now_ms // HOUR - 1) * HOUR
    last_open_ms = None if dry else state.data.get('__meta__', {}).get('last_open_ms')
    opens = catchup_open_times(last_open_ms, latest_open_ms)
    if state and last_open_ms is None:
        # Preserve a retryable starting point even if this first scan has an error.
        state.data['__meta__'] = {'last_open_ms': latest_open_ms - HOUR}
        state.save()
    if not opens:
        LOG.info('Candle terbaru sudah dipindai; tidak ada candle baru.')
        return
    if last_open_ms is not None and opens[0] > last_open_ms + HOUR:
        LOG.warning('Jeda terlalu panjang; hanya %d candle tertutup terakhir diproses.', len(opens))
    successful = skipped = errors = sent = matches = delivery_errors = 0
    LOG.info('Memeriksa %d pasar; %d candle tertutup; tanggal Tokyo %s; dry_run=%s',
             len(symbols), len(opens), target_day, dry)
    for symbol in symbols:
        if state and not eligible(state.data, symbol, target_day):
            skipped += 1
            continue
        attempted_delivery = False
        try:
            rows = retry_read(exchange.fetch_ohlcv, symbol, '1h', limit=1000)
            frame = closed_frame(rows, now_ms)
            successful += 1
            if dry and args.preview_dir and successful == 1:
                args.preview_dir.mkdir(parents=True, exist_ok=True)
                render_chart(frame, symbol, args.preview_dir / 'preview.png')
            for candle_open_ms in opens:
                candle_frame = frame.loc[frame.Timestamp <= candle_open_ms]
                if candle_frame.empty or int(candle_frame.Timestamp.iloc[-1]) != candle_open_ms:
                    raise ValueError('Candle catch-up tidak tersedia')
                arsi = float(candle_frame.ARSI.iloc[-1])
                if not math.isfinite(arsi):
                    raise ValueError('Ultimate RSI candle catch-up belum terdefinisi')
                LOG.info('%s candle %s Ultimate RSI %.4f', symbol, candle_open_ms, arsi)
                if arsi >= 20:
                    continue
                matches += 1
                close_time = datetime.fromtimestamp((candle_open_ms + HOUR) / 1000, TOKYO).strftime('%Y-%m-%d %H:%M JST')
                caption = signal_caption(symbol, float(candle_frame.Close.iloc[-1]),
                                         arsi, close_time, volume_rank[symbol])
                if dry:
                    if args.preview_dir and matches == 1:
                        render_chart(candle_frame, symbol, args.preview_dir / 'signal-preview.png')
                    LOG.info('DRY RUN sinyal: %s', caption.replace('\n', ' | '))
                    continue
                # A signal on an older missed candle counts toward today's delivery limit.
                if datetime.now(TOKYO).date().isoformat() != target_day:
                    raise StateError('Hari Tokyo berubah; tunggu pemindaian berikutnya')
                with tempfile.TemporaryDirectory(prefix='bingx-chart-') as temp:
                    path = Path(temp) / 'chart.png'
                    render_chart(candle_frame, symbol, path)
                    state.data[symbol] = {'day': target_day, 'status': 'pending', 'candle_close': close_time}
                    state.save()  # Reserve durably BEFORE irreversible delivery.
                    attempted_delivery = True
                    try:
                        message_id = send_photo(os.environ['TG_TOKEN'], os.environ['TG_CHAT_ID'], path, caption)
                    except ValueError:
                        del state.data[symbol]
                        state.save()
                        raise
                    state.data[symbol].update(status='sent', message_id=message_id)
                    state.save()
                    sent += 1
                    time.sleep(1.1)
                break  # At most one signal per coin per Tokyo delivery day.
        except StateError:
            raise
        except Exception as exc:
            errors += 1
            if attempted_delivery:
                delivery_errors += 1
            # Never print requests exception URLs; they can include the bot token.
            LOG.warning('%s gagal (%s)', symbol, type(exc).__name__)
    summary = (f'Pasar: {len(symbols)} | Diperiksa: {successful} | Sudah diproses hari ini: {skipped} | '
               f'Kondisi <20: {matches} | Terkirim: {sent} | Error: {errors} | Dry run: {dry}')
    LOG.info(summary)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write(summary + '\n')
    if successful == 0 or errors >= max(1, len(symbols) // 10):
        raise RuntimeError('Terlalu banyak pasar gagal diperiksa; periksa log.')
    if state and delivery_errors == 0:
        # An isolated bad market must not hold the global catch-up cursor back.
        # A failed Telegram delivery remains retryable at the next run.
        state.data['__meta__'] = {'last_open_ms': latest_open_ms}
        state.save()
    if errors:
        LOG.warning('%d pasar dilewati karena error; pasar lain berhasil diperiksa.', errors)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        main()
    except Exception as exc:
        LOG.error('Bot berhenti (%s): %s', type(exc).__name__,
                  str(exc) if isinstance(exc, (ValueError, StateError, RuntimeError)) else 'Periksa koneksi/configuration')
        raise SystemExit(1)
