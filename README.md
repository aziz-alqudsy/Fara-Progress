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
- ✅ Jika **semua** yang di-mention sudah update → bot langsung kirim **ringkasan progress** & tutup siklus.
- ✅ **Strategi pengingat per siklus reminder:**
  - **Hari H** — bot kirim **pesan reminder**.
  - **H+1** — **hanya jika** masih ada yang belum update, bot kirim **pengingat harian** (di-mention ulang). Jika semua sudah update, tidak ada pengingat.
  - **H+2** — bot kirim **ringkasan final** apa pun kondisinya (meski sebagian / belum ada yang update) lalu **menutup** siklus tersebut.
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
   | `DATABASE_URL` | connection string PostgreSQL (Neon/Supabase) — **wajib agar data tidak hilang saat redeploy**, lihat bagian *PostgreSQL persisten* |

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
  tidur. Untuk reminder yang andal, pakai paket **Starter** (selalu hidup), atau
  kombinasikan dua mitigasi di bawah.

#### Mengakali tidur di free tier (cron-job.org + catch-up)

1. **Keep-alive ping (cron-job.org / UptimeRobot).** Agar service tidak tidur saat
   jam jadwal, buat monitor/cronjob gratis yang mem-**ping endpoint `/health` tiap
   ~10 menit**:
   - **URL:** `https://nama-service-kamu.onrender.com/health`
   - **Schedule / interval:** tiap 5–10 menit.
   - Metode `GET` cukup. Endpoint ini membalas **HTTP 200 `ok`**, jadi monitor akan
     berstatus **UP** (bukan 404 seperti kalau nge-ping path lain).

   > Bot mendaftarkan `/health` sendiri di server webhook-nya (lihat
   > `bot/health.py`). Endpoint ini hanya aktif di **mode webhook**.
2. **Catch-up otomatis saat startup.** Jika sebuah occurrence tetap terlewat
   (mis. ping sempat gagal / redeploy tepat saat jam jadwal), saat bot start/wake
   bot otomatis mengirim **occurrence terakhir yang terlewat** (lihat
   `scheduler.run_catchup`). Jadi reminder masuk **telat** alih-alih hilang sampai
   siklus berikutnya. Hanya occurrence terbaru yang dikirim — tidak menumpuk.

> Kombinasi keduanya membuat reminder cukup andal di free tier, tapi untuk
> ketepatan waktu penuh paket **Starter** tetap paling baik.
- **Filesystem ephemeral.** File `reminder.db` (SQLite) akan **ter-reset setiap
  redeploy/restart**, sehingga semua reminder hilang. **Untuk produksi WAJIB pakai
  PostgreSQL** (lihat di bawah) — atau **Persistent Disk** Render bila pakai paket
  berbayar (set `DB_PATH` ke path yang di-mount, mis. `/data/reminder.db`).

#### PostgreSQL persisten (gratis via Neon) — disarankan untuk produksi

Tanpa ini, semua reminder hilang tiap redeploy. Bot memilih backend otomatis:
**bila `DATABASE_URL` diisi → PostgreSQL**, selain itu → SQLite (`DB_PATH`).

1. Buat database Postgres gratis & permanen di [Neon](https://neon.tech) (atau
   [Supabase](https://supabase.com)). Salin **connection string**-nya, contoh:
   `postgresql://user:password@ep-xxx.neon.tech/dbname?sslmode=require`
2. Di Render → **Environment** → tambahkan env var:

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | connection string dari Neon (sertakan `?sslmode=require`) |

3. Deploy. Saat start, log menampilkan `Backend DB: PostgreSQL` dan tabel dibuat
   otomatis. Reminder kini **aman walau redeploy**.

> - Skema `postgres://` otomatis dinormalkan ke `postgresql://`.
> - Bot **reconnect otomatis** bila koneksi sempat putus (Neon free tier
>   meng-suspend compute saat idle) — kombinasikan dengan keep-alive `/health`.
> - Tanpa `DATABASE_URL`, bot tetap jalan dengan SQLite (praktis untuk lokal).

## ⚠️ Penting untuk pemakaian di GRUP

Secara default Telegram mengaktifkan **privacy mode** sehingga bot tidak menerima
pesan biasa di grup. Bot ini menyiasati dengan **ForceReply** saat membuat reminder,
tetapi agar paling andal sebaiknya **matikan privacy mode**:

> @BotFather → `/setprivacy` → pilih bot → **Disable**

Lalu tambahkan bot ke grup. Untuk topic grup, jalankan perintah di dalam topic
yang diinginkan — reminder akan dikirim ke topic tersebut.

### Auto-hapus pesan setup di grup

Agar tanya-jawab saat `/new` tidak memenuhi chat anggota lain, di grup bot
**menghapus otomatis** semua pesan setup (perintah `/new`, prompt bot, dan jawaban
pengguna) setelah reminder selesai dibuat — hanya satu pesan konfirmasi yang
disisakan. Di chat pribadi, pesan tidak dihapus.

> **Syarat:** agar bisa menghapus *pesan pengguna*, **bot harus jadi admin grup
> dengan izin "Delete messages"**. Tanpa izin itu, bot tetap menghapus pesan
> miliknya sendiri, tetapi jawaban pengguna akan tertinggal.
>
> Catatan: notifikasi tetap muncul sesaat selama proses setup berlangsung;
> pesannya baru dihapus setelah setup selesai.

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
5. Saat reminder datang (**hari H**), **reply/quote** pesan bot untuk update progress.
6. **H+1**: yang belum update akan diingatkan ulang (kalau semua sudah update, dilewati).
7. Setelah semua yang ditugaskan update → bot kirim **ringkasan** otomatis. Jika sampai **H+2** masih ada yang belum, bot tetap kirim **ringkasan final** dan menutup siklus.

### Contoh tampilan pesan

**Pesan reminder** (dikirim saat jadwal tiba):

```
🔔 Daftar Task (3 item) — 2026-06-10

1. [App] Implement feedback page @aziz_alqudsy
2. [App] Create itinerary @FiqihNR
3. [BE] Create API feedback @riskinvnda

👥 Ditugaskan ke: @aziz_alqudsy, @FiqihNR, @riskinvnda
🔁 Jadwal: harian

💬 Cara update: balas (reply/quote) pesan ini dengan nomor task, contoh:
1. selesai
2. masih proses
3. belum mulai
```

**Jika reply tanpa nomor → ditolak:**

```
⚠️ Update progress harus memakai nomor task.
Reply/quote lagi pesan reminder dengan format bernomor, contoh:
1. selesai
2. masih proses
3. belum mulai
```

**Pengingat harian** (untuk yang belum update):

```
⏰ Pengingat harian
Belum update progress untuk: Daftar Task (3 item) — 2026-06-10

@FiqihNR

💬 Mohon balas (reply/quote) pesan reminder dengan nomor task, contoh:
1. selesai
2. masih proses
```

**Ringkasan** (dikirim otomatis setelah semua peserta update):

```
✅ Semua peserta sudah update progress!
📋 Ringkasan: Daftar Task (3 item) — 2026-06-10

• @aziz_alqudsy:
  1. selesai
  2. masih proses
• @FiqihNR:
  2. selesai
• @riskinvnda:
  3. selesai, sudah deploy
```

**Ringkasan final** (dikirim otomatis di **H+2** meski sebagian belum update):

```
⏳ Batas waktu update tercapai — ringkasan progress
📋 Ringkasan: Daftar Task (3 item) — 2026-06-10

• @aziz_alqudsy:
  1. selesai
  2. masih proses
• @FiqihNR: belum update
• @riskinvnda:
  3. selesai, sudah deploy
```

> Catatan: teks tebal/miring di atas disederhanakan menjadi teks biasa. Di Telegram,
> judul & label tampil **tebal**, dan contoh format ditampilkan dalam blok `monospace`.

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
  db.py             # penyimpanan: PostgreSQL (DATABASE_URL) atau SQLite (lokal)
  parser.py         # validasi numbering + ekstraksi mention
  scheduler.py      # APScheduler: penjadwalan reminder + tindak lanjut harian (nag H+1, ringkasan H+2)
  health.py         # endpoint GET /health (200) untuk keep-alive/monitoring (mode webhook)
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
- **Catch-up startup:** setelah membangun ulang job, bot mengecek tiap reminder aktif —
  bila occurrence terjadwal terakhir sudah lewat tapi belum punya `run`, occurrence itu
  langsung di-fire (telat) alih-alih hilang. Mencegah reminder "loncat" ke siklus
  berikutnya saat service sempat mati di jam jadwal.
- Job tindak lanjut harian berjalan pada jam `NAG_HOUR` (default 09:00 zona `DEFAULT_TZ`).
  Untuk tiap siklus reminder yang masih terbuka, umur run dihitung dalam **hari kalender**
  (zona `DEFAULT_TZ`): umur **1 hari** → pengingat (jika masih ada yang belum update),
  umur **≥2 hari** → ringkasan final + tutup siklus. Pemakaian `≥2` membuat ringkasan tetap
  terkirim walau bot sempat mati saat H+2.
- Pencocokan "siapa sudah update": berdasarkan `user_id` (mention via text_mention)
  atau `username` (mention `@`).
- Jalankan test: `python tests_smoke.py`.
