# RoC Support Desk — Dokumentasi Proyek

> Sistem Service Desk berbasis Django untuk manajemen Problem & Solving secara multikanal (WhatsApp, Email, Web Form).

---

## 1. Ikhtisar Sistem

RoC Support Desk adalah aplikasi helpdesk internal perusahaan yang memungkinkan karyawan (Employee) mengajukan case/tiket melalui **3 kanal utama**:

| Kanal | Mekanisme |
|-------|-----------|
| **WhatsApp** | Webhook dari Evolution API v2 → Celery Task → Case otomatis + Auto-reply WA dengan nomor tiket |
| **Email** | IMAP Polling setiap 60 detik → Celery Task → Case otomatis + Auto-acknowledgment email dengan nomor tiket |
| **Web Form** | Form publik di portal client → Case manual |

Staff Support Desk kemudian mengelola case melalui panel admin dengan fitur split-panel (chat di kiri, RCA form di kanan).

---

## 2. Tech Stack

| Komponen | Teknologi |
|----------|-----------|
| Backend | Django 5.1 + Python 3.x |
| Admin Theme | Django Unfold |
| Database | PostgreSQL 14+ |
| Task Queue | Celery 5.x + Redis |
| WhatsApp Gateway | Evolution API v2 (Docker) |
| Email Inbound | IMAP (Gmail) |
| Email Outbound | SMTP (Gmail) dengan threading headers |
| Quill.js | HTML Text Editor framework terintegrasi pada Form Builder |
| Frontend | Django Templates + HTMX + SortableJS + Alpine.js + Tailwind CDN |
| Design System | JokoUI (custom CSS di `jokoui.css`) |
| Static Files | WhiteNoise |
| WSGI Server | Gunicorn (production) |

---

## 3. Struktur Direktori

```
roc_support_desk/
├── core/                   # App: User, CompanyUnit, Employee
├── cases/                  # App: CaseCategory, CaseRecord, Message, Attachment
├── gateways/               # App: Webhook views, IMAP/SMTP services, Celery tasks
├── knowledge_base/         # App: Article (dari resolved cases)
├── roc_desk/               # Project settings, URL root, Celery config
├── templates/
│   ├── base.html           # Layout utama (sidebar + topbar)
│   ├── admin/              # Case list, case detail (split-panel)
│   ├── client/             # Dashboard, create case, submitted
│   ├── partials/           # HTMX partials (chat_thread, rca_form)
│   └── registration/       # Login page
├── static/css/jokoui.css   # Full design system
├── media/                  # File uploads (attachments)
├── .env                    # Environment variables (rahasia, JANGAN commit!)
├── .env.template           # Template variabel lingkungan
├── init_db.sql             # Script SQL untuk inisialisasi PostgreSQL
├── requirements.txt        # Dependensi Python
└── manage.py               # Entry point Django
```

---

## 4. Data Model (ERD)

```mermaid
erDiagram
    User ||--o{ CaseRecord : "assigned_to"
    Employee ||--o{ CaseRecord : "requester"
    Employee }o--|| CompanyUnit : "unit"
    CaseCategory ||--o{ CaseRecord : "category"
    CaseRecord ||--o{ Message : "messages"
    Message ||--o{ Attachment : "attachments"
    Employee ||--o{ Message : "sender_employee"
    User ||--o{ Message : "sender_staff"
    CaseCategory ||--o{ Article : "category"
    CaseRecord ||--o{ Article : "source_case"
```

### 4.1. Core App (`core/models.py`)

| Model | Deskripsi |
|-------|-----------|
| **AuditableModel** | Abstract base. UUID PK, `created_at`, `updated_at`, `created_by`, `updated_by`. |
| **SiteConfig** | Singleton model untuk konfigurasi global via DB (cth: `site_name`). |
| **OTPToken** | Token 6-digit untuk flow *Forgot Password* (valid 15 menit). |
| **User** | Custom user. Login via `login_username` (bukan `username`). Fields: `nik`, `role_access`, `initials`. |
| **CompanyUnit** | Unit organisasi (IT, FIN, HR). Fields: `name`, `code`. |
| **Employee** | Karyawan / end-user. Fields: `full_name`, `email` (unique), `phone_number` (E.164, unique), `job_role`, `unit` (FK → CompanyUnit). |
| **DynamicForm** | Entitas form dinamis utama. Fields: `title`, `slug`, `description`, `background_color`, `background_image`, `header_image`, `is_published`, `requires_login`, `show_on_portal`. |
| **FormField** | Relasi One-to-Many ke DynamicForm. Tipe input: text, email, text_area, dropdown, checkbox, radio, date, datetime, attachment, attachment_multiple, survey, title_desc, page_break. Mendukung `help_text` berbasis Quill Editor. |
| **FormSubmission** | Tabel penampung jawaban klien pada DynamicForm. Berisi JSON `answers` dan tracking user session/IP. |

### 4.2. Cases App (`cases/models.py`)

| Model | Deskripsi |
|-------|-----------|
| **CaseCategory** | Katalog layanan. Tampil sebagai kartu di client portal. Fields: `name`, `slug`, `icon`, `description`. |
| **CaseRecord** | Tiket utama. Status: `Open → Investigating → PendingInfo → Resolved → Closed`. Source: `EvolutionAPI_WA / Email / WebForm`. SLA: `response_due_at`, `resolution_due_at`, `first_response_at`, `resolved_at`, `closed_at`. Problem & Solving: `problem_description`, `root_cause_analysis`, `solving_steps`. Wajib isi RCA/Solving sebelum bisa di-Close. |
| **Message** | Pesan dalam thread tiket. Direction: `IN / OUT`. Channel: `WhatsApp / Email / Web`. `external_id` (Deduplikasi/Threading), `cc_emails` (Opsional Email CC). |
| **Attachment** | File lampiran per Message. Upload ke `media/attachments/<case_uuid>/`. |

### 4.3. Knowledge Base (`knowledge_base/models.py`)

| Model | Deskripsi |
|-------|-----------|
| **Article** | Artikel solusi. Dibuat dari case yang resolved. Fields: `title`, `problem_summary`, `root_cause`, `solution`, `is_published`. FK ke `CaseCategory` dan `CaseRecord`. |

### 4.4. Internal Notes & Notifications

| Model | Deskripsi |
|-------|-----------|
| **CaseComment** | Catatan internal staff (tidak terlihat oleh klien). Fields: `case`, `author`, `body`.<br>Memiliki **M2M `mentions`** ke `User` untuk fitur _@mention_. |

> **Logika Notifikasi Lonceng (Bell):**
> Dropdown notifikasi mengambil data 24 jam terakhir secara _real-time_ untuk:
> 1. **New Cases**: Kasus baru (Tampil untuk SEMUA staff).
> 2. **Unread Messages**: Pesan klien yang belum dibaca pada kasus yang di-_assign_ ke staff tersebut (Tampil HANYA untuk pemilik tiket).
> 3. **Mentions**: Internal Note yang men-tag `@username` staff (Tampil HANYA untuk staff yang di-tag).

---

## 5. URL Routing

| Path | App | Deskripsi |
|------|-----|-----------|
| `/` | cases | Client portal — dashboard kategori |
| `/create/<slug>/` | cases | Form buat case baru (publik) |
| `/submitted/<uuid>/` | cases | Halaman konfirmasi |
| `/desk/cases/` | cases (desk) | Daftar case (staff only) |
| `/desk/cases/<uuid>/` | cases (desk) | Detail case — split panel |
| `/desk/cases/<uuid>/thread/` | cases (desk) | HTMX partial — chat thread |
| `/desk/cases/<uuid>/reply/` | cases (desk) | HTMX POST — kirim balasan |
| `/desk/cases/<uuid>/rca/` | cases (desk) | HTMX POST — update RCA |
| `/desk/cases/<uuid>/comment/` | cases (desk) | HTMX POST — tambah internal note (@mentions) |
| `/api/gateways/evolution/webhook/` | gateways | Webhook Evolution API (POST) |
| `/api/gateways/evolution/webhook/<event>` | gateways | Webhook Evolution API v2 (POST, event suffix) |
| `/auth/login/` | django.auth | Login staff |
| `/auth/logout/` | django.auth | Logout |
| `/admin/` | django.admin | Django Admin panel (menggunakan tema Django Unfold) |
| `/kb/` | knowledge_base | Knowledge base (future) |
| `/api/users/` | cases (desk) | Endpoint JSON daftar staff untuk autocomplete Tribute.js |
| `/notifications/` | cases (desk) | HTMX partial — dropdown bell notifikasi |
| `/notifications/<type>/<id>/read/` | cases (desk) | Endpoint untuk menandai notifikasi telah dibaca |
| `/f/<slug>/` | cases (client) | Halaman antarmuka publik Dynamic Form untuk pengisian oleh Klien |
| `/desk/forms/` | core (admin) | Tampilan manajemen daftar Dynamic Form untuk Admin |
| `/desk/forms/<id>/edit/` | core (admin) | Laman Form Builder UI berbasis drag-drop untuk konfigurasi pertanyaanan form |

---

## 6. Integrasi Kanal

### 6.1. WhatsApp (Evolution API v2)

**Arah: Inbound (WA → Sistem)**

```
Employee kirim WA → Evolution API menerima
  → Evolution API POST ke /api/gateways/evolution/webhook/messages-upsert
    → Django view memvalidasi request (token lenient untuk v2)
      → Dispatch ke Celery task: process_evolution_webhook_task
        → Parse payload (remoteJidAlt untuk nomor E.164)
        → Lookup Employee by phone_number
        → Cek session WA aktif (dalam 30 menit terakhir)
          → Ada session → Thread ke case yang sama
          → Tidak ada → Buat case baru
        → Simpan Message + Attachment
        → Kirim auto-reply WA dengan nomor tiket (jika case baru)
```

**Arah: Outbound (Sistem → WA)**

```
Staff balas di chat panel (case source = WhatsApp)
  → cases/views.py: case_send_reply()
    → Buat Message (channel=WhatsApp, direction=OUT)
    → EvolutionAPIService.send_whatsapp_message()
      → POST ke Evolution API /message/sendText/<instance>
```

**Konfigurasi Webhook di Evolution API v2:**

> **PENTING:** Evolution API v2 menambahkan suffix event ke URL webhook (contoh: `/messages-upsert`).
> Django sudah menangani ini via URL pattern tambahan.

Untuk mendaftarkan webhook di Evolution API v2, gunakan endpoint berikut:

```bash
curl -X PUT "http://localhost:8080/webhook/set/<INSTANCE_NAME>" \
  -H "apikey: <EVOLUTION_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook": {
        "enabled": true,
        "url": "http://host.docker.internal:8000/api/gateways/evolution/webhook",
        "webhookByEvents": true,
        "webhookBase64": false,
        "events": ["MESSAGES_UPSERT"]
    }
  }'
```

> **Catatan:** Gunakan `host.docker.internal` sebagai hostname jika Django berjalan di host dan Evolution API berjalan di Docker.
> Hostname ini **WAJIB** ditambahkan ke `ALLOWED_HOSTS` di `.env`.

**Validasi Token (Evolution API v2):**

Evolution API v2 mengirimkan *instance token* (bukan webhook token yang dikonfigurasi) melalui header `X-Evolution-Token`. Karena perilaku ini, validasi token bersifat **lenient** — jika token tidak cocok, request tetap diteruskan dengan catatan warning di log.

**Session Threading WA (10 menit):**

Setiap pesan WA baru akan membuat case baru, **kecuali** jika employee sudah memiliki case WA aktif yang di-update dalam **10 menit terakhir**. Dalam hal ini, pesan akan di-thread ke case tersebut. Sistem juga akan otomatis mengirim pesan "Sesi Berakhir" jika lewat dari 10 menit tanpa interaksi.

### 6.2. Email (IMAP + SMTP)

**Arah: Inbound (Email → Sistem)**

```
Employee kirim email ke joyodrono24@gmail.com
  → Celery Beat trigger poll_imap_emails_task setiap 60 detik
    → ImapEmailService connect ke imap.gmail.com
    → Fetch UNSEEN emails SINCE hari ini (terbaru duluan, max 50)
    → Ekstrak Message-ID header (untuk email threading)
    → Lookup Employee by email
    → Thread by [CS-XXXXXXXX] di subject ATAU buat case baru
    → Simpan Message + Attachment (external_id = Message-ID)
    → Mark email sebagai SEEN
    → Jika case baru → dispatch send_case_acknowledgment_task
      → Kirim email konfirmasi otomatis dengan nomor tiket
```

**Arah: Outbound (Sistem → Email)**

```
Staff balas di chat panel (case source = Email)
  → cases/views.py: case_send_reply()
    → Buat Message (channel=Email, direction=OUT)
    → Dispatch ke Celery task: send_outbound_email_task
      → Django send_mail() via SMTP Gmail
      → Subject: "Re: [CS-XXXXXXXX] <subject>"
      → Headers: In-Reply-To + References (untuk threading)
      → Recipient: employee.email
```

**Email Threading:**

Semua email keluar (balasan staff + konfirmasi otomatis) menyertakan header RFC 2822:
- `In-Reply-To: <original-message-id>` — merujuk ke email asli pengirim
- `References: <original-message-id> <case-thread-id>` — chain lengkap
- `Message-ID: <case-uuid-reply-id@domain>` — ID unik deterministik

Ini memastikan Gmail (dan email client lain) mengelompokkan semua email terkait case ke dalam **satu thread**.

**Format Nomor Tiket di Email:**

- Subject: `[CS-XXXXXXXX]` (8 karakter pertama UUID case, uppercase hex)
- Contoh: `[CS-E38BBD1F] DISCOUNT Amount Odoo`
- Regex threading: `CS-([A-Fa-f0-9]{8})`

### 6.3. Web Form

```
Karyawan isi form di /create/<category-slug>/
  → Django view: create_case()
    → Validasi + simpan CaseRecord + Message
    → Redirect ke halaman konfirmasi
```

---

## 7. Celery Tasks

| Task Name | File | Trigger | Fungsi |
|-----------|------|---------|--------|
| `gateways.process_evolution_webhook_task` | `gateways/tasks.py` | Webhook POST | Proses pesan WhatsApp masuk + auto-reply WA |
| `gateways.poll_imap_emails_task` | `gateways/tasks.py` | Celery Beat (60s) | Poll email IMAP, buat case, dispatch auto-acknowledgment |
| `gateways.send_outbound_whatsapp_task` | `gateways/tasks.py` | Reply dari staff | Mengkonversi file upload (max 10MB/10 file) menjadi Base64 dan mengirimkannya + pesan teks ke Klien via Evolution API |
| `gateways.send_outbound_email_task` | `gateways/tasks.py` | Reply dari staff | Kirim email balasan via SMTP (dengan threading headers, attach actual files, formating rapi) |
| `gateways.send_case_acknowledgment_task` | `gateways/tasks.py` | Case baru dari email | Kirim email konfirmasi otomatis dengan nomor tiket |
| `gateways.check_wa_session_timeout_task` | `gateways/tasks.py` | 10 Menit stlh pesan WA | Mengecek apakah sesi sudah usang (10m) lalu mengirimkan WA penutup klien |
| `core.send_password_reset_otp_task` | `core/tasks.py` | Forgot Password Form | Mengirimkan kode rahasia OTP 6-Digit ke Email Admin saat lupa sandi |

**Celery Beat Schedule** (di `roc_desk/celery.py`):
```python
beat_schedule = {
    "poll-imap-emails-every-1-minute": {
        "task": "gateways.poll_imap_emails_task",
        "schedule": 60.0,
    },
}
```

> **PENTING:** Setiap ada perubahan kode di `tasks.py`, Celery Worker **HARUS di-restart** agar task terbaru terdaftar. Jika tidak, akan muncul error: `Received unregistered task`.

---

## 8. Autentikasi & Otorisasi

| Aspek | Detail |
|-------|--------|
| **Login field** | `login_username` (bukan `username` default) |
| **Portal Client** | Publik (tanpa login) |
| **Panel Admin/Desk** | Wajib login (`@staff_required` decorator) |
| **Role Access** | SuperAdmin, Manager, SupportDesk |
| **Webhook Security** | Lenient — token mismatch di-log tapi request diteruskan (Evolution API v2 compatibility) |

---

## 9. Environment Variables

Salin `.env.template` ke `.env` dan isi sesuai konfigurasi Anda:

```bash
copy .env.template .env
```

| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `SECRET_KEY` | — | Django secret key (ganti dengan string random 50 karakter) |
| `DEBUG` | `True` | Mode debug (set `False` di production) |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,host.docker.internal` | ⚠️ **WAJIB** termasuk `host.docker.internal` jika Evolution API di Docker |
| `DATABASE_URL` | — | PostgreSQL connection string (format: `postgres://user:pass@host:port/dbname`) |
| `CELERY_BROKER_URL` | `redis://127.0.0.1:6379/0` | Redis broker |
| `CELERY_RESULT_BACKEND` | `redis://127.0.0.1:6379/1` | Redis result backend |
| `EVOLUTION_API_URL` | — | URL server Evolution API (contoh: `http://localhost:8080`) |
| `EVOLUTION_API_KEY` | — | API key global Evolution API (dari `AUTHENTICATION_API_KEY` di docker-compose) |
| `EVOLUTION_INSTANCE_NAME` | — | Nama instance WA yang sudah di-scan QR |
| `EVOLUTION_WEBHOOK_TOKEN` | — | Token rahasia webhook (bebas, untuk keamanan) |
| `IMAP_HOST` | `imap.gmail.com` | Host IMAP |
| `IMAP_USER` | — | Email untuk IMAP polling |
| `IMAP_APP_PASSWORD` | — | App Password Gmail (16 karakter, tanpa spasi) |
| `EMAIL_HOST` | `smtp.gmail.com` | Host SMTP |
| `EMAIL_PORT` | `587` | Port SMTP |
| `EMAIL_USE_TLS` | `True` | Gunakan TLS |
| `EMAIL_HOST_USER` | — | Email pengirim SMTP (harus sama dengan `IMAP_USER`) |
| `EMAIL_HOST_PASSWORD` | — | Password SMTP (App Password, sama dengan `IMAP_APP_PASSWORD`) |

> **⚠️ PENTING — ALLOWED_HOSTS:**
> Jika `host.docker.internal` **tidak** ada di `ALLOWED_HOSTS`, semua webhook dari Evolution API (Docker) akan ditolak dengan error `400 Bad Request (DisallowedHost)` **tanpa log apapun di Django**. Ini adalah penyebab error paling umum saat instalasi baru.

---

## 10. Cara Menjalankan

### 10.1. Prasyarat

| Software | Versi Minimum | Catatan |
|----------|---------------|---------|
| Python | 3.10+ | |
| PostgreSQL | 14+ | |
| Redis | 7+ | Sebagai broker Celery |
| Docker | 20+ | Untuk menjalankan Evolution API |
| Gmail | — | Aktifkan 2-Step Verification + buat App Password |

### 10.2. Instalasi

```bash
# 1. Clone project
git clone <repo-url> roc_support_desk
cd roc_support_desk

# 2. Buat virtual environment
python -m venv .venv

# Windows:
.venv\Scripts\activate

# Linux/Mac:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Siapkan database PostgreSQL
psql -U postgres -f init_db.sql

# 5. Salin dan isi environment variables
copy .env.template .env         # Windows
# cp .env.template .env         # Linux/Mac

# ⚠️ EDIT .env — isi semua variabel yang kosong!
# Pastikan ALLOWED_HOSTS mengandung host.docker.internal

# 6. Migrasi database
python manage.py migrate

# 7. Buat superuser (untuk login ke admin panel)
python manage.py createsuperuser
# Isi: login_username, email, password

# 8. (Opsional) Collect static files (production)
python manage.py collectstatic --noinput
```

### 10.3. Setup Evolution API (WhatsApp)

```bash
# 1. Jalankan Evolution API via Docker
docker run -d \
  --name evolution-api \
  -p 8080:8080 \
  -e AUTHENTICATION_API_KEY=rocdesk-evo-key-2026 \
  atende/evolution-api:v2.3.6

# 2. Buat instance WhatsApp
curl -X POST "http://localhost:8080/instance/create" \
  -H "apikey: rocdesk-evo-key-2026" \
  -H "Content-Type: application/json" \
  -d '{
    "instanceName": "helpdesk-wa-final",
    "integration": "WHATSAPP-BAILEYS",
    "qrcode": true
  }'

# 3. Scan QR Code
# Buka browser: http://localhost:8080/manager
# Atau gunakan endpoint: GET /instance/connect/<instance-name>

# 4. Daftarkan Webhook
curl -X PUT "http://localhost:8080/webhook/set/helpdesk-wa-final" \
  -H "apikey: rocdesk-evo-key-2026" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook": {
        "enabled": true,
        "url": "http://host.docker.internal:8000/api/gateways/evolution/webhook",
        "webhookByEvents": true,
        "webhookBase64": false,
        "events": ["MESSAGES_UPSERT"]
    }
  }'

# ✅ Verifikasi webhook terdaftar:
curl "http://localhost:8080/webhook/find/helpdesk-wa-final" \
  -H "apikey: rocdesk-evo-key-2026"
```

> **Catatan Docker Networking:**
> - Evolution API (Docker) → Django (Host): gunakan `host.docker.internal:8000`
> - Django (Host) → Evolution API (Docker): gunakan `localhost:8080`

### 10.4. Setup Gmail (IMAP + SMTP)

1. **Aktifkan 2-Step Verification** di akun Gmail
2. **Buat App Password**:
   - Buka https://myaccount.google.com/apppasswords
   - Pilih "Mail" → Generate
   - Salin password 16 karakter (tanpa spasi)
3. **Isi di `.env`**:
   ```
   IMAP_USER=your_email@gmail.com
   IMAP_APP_PASSWORD=abcdefghijklmnop
   EMAIL_HOST_USER=your_email@gmail.com
   EMAIL_HOST_PASSWORD=abcdefghijklmnop
   ```
4. **Pastikan IMAP aktif** di Gmail Settings → Forwarding and POP/IMAP → Enable IMAP

### 10.5. Menjalankan (Development)

Buka **3 terminal** terpisah:

**Terminal 1 — Django Server:**
```bash
python manage.py runserver
```

**Terminal 2 — Celery Worker:**
```bash
celery -A roc_desk worker --loglevel=info --pool=solo
```
> `--pool=solo` wajib digunakan di Windows. Di Linux, bisa menggunakan `--pool=prefork`.

**Terminal 3 — Celery Beat (Scheduler):**
```bash
celery -A roc_desk beat -l info
```

> **⚠️ URUTAN PENTING:** Jalankan Django Server terlebih dahulu, lalu Celery Worker, lalu Celery Beat.
> Jika ada perubahan kode Python, restart **Celery Worker** (Ctrl+C → jalankan ulang).

### 10.6. Menjalankan (Production)

```bash
# Gunakan Gunicorn (Linux)
gunicorn roc_desk.wsgi:application --bind 0.0.0.0:8000 --workers 4

# Celery Worker (daemon)
celery -A roc_desk worker --loglevel=info --detach

# Celery Beat (daemon)
celery -A roc_desk beat -l info --detach
```

### 10.7. Registrasi Data Awal

Setelah aplikasi berjalan, lakukan setup berikut via Django Admin (`/admin/`):

1. **Buat CompanyUnit** — Tambahkan unit organisasi (IT, Finance, HR, dll.)
2. **Buat Employee** — Daftarkan karyawan dengan:
   - `email` — Harus sesuai email yang digunakan karyawan (untuk matching IMAP)
   - `phone_number` — Format E.164 (contoh: `+6282331565773`). Harus sesuai nomor WA karyawan.
   - `unit` — Pilih CompanyUnit
3. **Buat CaseCategory** — Tambahkan kategori layanan yang tampil di portal client

> **PENTING:** Hanya pesan dari Employee terdaftar yang akan diproses. Pesan dari pengirim tak dikenal akan diabaikan (`discarded`).

### 10.8. Akses Aplikasi

| URL | Fungsi |
|-----|--------|
| `http://localhost:8000/` | Portal Client (publik) |
| `http://localhost:8000/desk/cases/` | Panel Staff (login required) |
| `http://localhost:8000/admin/` | Django Admin |
| `http://localhost:8000/auth/login/` | Halaman Login |

---

## 11. Alur Kerja (Workflow)

### 11.1. Alur Case dari Email

```mermaid
sequenceDiagram
    participant E as Employee
    participant G as Gmail
    participant C as Celery Beat
    participant D as Django
    participant S as Staff

    E->>G: Kirim email
    C->>G: Poll IMAP (setiap 60s)
    G-->>C: Email UNSEEN hari ini
    C->>D: Buat CaseRecord + Message
    D->>G: Auto-reply: "Tiket Diterima [CS-XXXX]"
    G-->>E: Terima konfirmasi tiket
    S->>D: Buka case di /desk/cases/
    S->>D: Ketik balasan di chat panel
    D->>C: Dispatch send_outbound_email_task
    C->>G: Kirim email balasan (threaded)
    G-->>E: Menerima email balasan (dalam thread yang sama)
```

### 11.2. Alur Case dari WhatsApp

```mermaid
sequenceDiagram
    participant E as Employee
    participant W as WhatsApp
    participant EV as Evolution API
    participant D as Django
    participant CL as Celery Worker
    participant S as Staff

    E->>W: Kirim pesan WA
    W->>EV: Pesan diterima
    EV->>D: POST /webhook/messages-upsert
    D->>CL: Dispatch process_evolution_webhook_task
    CL->>D: Buat CaseRecord + Message
    CL->>EV: Auto-reply WA: "Tiket CS-XXXX diterima"
    EV->>W: Kirim auto-reply
    W-->>E: Terima nomor tiket
    S->>D: Buka case di /desk/cases/
    S->>D: Ketik balasan di chat panel
```

### 11.3. Alur Case dari Web Form

```mermaid
sequenceDiagram
    participant E as Employee
    participant D as Django
    participant S as Staff

    E->>D: Buka /create/<kategori>/
    E->>D: Isi form + submit
    D->>D: Buat CaseRecord + Message
    D-->>E: Redirect ke halaman konfirmasi
    S->>D: Buka case di /desk/cases/
    S->>D: Proses + balas case
```

### 11.4. Alur Dynamic Form Builder

```mermaid
sequenceDiagram
    participant S as Staff (Admin)
    participant D as Django (Builder)
    participant K as Klien (Responden)

    S->>D: Masuk ke /desk/forms/ & Create Form
    S->>D: Tarik Drop, ubah label/Tipe via Edit Drawer HTMX
    S->>D: Mengatur Deskripsi Form & Field via Editor HTML (Quill)
    S->>D: Mencentang "Is Published"
    S-->>K: Membagikan tautan URL (/f/slug-form/)
    K->>D: Membuka URL Form (Merespon validasi Alpine.js & Multi-page)
    K->>D: Mengirim data. Form di-serialize ke JSON.
    D->>D: Menyimpan ke entitas FormSubmission
    S->>D: Mengecek tab Responses di Builder untu melihat input masuk
```

---

## 12. Design System — JokoUI

File: `static/css/jokoui.css`

| Komponen | Class Prefix | Deskripsi |
|----------|-------------|-----------|
| Layout | `.jk-page`, `.jk-sidebar`, `.jk-main`, `.jk-topbar` | Shell layout responsif |
| Cards | `.jk-card`, `.jk-card-clickable` | Kartu permukaan utama |
| Buttons | `.jk-btn-primary/secondary/success/danger` | Tombol aksi |
| Forms | `.jk-input`, `.jk-select`, `.jk-textarea` | Input elemen |
| Badges | `.jk-badge-open/investigating/resolved/closed` | Status badge |
| Chat Panel | `.jk-chat-panel`, `.jk-msg`, `.jk-reply-bar` | Chat interface profesional |
| Table | `.jk-table`, `.jk-table-wrapper` | Tabel data |

**Warna Utama:**
- Primary: `#6366f1` (Indigo)
- Secondary: `#0ea5e9` (Sky)
- Success: `#10b981` (Emerald)
- Warning: `#f59e0b` (Amber)
- Danger: `#ef4444` (Red)

---

## 13. Catatan Keamanan

1. **Webhook Token (Lenient)** — Evolution API v2 mengirimkan *instance token* via header `X-Evolution-Token`, bukan token webhook yang dikonfigurasi. Sistem menerima request dengan token berbeda (log warning) tetapi menolak jika token yang dikirim salah format.
2. **CSRF Protection** — Semua form POST menggunakan `{% csrf_token %}`. Webhook endpoint dikecualikan dengan `@csrf_exempt`.
3. **Employee Filtering** — Hanya email/WA dari Employee terdaftar yang diproses. Pengirim tak dikenal akan diabaikan (`discarded`).
4. **App Password** — Gmail menggunakan App Password (16 karakter) untuk autentikasi IMAP/SMTP, bukan password utama.
5. **UUID Primary Key** — Semua model menggunakan UUID v4 untuk menghindari sequential ID exposure.
6. **Message Deduplication** — Pesan WA dideduplikasi berdasarkan `external_id` (Evolution API message ID). Pesan duplikat diabaikan.
7. **Rate Limiting (Anti-Spam)** — Untuk mencegah spam dan serangan Denial of Service (DoS):
   - **WhatsApp**: Maksimal 3 tiket baru per nomor WA dalam 10 menit. Kasus baru ke-4+ akan ditandai `is_spam=True` secara diam-diam dan auto-reply *tidak* akan dikirim.
   - **Email**: Maksimal 3 tiket baru per email dalam 10 menit. Kasus baru ke-4+ ditandai `is_spam=True`. Sistem juga sepenuhnya memblokir email masuk yang memuat header *Auto-Submitted* atau *X-Auto-Response-Suppress* (mencegah *auto-reply loop* antar robot/sistem).
   - **Web Form**: Menggunakan *Django Cache* dengan batas 5 form submissions per IP Address dalam 10 menit. Pengguna akan melihat pesan error HTTP 429 jika melebihi batas.

---

## 14. Troubleshooting

| Masalah | Penyebab | Solusi |
|---------|----------|--------|
| WA webhook return 400 (tanpa error log Django) | `host.docker.internal` tidak ada di `ALLOWED_HOSTS` | Tambahkan `host.docker.internal` ke `ALLOWED_HOSTS` di `.env`, lalu **restart Django server** |
| WA webhook return 403 | Token mismatch (Evolution API v2 mengirim instance token) | Sudah ditangani (lenient validation). Jika masih error, periksa `views.py` |
| WA pesan masuk tapi tiket tidak terbuat | Pesan di-thread ke case aktif yang sudah ada | Normal jika ada case WA aktif dalam 30 menit terakhir. Cek Celery Worker log |
| Email tidak masuk sebagai tiket | Pengirim tidak terdaftar sebagai Employee | Daftarkan Employee dengan email yang sesuai di Django Admin |
| `Received unregistered task` di Celery | Task baru belum terdaftar di worker | **Restart Celery Worker** setiap ada perubahan kode `tasks.py` |
| IMAP Authentication Failed | App Password salah atau belum dibuat | Periksa App Password Gmail (16 karakter, tanpa spasi). Aktifkan 2-Step Verification |
| Email balasan tidak ter-thread di Gmail | Email lama tidak menyimpan Message-ID | Threading otomatis menggunakan fallback `case-<uuid>@domain`. Untuk email baru, threading sudah berfungsi |
| Balasan email tidak terkirim | Env SMTP tidak diisi | Isi `EMAIL_HOST_USER` dan `EMAIL_HOST_PASSWORD` di `.env`. Restart Celery Worker |
| `.env` berubah tapi tidak berlaku | Django tidak auto-reload saat `.env` berubah | **Restart Django server secara manual** (Ctrl+C → `python manage.py runserver`) |
| Evolution API ECONNREFUSED | Django tidak bisa dijangkau dari Docker | Gunakan `host.docker.internal:8000` sebagai webhook URL, bukan `localhost:8000` |
| Email lama ikut diproses | — | Sistem sudah difilter hanya email UNSEEN SINCE hari ini |

---

## 15. Checklist Instalasi Server Baru

Gunakan checklist ini untuk memastikan instalasi berjalan lancar:

- [ ] Python 3.10+ terinstall
- [ ] PostgreSQL 14+ berjalan
- [ ] Redis 7+ berjalan
- [ ] Docker terinstall (untuk Evolution API)
- [ ] Clone project + install dependencies (`pip install -r requirements.txt`)
- [ ] Jalankan `psql -U postgres -f init_db.sql`
- [ ] Salin `.env.template` → `.env` dan isi semua variabel
- [ ] **⚠️ Pastikan `ALLOWED_HOSTS` mengandung `host.docker.internal`**
- [ ] Jalankan `python manage.py migrate`
- [ ] Jalankan `python manage.py createsuperuser`
- [ ] Jalankan Evolution API via Docker
- [ ] Buat instance WA + scan QR Code
- [ ] Daftarkan webhook ke Evolution API (URL: `host.docker.internal:8000`)
- [ ] Buat Gmail App Password + isi di `.env`
- [ ] Jalankan Django Server (Terminal 1)
- [ ] Jalankan Celery Worker (Terminal 2)
- [ ] Jalankan Celery Beat (Terminal 3)
- [ ] Daftarkan Employee di Django Admin (email + phone_number)
- [ ] Test: Kirim email → cek tiket terbuat + auto-reply diterima
- [ ] Test: Kirim WA → cek tiket terbuat + auto-reply WA diterima
- [ ] Test: Buat tiket via Web Form → cek di panel staff

---

## 16. Integrasi dengan n8n (Workflow Automation)

RoC Support Desk didesain untuk mudah diintegrasikan dengan platform otomasi seperti **n8n**, **Make**, atau **Zapier**. Berikut adalah panduan konseptual dan teknis untuk mengintegrasikannya:

### 16.1. Skenario 1: Trigger dari RoC Desk ke n8n (Outbound Webhooks)
**Tujuan:** n8n bereaksi saat ada event di RoC Desk (misal: Case baru dibuat, atau Status berubah menjadi "Resolved").

**Cara Implementasi di Django:**
Anda cukup menambahkan mekanisme `post_save` signal di `cases/models.py`, yang menembakkan HTTP POST ke Webhook URL n8n Anda via Celery (agar tidak memblokir proses aplikasi utama).

**Contoh Kode Signal (Django):**
```python
# cases/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import CaseRecord
import requests
import json

@receiver(post_save, sender=CaseRecord)
def notify_n8n_on_case_change(sender, instance, created, **kwargs):
    # Ganti dengan Webhook URL dari n8n
    N8N_WEBHOOK_URL = "http://n8n-server.local:5678/webhook/roc-desk-event"
    
    payload = {
        "event": "case_created" if created else "case_updated",
        "case_id": str(instance.id),
        "case_number": instance.case_number,
        "subject": instance.subject,
        "status": instance.status,
        "requester": instance.requester.full_name if instance.requester else "Unknown"
    }
    
    try:
        # Gunakan timeout agar tidak hang
        requests.post(N8N_WEBHOOK_URL, json=payload, timeout=3)
    except Exception as e:
        print(f"Failed to notify n8n: {e}")
```

**Di n8n:**
1. Tambahkan node **Webhook**.
2. Set Http Method menjadi `POST`.
3. Gunakan URL yang tertera di layar node tersebut sebagai `N8N_WEBHOOK_URL` di kode Django.
4. Hubungkan node Webhook ini ke node lain (Slack, Jira, Discord, dll).

### 16.2. Skenario 2: Action dari n8n ke RoC Desk (Inbound API)
**Tujuan:** n8n yang memerintahkan RoC Desk (misalnya: Otomatis menutup tiket tertentu, atau membuat case otomatis saat invoice Xero gagal dibayar).

**Cara Implementasi di Django:**
Buat sebuah REST API endpoint sederhana di `cases/views.py` (atau pisahkan dalam app baru misal `api/`).

**Contoh Kode API Endpoint (Django):**
```python
# cases/api.py
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from .models import CaseRecord, CaseCategory
from core.models import Employee

@csrf_exempt
def api_create_case(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
        
    # Contoh autentikasi token sederhana
    token = request.headers.get("Authorization")
    if token != "Bearer MY_SECRET_API_TOKEN":
        return JsonResponse({"error": "Unauthorized"}, status=401)
        
    data = json.loads(request.body)
    
    # Logika pembuatan Case
    # ...
    return JsonResponse({"message": "Case created successfully", "case_number": "CS-12345678"})
```

**Di n8n:**
1. Gunakan node **HTTP Request**.
2. Set URL ke `http://your-django-server/api/v1/cases/create/`.
3. Set Method `POST` dan tambahkan Header Authentication.
4. Body format JSON sesuai parameter yang diminta API.

### 16.3. Skenario 3: Direct Database Connection (Cepat, khusus Report)
Jika digunakan untuk menarik data dalam jumlah banyak langsung ke Google Sheets/Metabase/Looker via n8n.

**Di n8n:**
1. Tambahkan node **PostgreSQL**.
2. Masukkan kredensial database yang digunakan oleh RoC Desk (Host, Port, User, Password, Database).
3. Anda bisa mengeksekusi `SELECT * FROM cases_caserecord WHERE status = 'Open'` langsung dari n8n!
> **Peringatan:** Sangat tidak disarankan menggunakan mode ini untuk kebutuhan *INSERT*/*UPDATE* data. Gunakan API untuk modifikasi data agar _business logic_ dan _SLA Tracking_ di Django tetap berjalan.

### 16.4. Skenario 4: Cron via n8n (Automated Reminders)
Anda bisa memindahkan logika Celery Beat rumit menjadi Workflow visual di n8n.

**Skenario**: n8n mengecek tiket "*Pending Info*" yang menganggur selama 2 hari setiap pagi jam 08:00, lalu mengeksekusi REST API Node ke RoC Desk, atau langsung menembak API WhatsApp (Evolution API) untuk Follow-up klien.

**Alur n8n:**
`Schedule Trigger (Setiap Hari 08:00)` ➡️ `PostgreSQL Node (Cari Tiket PendingInfo > 2 Hari)` ➡️ `Loop Node` ➡️ `HTTP Request (Tembak WhatsApp Webhook)`.
