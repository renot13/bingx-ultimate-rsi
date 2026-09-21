# BingX Ultimate RSI → Telegram

Screener **1 jam**, menggunakan candle **BingX USDT-M Perpetual**, bukan RSI
standar. Mengirim gambar candlestick + panel Ultimate RSI LuxAlgo ketika
**nilai Ultimate RSI yang belum dibulatkan < 20** pada candle tertutup terbaru.
Tidak menggunakan SMA20 atau syarat crossing. Tidak memasang order.

## Pengaturan siap pakai

- Setiap jam pada menit :01; GitHub dapat menunda atau melewatkan jadwal.
- Waktu gambar dan pergantian hari: **Asia/Tokyo (JST / UTC+9)**.
- **200 pasangan aktif dengan volume transaksi 24 jam terbesar**, dipilih ulang
  saat scan. Jika kurang dari 200, semua pasangan aktif diperiksa. Pemilihan
  berdasarkan quoteVolume, dengan baseVolume × last sebagai fallback.
- Maksimal satu pengiriman per pasangan per tanggal pengiriman Tokyo.
- Hari baru boleh mengirim kembali meskipun RSI masih <20.
- 1.000 candle diambil untuk warm-up; minimal 200 candle tertutup diperlukan.
  Koin baru dengan riwayat lebih pendek dilewati dan dilaporkan sebagai error.
- 50 candle terakhir pada gambar; sumbu waktu menunjukkan waktu BUKA candle,
  judul dan caption menunjukkan waktu TUTUP candle.
- Tampilan putih tanpa grid, candle naik abu-abu muda dan turun abu-abu tua,
  dengan sumbu/wick abu-abu gelap. Format melebar 14 × 6,5 inci, candle ramping.
  Area plot diperbesar dengan margin rapat; label samping Price/Ultimate RSI
  dihapus, angka harga dan indikator tetap terlihat.
  Garis level indikator 20/80 tetap ditampilkan sebagai acuan sinyal.
  Bingkai tipis gelap tetap terlihat di tepi chart dan pemisah panel RSI,
  termasuk batas atas serta sisi kanan dekat angka harga; grid interior mati.
- Ultimate RSI: length 14, source close, RMA; signal line EMA14; level 20/80.
- Gambar dibuat dari OHLCV perdagangan BingX, bukan screenshot aplikasi, dan
  bukan candle mark price. File sementara dibersihkan setelah percobaan kirim.

## Pasang di GitHub

1. Buat repository **Public** di GitHub, misalnya `bingx-ultimate-rsi`.
   Runner standar Linux pada repository publik gratis menurut ketentuan GitHub.
   Repository privat GitHub Free memiliki kuota 2.000 menit/bulan yang dibagi
   dengan workflow lain. Gambar tidak diunggah sebagai artifact GitHub.
2. Ekstrak ZIP, unggah ISI folder `bingx-screener` ke root repository. Pastikan
   `bot.py`, `requirements.txt`, `tests/`, dan **`.github/workflows/run_bot.yml`**
   berada di lokasi yang tepat. Folder `.github` mungkin tersembunyi di komputer;
   pada macOS tekan Command+Shift+. untuk menampilkannya. Jika folder itu tidak
   ikut unggah, buat file tersebut lewat GitHub → Add file → Create new file,
   lalu salin isi YAML dari paket ini. Jangan unggah ZIP saja.
3. Buat bot lewat akun resmi **@BotFather** di Telegram menggunakan `/newbot`.
   Salin token dan kirim `/start` ke bot baru. Jangan bagikan token dalam chat,
   source code, screenshot, atau commit.
4. Repository → Settings → Secrets and variables → Actions → New repository
   secret. Tambahkan **TG_TOKEN** dan **TG_CHAT_ID**. Untuk chat pribadi,
   TG_CHAT_ID adalah ID numerik chat, bukan username. Untuk grup, tambahkan bot,
   izinkan mengirim gambar, lalu gunakan ID grup (biasanya negatif).
   Salah satu cara mendapatkan ID: setelah mengirim `/start`, panggil API
   Telegram `getUpdates` secara lokal dan baca `result[].message.chat.id`.
   Jangan menempelkan URL berisi token ke issue atau log publik. Contoh lokal:

   ```python
   import getpass, requests
   token = getpass.getpass('Token bot: ')
   data = requests.get(f'https://api.telegram.org/bot{token}/getUpdates', timeout=20).json()
   for update in data.get('result', []):
       message = update.get('message', {})
       if 'chat' in message:
           print(message['chat']['id'])
   ```

5. Settings → Actions → General: pastikan Actions diizinkan. Workflow meminta
   `contents: write` untuk menyimpan riwayat di cabang `bot-state`. Jika kebijakan
   akun/organisasi melarang, izinkan workflow menulis. **Tidak perlu membuat PAT
   atau secret GITHUB_TOKEN**: token tersebut disediakan GitHub otomatis.
6. Actions → BingX Ultimate RSI Screener → Run workflow. Biarkan **dry_run**
   dicentang untuk mencoba tanpa pesan dan tanpa perubahan riwayat. Lihat
   ringkasan dan log. Kemudian jalankan dengan dry_run tidak dicentang untuk
   mengaktifkan pengiriman pada eksekusi manual. Jadwal otomatis selalu mode live.
   Tidak ada pesan Telegram jika belum ada koin dengan nilai <20.
7. Workflow harus ada di default branch (biasanya `main`). Cabang `bot-state`
   dibuat otomatis saat eksekusi live pertama; jangan menjadikannya default.

Laptop boleh mati setelah pemasangan. Jadwal publik dapat dinonaktifkan GitHub
setelah 60 hari tanpa aktivitas repository. Periksa tab Actions secara berkala.
Tidak ada jaminan eksekusi tepat pada menit :01 atau pemulihan candle yang
terlewat: bot hanya memeriksa candle tertutup terbaru saat mulai.

## Riwayat harian dan pengiriman ganda

`bot-state/state.json` menyimpan tanggal, candle, status, dan ID pesan, tanpa token
atau chat ID. Pada repository publik informasi sinyal itu juga publik. Workflow
dibatasi agar tidak berjalan bersamaan. Jangan menjalankan salinan live di repo
lain dengan chat yang sama karena riwayatnya terpisah.

Sebelum mengirim, bot menyimpan status `pending`. Setelah Telegram mengonfirmasi,
status menjadi `sent`. Penolakan eksplisit Telegram membebaskan reservasi untuk
dicoba pada scan berikutnya. Timeout atau server error mempertahankan `pending`:
pesan mungkin sudah diterima Telegram. Bot tidak mengirim ulang pasangan tersebut
pada hari yang sama. Jika proses berhenti setelah reservasi tetapi sebelum kirim,
sinyal bisa terlewat. Ini sengaja memprioritaskan tidak ada duplikat; Telegram dan
GitHub tidak menyediakan transaksi bersama yang menjamin pengiriman tepat sekali.
Untuk memulihkan pending, cek chat terlebih dahulu. Hanya jika yakin belum
terkirim, hapus entri pasangan di state.json melalui GitHub dan jalankan ulang.
Jangan menghapus semua riwayat atau cabang state saat bot aktif.

Tanggal memakai waktu pengiriman Tokyo; scan dihentikan jika melewati tengah
malam. Candle 23:00–00:00 dievaluasi sekitar 00:01 dan termasuk hari baru.

## Mengubah jumlah koin

Edit `MAX_COINS` dalam workflow: `'200'` untuk 200 pasangan teratas berdasarkan
volume, atau `'0'` untuk SEMUA pasar aktif. Daftar bukan hardcoded. Pasangan yang
keluar dari 200 besar tidak dipantau sampai masuk kembali.

## Tes lokal tanpa mengirim pesan

Gunakan Python 3.14 (sama dengan workflow dan pengujian lokal):

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python bot.py --dry-run --limit 3 --preview-dir charts
```

Dry-run tidak memerlukan kredensial Telegram/GitHub. Gambar contoh pertama
disimpan walaupun tidak oversold dan **tidak dikirim**. Live mode dirancang untuk
GitHub Actions, bukan dua proses lokal bersamaan. Gunakan varian dependency
yang lolos pengujian; versi dependensi utama dikunci dalam requirements.txt.

## Batas akurasi dan operasional

Rumus diterjemahkan dari Pine Script yang disediakan, termasuk seed RMA berbasis
SMA. Ini bukan `pandas_ta.rsi()`. Warm-up 1.000 candle mengurangi pengaruh awal
riwayat, tetapi kesamaan digit dengan TradingView belum dijamin tanpa perbandingan
data, panjang sejarah, exchange, jenis harga, dan pengaturan indikator yang sama.
Hasil scan bukan bukti keuntungan strategi.

CCXT rate limiter aktif; permintaan data memakai timeout dan retry terbatas.
Satu koin gagal tidak menghentikan koin lain. Pada akhir eksekusi, error membuat
workflow merah agar kegagalan tidak disamarkan sebagai "tidak ada sinyal".
Kegagalan penyimpanan riwayat menghentikan pengiriman demi menjaga deduplikasi.
Jeda Telegram pendek untuk pembatasan pengiriman; tidak ada loop layanan 24 jam.
Pemindaian 200 koin belum tentu selesai dalam kuota privat; ukur durasi di Actions.

## Atribusi dan sumber

Ultimate RSI © LuxAlgo; adaptasi ini memakai CC BY-NC-SA 4.0. Lihat NOTICE.md.

- https://github.com/ccxt/ccxt/wiki/Manual
- https://core.telegram.org/bots/api#sendphoto
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- https://creativecommons.org/licenses/by-nc-sa/4.0/
