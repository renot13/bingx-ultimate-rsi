"""BingX hourly screener. Ultimate RSI © LuxAlgo, adapted under CC BY-NC-SA 4.0.
See NOTICE.md. No order execution or exchange credentials are used.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo

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


def render_chart(frame: pd.DataFrame, symbol: str, path: Path) -> None:
    visible = frame.tail(50).copy()
    # Local naive dates avoid mplfinance converting the axis back to UTC.
    visible.index = visible.index.tz_localize(None)
    overlays = [
        mpf.make_addplot(visible.ARSI, panel=1, color='#4b4b4b', width=1.2, ylim=(0, 100)),
        mpf.make_addplot(visible.Signal, panel=1, color='#ff952f', width=1.1, secondary_y=False),
        mpf.make_addplot(pd.Series(20, index=visible.index), panel=1, color='#ff6577', width=0.9, linestyle='--', secondary_y=False),
        mpf.make_addplot(pd.Series(80, index=visible.index), panel=1, color='#39ceab', width=0.9, linestyle='--', secondary_y=False),
    ]
    market_colors = mpf.make_marketcolors(up='#b8b8b8', down='#626262',
                                         edge='#4b4b4b', wick='#4b4b4b')
    style = mpf.make_mpf_style(base_mpf_style='classic', marketcolors=market_colors,
                              facecolor='white', figcolor='white',
                              rc={'axes.grid': False, 'font.size': 9, 'axes.labelcolor': '#555555',
                                  'xtick.color': '#555555', 'ytick.color': '#555555'})
    close_time = datetime.fromtimestamp((int(frame.Timestamp.iloc[-1]) + HOUR) / 1000, TOKYO).strftime('%Y-%m-%d %H:%M JST')
    title = f'{symbol} | BingX USDT-M | 1h\nClosed: {close_time} | Ultimate RSI: {frame.ARSI.iloc[-1]:.2f}'
    fig, axes = mpf.plot(visible, type='candle', style=style, addplot=overlays,
                         panel_ratios=(3.5, 1), volume=False, figsize=(14, 6.5),
                         update_width_config={'candle_width': 0.48, 'candle_linewidth': 0.8},
                         ylabel='', datetime_format='%m-%d %H:%M',
                         xrotation=0, returnfig=True, tight_layout=False)
    fig.suptitle(title, y=0.97, color='#333333', fontsize=11)
    # Use most of the canvas, reserving only space for header, ticks and credit.
    bottom, total_height = 0.10, 0.78
    rsi_height = total_height / 4.5
    for axis in axes[:2]:
        axis.set_position([0.025, bottom + rsi_height, 0.91, total_height - rsi_height])
    for axis in axes[2:]:
        axis.set_position([0.025, bottom, 0.91, rsi_height])
    for axis in axes:
        axis.set_ylabel('')
        axis.set_xlabel('')
        axis.grid(False, which='both', axis='both')
        axis.tick_params(labelsize=8)
        for spine in axis.spines.values():
            spine.set_visible(False)
    # Outline primary panels only; keep internal chart grid disabled.
    for axis in (axes[0], axes[2]):
        for spine in axis.spines.values():
            spine.set_visible(True)
            spine.set_color('#333333')
            spine.set_linewidth(0.65)
    axes[2].axhspan(0, 20, color='#ff6577', alpha=0.05)
    fig.text(0.5, 0.012, 'Asia/Tokyo | © LuxAlgo · CC BY-NC-SA 4.0 | Python adaptation',
             ha='center', color='#777777', fontsize=7)
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
                if not isinstance(self.data, dict) or any(
                    not isinstance(v, dict) or not isinstance(v.get('day'), str)
                    or v.get('status') not in ('pending', 'sent') for v in self.data.values()
                ):
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
                    data={'chat_id': chat, 'caption': caption}, files={'photo': photo}, timeout=(10, 60))
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
    symbols = sorted(m['symbol'] for m in markets if m.get('active') is True
                     and m.get('swap') and m.get('linear')
                     and m.get('quote') == 'USDT' and m.get('settle') == 'USDT')
    if not symbols:
        raise RuntimeError('Tidak ada pasar BingX USDT-M aktif yang ditemukan')
    if args.limit and len(symbols) > args.limit:
        tickers = retry_read(exchange.fetch_tickers, symbols, {'type': 'swap'})
        def volume(symbol):
            ticker = tickers.get(symbol, {})
            value = ticker.get('quoteVolume')
            if value is None:
                value = (ticker.get('baseVolume') or 0) * (ticker.get('last') or 0)
            return float(value) if math.isfinite(float(value)) else 0
        symbols = sorted(symbols, key=lambda s: (-volume(s), s))[:args.limit]
    now_ms = retry_read(exchange.fetch_time)
    if not isinstance(now_ms, int):
        raise RuntimeError('Waktu server BingX tidak valid')
    target_day = tokyo_day(now_ms)
    successful = skipped = errors = sent = matches = 0
    LOG.info('Memeriksa %d pasar; tanggal Tokyo %s; dry_run=%s', len(symbols), target_day, dry)
    for symbol in symbols:
        if state and not eligible(state.data, symbol, target_day):
            skipped += 1
            continue
        try:
            rows = retry_read(exchange.fetch_ohlcv, symbol, '1h', limit=1000)
            frame = closed_frame(rows, now_ms)
            successful += 1
            arsi = float(frame.ARSI.iloc[-1])
            LOG.info('%s Ultimate RSI %.4f', symbol, arsi)
            if dry and args.preview_dir and successful == 1:
                args.preview_dir.mkdir(parents=True, exist_ok=True)
                render_chart(frame, symbol, args.preview_dir / 'preview.png')
            if arsi >= 20:
                continue
            matches += 1
            close_time = datetime.fromtimestamp((int(frame.Timestamp.iloc[-1]) + HOUR) / 1000, TOKYO).strftime('%Y-%m-%d %H:%M JST')
            caption = (f'OVERSOLD | {symbol}\nBingX USDT-M Perpetual | 1 jam\n'
                       f'Close: {frame.Close.iloc[-1]:.10g} USDT\nUltimate RSI: {arsi:.4f} (<20)\n'
                       f'Candle tutup: {close_time}\nMaksimal 1 sinyal/koin/hari Tokyo\n'
                       'Ultimate RSI © LuxAlgo')
            if dry:
                if args.preview_dir and matches == 1:
                    render_chart(frame, symbol, args.preview_dir / 'signal-preview.png')
                LOG.info('DRY RUN sinyal: %s', caption.replace('\n', ' | '))
                continue
            # If a manual run crosses midnight, stop rather than assigning an old
            # candle to a new delivery day. Scheduled runs are capped at 45 min.
            if datetime.now(TOKYO).date().isoformat() != target_day:
                raise StateError('Hari Tokyo berubah; tunggu pemindaian berikutnya')
            with tempfile.TemporaryDirectory(prefix='bingx-chart-') as temp:
                path = Path(temp) / 'chart.png'
                render_chart(frame, symbol, path)
                state.data[symbol] = {'day': target_day, 'status': 'pending', 'candle_close': close_time}
                state.save()  # Reserve durably BEFORE irreversible delivery.
                try:
                    message_id = send_photo(os.environ['TG_TOKEN'], os.environ['TG_CHAT_ID'], path, caption)
                except ValueError:
                    del state.data[symbol]
                    state.save()
                    raise
                state.data[symbol].update(status='sent', message_id=message_id)
                state.save()
                sent += 1
                time.sleep(1.1)  # Short per-chat pacing, not a continuously running bot.
        except StateError:
            raise
        except Exception as exc:
            errors += 1
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
