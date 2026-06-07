# Telegram Reminder Bot

Bot reminder Telegram untuk **pemakaian pribadi** maupun di dalam **grup / topic grup**.
Dibangun dengan `python-telegram-bot` (v21), `APScheduler`, dan `SQLite`.

## Fitur

- ✅ Bisa dipakai di chat pribadi, grup, maupun **topic** (forum) grup.
- ✅ Penulisan task **wajib numbering** (`1. 2. 3.`) dengan **sub-abjad** (`a. b. c.`).
- ✅ Baris tanpa numbering **ditolak** (kecuali baris kosong).
- ✅ Di grup, **mention (@)** pengguna untuk menugaskan task.
- ✅ Atur **tanggal & jam** mulai serta **frekuensi**: sekali / tiap jam / harian / mingguan / bulanan / tahunan, dengan **interval** (mis. tiap 2 hari).
- ✅ Pengguna (atau yang ditugaskan) **update progress** dengan **reply/quote** pesan bot.
- ✅ Jika **semua** yang di-mention sudah update → bot kirim **ringkasan progress**.
- ✅ Yang **belum** update akan **diingatkan tiap hari** (di-mention ulang).
- ✅ Setelah ringkasan terkirim, reply ke pesan reminder/summary **diabaikan**.

## Persyaratan

- Python 3.11+
- Token bot dari [@BotFather](https://t.me/BotFather)

## Instalasi

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# lalu edit .env (minimal isi BOT_TOKEN)
```

## Menjalankan

### Mode polling (paling mudah, untuk lokal/testing)

Set `RUN_MODE=polling` di `.env`, lalu:

```powershell
python run.py
```

### Mode webhook (produksi)

Webhook butuh URL **HTTPS publik** yang menunjuk ke bot. Set di `.env`:

```
RUN_MODE=webhook
WEBHOOK_URL=https://domain-kamu.com   # tanpa trailing slash
WEBHOOK_PORT=8443
WEBHOOK_PATH=telegram
WEBHOOK_SECRET=isi-rahasia-opsional
```

Lalu `python run.py`. Bot otomatis mendaftarkan webhook ke Telegram.

Untuk uji webhook dari lokal, gunakan tunnel HTTPS, contoh:

```powershell
cloudflared tunnel --url http://localhost:8443
# pakai URL https hasilnya sebagai WEBHOOK_URL
```

## Deploy ke Render.com

Render memberi URL HTTPS publik (mis. `https://nama-service-kamu.onrender.com`)
yang langsung dipakai sebagai `WEBHOOK_URL`.

### Langkah

1. Push repo ini ke GitHub.
2. Render → **New → Web Service** → connect repo.
3. **Build command:** `pip install -r requirements.txt`
4. **Start command:** `python run.py`
5. **Environment variables:**

   | Key | Value |
   |---|---|
   | `BOT_TOKEN` | token dari @BotFather |
   | `RUN_MODE` | `webhook` |
   | `WEBHOOK_URL` | `https://nama-service-kamu.onrender.com` |
   | `WEBHOOK_PATH` | `telegram` |
   | `DEFAULT_TZ` | `Asia/Jakarta` |

   > **Jangan** set `WEBHOOK_PORT`. Render menyuntik env var `PORT` sendiri dan
   > bot otomatis bind ke port tersebut.
   >
   > **Jangan** isi `WEBHOOK_PATH` dengan token bot — nilainya ikut tercetak di
   > log Render. Pakai nilai biasa seperti `telegram`.

6. Deploy. Bot otomatis mendaftarkan webhook ke Telegram saat start.

### Versi Python (WAJIB)

`python-telegram-bot` 21.6 belum mendukung Python 3.14 (yang dipakai Render secara
default) — akan muncul `RuntimeError: There is no current event loop`. Repo ini
menyertakan file **`.python-version`** (`3.12.7`) yang otomatis dibaca Render untuk
mem-pin versi Python. Alternatif: set env var `PYTHON_VERSION=3.12.7` di dashboard.

### ⚠️ Catatan penting Render

- **Free tier tidur saat idle (~15 menit).** Webhook tetap bangun saat ada chat
  masuk, tetapi **reminder terjadwal & nag harian tidak akan fire** selama service
  tidur. Untuk reminder yang andal, pakai paket **Starter** (selalu hidup).
- **Filesystem ephemeral.** File `reminder.db` (SQLite) akan **ter-reset setiap
  redeploy/restart**, sehingga semua reminder hilang. Untuk produksi, gunakan
  **Persistent Disk** Render (set `DB_PATH` ke path yang di-mount, mis.
  `/data/reminder.db`) atau pindah ke **PostgreSQL**.

## ⚠️ Penting untuk pemakaian di GRUP

Secara default Telegram mengaktifkan **privacy mode** sehingga bot tidak menerima
pesan biasa di grup. Bot ini menyiasati dengan **ForceReply** saat membuat reminder,
tetapi agar paling andal sebaiknya **matikan privacy mode**:

> @BotFather → `/setprivacy` → pilih bot → **Disable**

Lalu tambahkan bot ke grup. Untuk topic grup, jalankan perintah di dalam topic
yang diinginkan — reminder akan dikirim ke topic tersebut.

## Cara pakai

1. `/new` — mulai membuat reminder.
2. Kirim daftar task (numbering). Contoh:
   ```
   1. Siapkan slide @budi
      a. cover
      b. ringkasan
   2. Kirim undangan @sari
   ```
3. Kirim **tanggal & jam** mulai: `YYYY-MM-DD HH:MM` (zona waktu dari `DEFAULT_TZ`).
4. Pilih **frekuensi** lewat tombol, lalu **interval** (atau `/skip` untuk 1).
5. Saat reminder datang, **reply/quote** pesan bot untuk update progress.
6. Setelah semua yang ditugaskan update → bot kirim **ringkasan** otomatis.

### Perintah

| Perintah | Fungsi |
|---|---|
| `/new` | Buat reminder baru |
| `/list` | Lihat reminder aktif di chat/topic ini |
| `/delete <id>` | Hapus/nonaktifkan reminder |
| `/cancel` | Batalkan pembuatan reminder |
| `/help` | Bantuan |

## Struktur kode

```
bot/
  app.py            # bootstrap: handler, scheduler, mode jalan
  config.py         # konfigurasi dari .env
  db.py             # penyimpanan SQLite
  parser.py         # validasi numbering + ekstraksi mention
  scheduler.py      # APScheduler: penjadwalan reminder + nag harian
  texts.py          # penyusunan teks pesan (HTML)
  handlers/
    conversation.py # alur /new (tasks -> jadwal -> frekuensi)
    commands.py     # /start /help /list /delete
    replies.py      # update progress via reply + ringkasan
run.py              # entry point
tests_smoke.py      # smoke test parser & alur DB
```

## Catatan teknis

- Jadwal job dibangun ulang dari DB saat startup (job tidak dipersist, datanya yang dipersist).
- Reminder harian (nag) berjalan pada jam `NAG_HOUR` (default 09:00 zona `DEFAULT_TZ`).
- Pencocokan "siapa sudah update": berdasarkan `user_id` (mention via text_mention)
  atau `username` (mention `@`).
- Jalankan test: `python tests_smoke.py`.
