# Panduan Deployment (Server Production)

Aplikasi **RoC Support Desk** merupakan sistem yang mengkombinasikan API, Background Workers (Celery), Task Scheduler (Celery Beat), dan Eksternal Integrasi (Evolution API via Docker).

Karena arsitektur ini membutuhkan *Daemon* (proses yang terus berjalan di latar belakang) dan *File Storage* untuk lampiran (attachments), **maka deployment di VPS (Virtual Private Server) adalah solusi yang paling tepat dan sangat direkomendasikan.**

Berikut panduan lengkap deployment untuk VPS (seperti Hostinger, SumoPod, DigitalOcean, dll) dan juga analisis terkait deployment ke *Serverless* platform seperti Vercel.

---

## Opsi 1: Deployment di VPS (Sangat Direkomendasikan) 🌟

### A. Prasyarat Sistem & Persiapan

1. **VPS / VM Server**: Minimal RAM 2GB (Ideal: 4GB) dengan OS **Ubuntu 22.04 LTS / 24.04 LTS**.
2. **Akses Root**: Anda harus bisa masuk ke server menggunakan SSH (`ssh root@ip_vps_anda`).
3. **Domain Name**: Anda harus memiliki nama domain (misal: `helpdesk.perusahaan.com`) dan pastikan **A Record** di menu DNS Management (misal di Hostinger/Cloudflare) sudah diarahkan ke IP Public VPS Anda.

### B. Langkah 1: Update Server & Instalasi Dependensi Inti

Setelah masuk ke VPS via SSH, jalankan perintah berikut:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip python3-venv git curl nginx certbot python3-certbot-nginx redis-server postgresql postgresql-contrib dirmngr apt-transport-https lsb-release ca-certificates -y
```

### C. Langkah 2: Setup Database PostgreSQL

Buat database, user, dan berikan akses untuk produksi. Buka shell PostgreSQL:

```bash
sudo -u postgres psql
```

Di dalam terminal PostgreSQL, ketik:

```sql
CREATE DATABASE rocdesk_db;
CREATE USER rocdesk_user WITH PASSWORD 'GantiDenganPasswordKuat123!';
ALTER ROLE rocdesk_user SET client_encoding TO 'utf8';
ALTER ROLE rocdesk_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE rocdesk_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE rocdesk_db TO rocdesk_user;
-- Pada versi PG 15+ perlu grant schema:
\c rocdesk_db
GRANT ALL ON SCHEMA public TO rocdesk_user;
\q
```

### D. Langkah 3: Setup Project Django

Buat folder proyek dan tarik kode asli:

```bash
# Clone proyek ke folder /var/www
cd /var/www
sudo git clone <URL_GITHUB_ANDA> roc_support_desk
cd roc_support_desk

# Berikan hak akses ke user ubuntu (atau root jika Anda pakai root)
sudo chown -R $USER:$USER /var/www/roc_support_desk

# Buat virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install requirements aplikasi DAN paket server (Gunicorn & Psycopg2)
pip install -r requirements.txt
pip install gunicorn psycopg2-binary
```

**Konfigurasi File `.env`**:
Ganti/buat file `.env` di folder proyek dan isi dengan konfigurasi berikut:

```env
SECRET_KEY="kunci_rahasia_anda_yang_sangat_panjang_sekali"
DEBUG=False
ALLOWED_HOSTS="helpdesk.perusahaan.com,localhost,127.0.0.1,host.docker.internal"

DATABASE_URL="postgres://rocdesk_user:GantiDenganPasswordKuat123!@127.0.0.1:5432/rocdesk_db"

CELERY_BROKER_URL="redis://127.0.0.1:6379/0"
CELERY_RESULT_BACKEND="redis://127.0.0.1:6379/1"

# [Lanjutkan mengisi konfigurasi IMAP, SMTP, dan EVOLUTION_API]

DEFAULT_FROM_EMAIL="noreply@bantuanteknis.com" # Digunakan untuk pengiriman OTP Password Reset

```

Jalankan perintah Migrasi & Static Files:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

### E. Langkah 4: Menjalankan Django (Gunicorn Service)

Agar Django jalan di latar belakang secara otomatis ketika server reboot, kita buat _Systemd Service_.

Buat file daemon: `sudo nano /etc/systemd/system/gunicorn_roc.service`

Isi file dengan skrip:
```ini
[Unit]
Description=gunicorn daemon for RoC Support Desk
Requires=gunicorn_roc.socket
After=network.target

[Service]
User=root
Group=www-data
WorkingDirectory=/var/www/roc_support_desk
ExecStart=/var/www/roc_support_desk/.venv/bin/gunicorn \
          --access-logfile - \
          --workers 3 \
          --bind unix:/run/gunicorn_roc.sock \
          roc_desk.wsgi:application

[Install]
WantedBy=multi-user.target
```

Buat juga Socket: `sudo nano /etc/systemd/system/gunicorn_roc.socket`

```ini
[Unit]
Description=gunicorn socket

[Socket]
ListenStream=/run/gunicorn_roc.sock

[Install]
WantedBy=sockets.target
```

Aktifkan *service*:
```bash
sudo systemctl start gunicorn_roc.socket
sudo systemctl enable gunicorn_roc.socket
sudo systemctl status gunicorn_roc
```

### F. Langkah 5: Setup Background Workers (Celery & Celery Beat)

Aplikasi menangani IMAP (Email), SMTP (Kirim Email), & Evolution API Webhook di antrean belakang. Kita _wajib_ menyalakannya sebagai Daemon.

Buat file daemon Worker: `sudo nano /etc/systemd/system/celery_roc_worker.service`

```ini
[Unit]
Description=Celery Service for RoC Desk
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/var/www/roc_support_desk
ExecStart=/var/www/roc_support_desk/.venv/bin/celery -A roc_desk worker --loglevel=info
Restart=always

[Install]
WantedBy=multi-user.target
```

Buat file daemon Beat (Scheduler - untuk mengecek inbox email tiap 1 menit): `sudo nano /etc/systemd/system/celery_roc_beat.service`

```ini
[Unit]
Description=Celery Beat Service for RoC Desk
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/var/www/roc_support_desk
ExecStart=/var/www/roc_support_desk/.venv/bin/celery -A roc_desk beat -l info
Restart=always

[Install]
WantedBy=multi-user.target
```

Aktifkan keduaya:
```bash
sudo systemctl start celery_roc_worker
sudo systemctl start celery_roc_beat
sudo systemctl enable celery_roc_worker
sudo systemctl enable celery_roc_beat
```

### G. Langkah 6: Konfigurasi NGINX (Domain & HTTPS)

Menyambungkan Domain ke Aplikasi Gunicorn.

Buat file Nginx: `sudo nano /etc/nginx/sites-available/rocdesk`

Pastikan server names / path diubah dengan domain dan letak project Anda:

```nginx
server {
    listen 80;
    server_name helpdesk.perusahaan.com;

    location = /favicon.ico { access_log off; log_not_found off; }
    
    # Static & Media routing
    location /static/ {
        root /var/www/roc_support_desk;
    }
    
    location /media/ {
        root /var/www/roc_support_desk;
    }

    # Pass everything else to Gunicorn
    location / {
        include proxy_params;
        proxy_pass http://unix:/run/gunicorn_roc.sock;
    }
}
```

Aktifkan konfigurasi, test syntaks, lalu amankan dengan HTTPS Let's Encrypt:

```bash
sudo ln -s /etc/nginx/sites-available/rocdesk /etc/nginx/sites-enabled
sudo nginx -t
sudo systemctl restart nginx
sudo certbot --nginx -d helpdesk.perusahaan.com
```

### H. Langkah 7: Install Docker & Menjalankan Evolution API

Webhook WhatsApp tidak bisa hidup tanpa ini.

Install Docker CE:
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

Jalankan container Evolution API (Port 8080):
```bash
docker run -d --name evolution-api \
  --restart always \
  -p 8080:8080 \
  -e AUTHENTICATION_API_KEY=KUNCI_RAHASIA_Evolution_123 \
  -v evolution_store:/evolution/store \
  atende/evolution-api:v2.3.6
```

**Penting:** Daftarkan Webhook dari dalam Server VPS Anda. Karena Anda pakai domain sekarang, URL Webhook BUKAN `host.docker.internal` lagi, tetapi URL domain VPS Anda:

Gunakan cURL:
```bash
# Sesuaikan InstanceName dan ApiKey
curl -X PUT "http://127.0.0.1:8080/webhook/set/namainstance" \
  -H "apikey: KUNCI_RAHASIA_Evolution_123" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook": {
        "enabled": true,
        "url": "https://helpdesk.perusahaan.com/api/gateways/evolution/webhook",
        "webhookByEvents": true,
        "webhookBase64": true,
        "events": ["MESSAGES_UPSERT"]
    }
  }'
```

🎉 **Selesai! Sistem sudah Production-Ready di VPS.**

---

## Opsi Tambahan: Konfigurasi Site Global (Admin Panel)

Nama sistem dan judul website kini tidak lagi *hardcoded* melainkan dinamis dari Database. Setup ini **wajib** dilakukan pertama kali:

1. Masuk ke Django Admin (`https://helpdesk.perusahaan.com/admin/`).
2. Masuk ke menu **Core** -> **Site Configurations**.
3. Edit objek `Support Desk` dan ubah teksnya sesuai perusahaan (misal: "IT Helpdesk PT Sejahtera").
4. Tombol *Save* akan otomatis mengubah Judul Tab Browser, Profil Sidebar, Formulir Masuk, dan Tanda Tangan Email Penutup Klien.

---

## Proses Lupa Kata Sandi Terenkripsi (OTP 6-Digit)

Berbeda dengan bawaan Django, RoC Support Desk menggunakan keamanan kode 6-digit OTP (*One-Time Password*) untuk staff yang lupa kata sandi. Email OTP ini bergantung pada SMTP Worker, jadi **Celery Worker wajib menyala**.

1. Pastikan `DEFAULT_FROM_EMAIL` dan parameter `SMTP` diisi pada berkas `.env`.
2. OTP dikirim dalam templat HTML bersih.
3. Waktu kadaluwarsa token dibatasi secara spesifik ke **15 menit**.
4. Hanya Email dari Admin yang sebelumnya *terdaftar resmi* (dibuat via `/admin` atau konsol) yang akan memicu surat OTP. Sistem menolak sembarang email untuk menghindari *spam enumeration*.


---

## Opsi 2: Deployment via Vercel (TIDAK DIREKOMENDASIKAN ❌)

Secara teknis, Anda **BISA** mendeploy kode Django Frontend/Admin ke Vercel (yang pada dasarnya mengubah server web Python menjadi rentetan fungsi *AWS Lambda*).  

Namun, aplikasi **RoC Support Desk sangat tidak cocok untuk environment Vercel (Serverless)**.

### Mengapa Vercel TIDAK COCOK?

1. **Vercel Bersifat Stateless & Singkat (Ephemeral)**  
   - Setiap halaman diakses, Vercel hanya menghidupkan fungsi selama 10 detik lalu "mematikan" servernya.
   - Dampaknya: Anda **TIDAK BISA** menjalankan **Celery Worker** atau **Celery Beat** di latar belakang. Fitur membaca Inbox Email (Polling IMAP tiap menit) dan fitur menangani antrean Webhook (Evolution API WhatsApp processing yang butuh waktu lebih lama dari 10 detik) akan gagal total (*timeout*).
2. **Tidak Mendukung Docker Container**  
   - Evolution API (Server Integrasi WhatsApp yang ditulis dalam Javascript/TypeScript) memerlukan _Node daemon_ utuh berbasis Container yang tidak bisa ditampung dan diletakkan di platform Vercel. Anda harus menyewa VPS terpisah lagi khusus hanya untuk menjalankan Evolution API.
3. **Penyimpanan FIle Lokal Hilang**  
   - Tiket sering menerima lampiran (screenshot via WA, PDF via Email, File Upload di web form). Vercel menghapus semua file lokal yang diupload 10 detik setelah di-request selesai.  
   - Anda terpaksa merombak kode (`django-storages`) dan menyewa **Amazon S3 (AWS)** atau **Cloudinary** khusus hanya untuk menyimpan _attachments_ milik pesan chat.
4. **Database & Redis Terpisah**
   - Vercel tidak melayani Database. Anda wajib menyewa server terpisah untuk PostgreSQL (seperti Supabase atau Neon) dan membiarkan mereka tersambung عبر API terpisah dengan latency internet, yang menambah latensi.
   - Redis Broker Celery harus menyewa platform Upstash Serverless Redis.
5. **Django Scheduler Crash**  
   - Karena Celery Beat tidak hidup, Anda membutuhkan setup pihak ketiga (Vercel Cron Jobs / GitHub Actions CRON) yang menembak suatu endpoint custom API setiap 1 menit di Django Anda untuk memancing script baca Email (Cron Polling `IMAP`). Ini sangat inefisien untuk Helpdesk dan rentan putus.

### Jika Tetap Memaksa ke Vercel (Untuk Uji Coba UI Frontend)

Syarat: Anda harus puas fitur WhatsApp & Email Auto-Polling **RUSAK** atau **TIDAK RUNNING**.

1. Siapkan Database **Supabase** (PostgreSQL Server), masukkan link nya ke `.env`.
2. Hapus referensi `Celery` di `__init__.py` aplikasi `roc_desk`.
3. Tambahkan library Vercel Python WSGI (`pip install vercel-wsgi`).
4. Buat file `vercel.json` di root:

```json
{
  "version": 2,
  "builds": [
    {
      "src": "roc_desk/wsgi.py",
      "use": "@vercel/python",
      "config": { "maxLambdaSize": "15mb", "runtime": "python3.10" }
    }
  ],
  "routes": [
    {
      "src": "/(.*)",
      "dest": "roc_desk/wsgi.py"
    }
  ]
}
```
5. Dorong repository ke GitHub, dan *Connect* dari Dashboard Vercel. Konfigurasi semua Environment Variables VPS (S3 Link, DB link, SMTP mailer) di Menu Setting Environment Vercel.

**Kesimpulan Akhir & Saran Profesional:**
Sistem CRM / Support Desk yang melakukan Polling Socket berkala ke kotak surat Mailbox/IMAP, menampung pesan WA secara _streaming_ dari Provider Server, dan menampung Base64 attachment _local_ dirancang secara eksplisit sejak dekade awal pemrograman menggunakan **Stateful Server**. Memaksakannya ke pola arsitektur Micro-Serverless Vercel ibarat memasukkan mesin Truk Diesel ke dalam Rangka Mobil Mainan.

Gunakanlah **Cloud VPS (Hostinger KVM / DigitalOcean Basic Droplet Minimalis $6-$10/bulan)** untuk mendapat environment utuh, kendali penuh atas Container API Whatsapp nya, storage disk lega untuk upload chat klien tanpa bayar bandwith S3 ekstra per bulan, serta layanan Email & Worker lancar jaya sepanjang musim.
