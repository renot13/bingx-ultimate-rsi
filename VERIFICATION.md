# Hasil verifikasi

Pengujian lokal pada 20 September 2026, Python 3.14.3.

- 12 tes otomatis: rumus Ultimate RSI dengan referensi scalar independen,
  seed RMA, seri naik/turun/datar, candle berjalan vs candle tertutup,
  data stale/gap, tengah malam Tokyo, deduplikasi sent/pending, timeout
  Telegram tanpa retry, pemuatan riwayat, riwayat rusak, dan konflik update.
- Dry-run API publik BingX: 200 pasangan, 200 berhasil diperiksa,
  20 memenuhi Ultimate RSI <20, 0 error, 0 pesan dikirim.
  Tahap pemindaian sekitar 69 detik (19:44:20–19:45:29 JST), tidak termasuk
  instalasi dependensi, pengiriman Telegram, atau penyimpanan GitHub.
- Setelah perbaikan tata letak, dry-run 5 pasangan: 5 berhasil, 1 sinyal,
  0 error. Gambar contoh diperiksa secara visual.
- Contoh chart memakai data live BingX untuk candle tutup 19:00 JST.

Belum diuji end-to-end: penjadwalan pada GitHub hosted Linux, penulisan cabang
bot-state dengan GITHUB_TOKEN nyata, pengiriman Telegram dengan token pengguna,
dan kesamaan digit dengan indikator pada TradingView. Tidak ada repository
GitHub yang dibuat atau diubah dan tidak ada pesan Telegram yang dikirim.

Durasi lokal bukan jaminan durasi atau kuota pada GitHub Actions.
