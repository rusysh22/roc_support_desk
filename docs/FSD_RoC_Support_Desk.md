# Functional Specification Document (FSD)
**Proyek:** RoC Support Desk
**Versi:** 1.0
**Tanggal:** 1 Maret 2026

---

## 1. Pendahuluan
Dokumen FSD ini menjabarkan rincian teknis dari fitur-fitur fungsional yang sudah ditetapkan pada SRS. Dokumen ini fokus pada *bagaimana* sistem bekerja pada tingkat modul, struktur data, dan rekayasa interaksi pada antar-muka *RoC Support Desk*.

---

## 2. Spesifikasi Modul (Module Breakdown)

### 2.1 Modul Authentikasi & User Profile
- **Login:**
  - Metode: Form Based Authentication menggunakan Email dan Password.
  - Alur: User memasukkan kredensial -> Validasi Database -> Buat HTTP Session -> Redirect ke `/portal/` (Client) atau `/desk/` (Admin).
  - Pengecualian: Invalid kredensial, akun dinonaktifkan.
- **Reset Password:**
  - Fungsi mengirimkan OTP numerik 6-digit ke email user. OTP disimpan sementara di cache/database. Diperlukan validasi `<otp>`, `<new_password>`, `<confirm_password>`.

### 2.2 Modul Portal Klien
- **Form Pembuatan Tiket:**
  - Input: Subject (String), Category (Dropdown model kustom), Priority (Low, Normal, High, Urgent), Description (Rich Text/Textarea), Attachments (Multiple File Input).
  - Aksi: Menyimpan entitas `Case`. Memicu (trigger) sinyal untuk mengirimkan Email Acknowledgment otomatis kepada klien (`send_case_acknowledgment_task`).
- **Dashboard Klien:**
  - Menampilkan metrik tiket (Total Open, Total Resolved).
  - Tabel kasus klien dengan fitur pencarian dan paginasi.

### 2.3 Modul Admin Desk & Kanban
- **Tampilan Tabel List:**
  - Menampilkan kasus dengan kolom referensi tiket, status, pelapor, SLA tag.
- **Kanban Board:**
  - Kolom status standar: `New/Open`, `In Progress`, `Waiting on Customer`, `Resolved`, `Closed`.
  - Integrasi: Menggunakan SortableJS. Mengirim request AJAX/HTMX POST untuk memodifikasi `status` tabel `Case` di database saat kartu tiket di drag & drop ke kolom baru.
- **Detail Kasus (Ticket View):**
  - Thread komunikasi: Merender percakapan antar sistem dan klien secara kronologis berurutan ke bawah.
  - Kotak Balasan: Mendukung penambahan Catatan Internal (Internal Note - kuning, privat admin) atau Balasan Publik (biru, dikirim ke pelanggan).
  - Log Aktivitas (Audit Trail): Status berubah, assignee ditugaskan.

### 2.4 Modul Integrasi Email & WhatsApp
- **Email Fetching (Celery Task):**
  - Skrip berkala (`imap_lib`) mengecek folder INBOX di email dukungan perusahan.
  - Membaca pengirim. Jika pengirim sudah ada, tiket dikaitkan. Jika subjek memiliki regex `[CASE-XXXX]`, diparsing untuk membalas `Case` *existing*.
- **Evolution API (WhatsApp):**
  - **Koneksi:** Admin masuk ke Settings -> Integrations -> WhatsApp. Sistem mengambil data instance dari API Evolution dan merender QR Code ke browser. Admin scan pakai WA HP.
  - **Webhook:** Endpoint webhook disediakan Django (`/api/whatsapp/webhook/`) untuk menerima POST JSON tiap ada pesan masuk ke WA. Data JSON diparsing guna menarik nomor WA, nama kontak, tipe pesan, dan otomatis masuk ke tabel pesan tiket terkait.

### 2.5 Modul Dynamic Form Builder
Fitur utama yang mirip Google Forms.
- **Struktur Data:**
  - `DynamicForm`: Entitas form induk (Judul, URL slug, Publikasi Status, Header Image, Background Color).
  - `FormField`: Field anak dengan jenis tipe-tipe spesifik (Text, Email, Dropdown, Page Break, dll). Terikat `ForeignKey` ke `DynamicForm`. Disertai kolom JSON `choices`.
  - `FormSubmission`: Menampung jawaban publik. Kolom `answers` berbentuk struktur `JSONB` yang memetakan ID field ke nilainya.
- **Pembuat / Builder (Frontend Alpine.js):**
  - Drag & Drop pengurutan antar *field* (`SortableJS`).
  - Laci penyunting properti (Edit Drawer) per field menggunakan HTMX.
  - **Fitur Khusus: HTML Rich Text Editor (Quill.js)** teraplikasi terpusat pada *Form Description* utama dan *Field Description* (`help_text`). Ini memungkinkan admin membuat uraian narasi form yang rapi dengan tebal, miring, poin (*bullet/numbering list*), dan deteksi *link*. Output form publik sepenuhnya *safe-rendered* terhindar dari suntikan tag berbahaya.
- **Eksekusi Form Publik (`/f/slug/`):**
  - Dukungan multi-halaman jika terdapat `page_break` type. Alpine mengamankan state nomor halaman `currentPage`.
  - Searchable dropdown diaplikasikan secara kustom dengan input hidden.
  - Preview Mode (`?preview=1`): Mengeksekusi tampilan form secara bypass (tanpa limit akses/publik) dan mematikan fungsi submit data sehingga klien tidak tak sengaja menambah *junk data*.
  - Penyelesaian: File di-handle terpisah pada saat request POST, diregistrasikan alamat url aslinya yang disimpan ke state `answers` JSON.

---

## 3. Struktur Database (Entity Relationship Poin Inti)
- `User`: Extend `AbstractBaseUser`.
- `Case (Ticket)`: ID unik (`CASE-0001`), Subject, Deskripsi, Status, Priority, SLA Timeout, Assignee, Requester.
- `Message`: Berisi balasan / komen per tiket. Kolom `is_internal`, `channel_source` (WEB, EMAIL, WHATSAPP).
- `DynamicForm` & `FormField`: Berkaitan satu sama lain (One-to-Many).
- `FormSubmission`: Data array JSON.

---
*End of Document FSD.*
