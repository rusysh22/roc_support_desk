# Software Requirements Specification (SRS)
**Proyek:** RoC Support Desk
**Versi:** 1.0
**Tanggal:** 1 Maret 2026

---

## 1. Pendahuluan

### 1.1 Tujuan
Dokumen Software Requirements Specification (SRS) ini bertujuan untuk mendefinisikan spesifikasi kebutuhan perangkat lunak untuk aplikasi **RoC Support Desk**. Dokumen ini menjadi acuan utama bagi pengembang, penguji (QA), dan pemangku kepentingan (stakeholder) dalam memahami fitur, kapabilitas, dan batasan sistem.

### 1.2 Ruang Lingkup Sistem
**RoC Support Desk** adalah sistem manajemen tiket IT (IT Helpdesk/Support) berbasis web yang dirancang untuk mempermudah pelaporan insiden, permintaan layanan (service request), dan interaksi antara klien dengan agen dukungan. Sistem ini mencakup fitur:
- Pembuatan dan pelacakan tiket oleh klien.
- Manajemen tiket berbasis Kanban Board untuk Admin/Agent.
- Integrasi komunikasi multi-channel (WhatsApp via Evolution API & Email IMAP/SMTP).
- *Dynamic Form Builder* untuk pembuatan formulir kustom seperti kuesioner, reservasi, dll.
- Sistem manajemen SLA (Service Level Agreement).

### 1.3 Definisi Pengecualian dan Istilah
- **Klien / Pelapor (Requester):** Pengguna akhir yang membuat dan melacak tiket dukungan.
- **Admin / Agent:** Staf IT atau Customer Service yang menanggapi dan menyelesaikan tiket.
- **SLA (Service Level Agreement):** Batas waktu target penyelesaian tiket yang ditentukan berdasarkan prioritas.
- **Evolution API:** API pihak ketiga yang digunakan untuk menjembatani komunikasi WhatsApp secara tidak resmi melalui scan QR Code perangkat.

---

## 2. Kebutuhan Fungsional (Functional Requirements)

### 2.1 Manajemen Pengguna dan Autentikasi
- **FR-01:** Sistem harus memungkinkan pengguna login menggunakan email dan kata sandi.
- **FR-02:** Sistem harus membedakan hak akses dan antarmuka berdasarkan peran: `Client` (Portal Klien) dan `Agent/Admin` (Dasbor Admin).
- **FR-03:** Sistem harus memiliki fitur otentikasi reset password via email OTP (One Time Password).

### 2.2 Portal Klien (Client Portal)
- **FR-04:** Klien dapat membuat tiket baru dengan mengisi formulir standar (Subjek, Deskripsi, Kategori, Prioritas, Lampiran File).
- **FR-05:** Klien dapat melihat daftar tiket yang pernah dibuat beserta status terkininya.
- **FR-06:** Klien dapat berkomunikasi dengan agen di dalam halaman detail tiket (Sistem Komentar/Reply).

### 2.3 Manajemen Tiket (Admin Desk)
- **FR-07:** Admin dapat melihat daftar seluruh tiket yang masuk dalam tampilan tabel maupun Kanban Board (drag-and-drop status).
- **FR-08:** Admin dapat mengubah status tiket (Open, In Progress, Waiting, Resolved, Closed).
- **FR-09:** Admin dapat mengalokasikan (assign) tiket ke sesama agen.
- **FR-10:** Admin dapat membalas tiket klien melalui halaman detail kasus.

### 2.4 Integrasi Omnichannel
- **FR-11 (Email):** Sistem harus bisa mengambil email dari kotak masuk (via IMAP) dan secara otomatis mengkonversinya menjadi tiket baru atau balasan pada tiket yang sudah ada.
- **FR-12 (Email):** Sistem dapat mengirimkan notifikasi dan balasan email (via SMTP) ke klien.
- **FR-13 (WhatsApp):** Sistem dapat dihubungkan ke akun WhatsApp Admin dengan memindai QR Code via Evolution API.
- **FR-14 (WhatsApp):** Pesan WhatsApp dari klien yang ditujukan ke nomor Admin harus otomatis masuk sebagai pesan dukungan di dalam dasbor RoC Desk.

### 2.5 Dynamic Form Builder
- **FR-15:** Admin dapat membuat formulir publik secara dinamis menggunakan interface *Drag-and-Drop*.
- **FR-16:** Sistem harus mendukung berbagai tipe *field*: Short text, Long text, Email, Dropdown (Searchable), Checkbox, Radio, Date, Datetime, Survey Scale, File Upload Single/Multiple, HTML Description, dan Page Break.
- **FR-17:** Form yang diterbitkan harus memiliki URL unik (`/f/slug-form`) yang dapat dibagikan atau dipreview.
- **FR-18:** Hasil submit formulir dinamis harus masuk ke dalam database dan bisa dilihat oleh admin.
- **FR-19:** Admin dapat memodifikasi UI publik form (Background, Header Image, Badge, dll.).

---

## 3. Kebutuhan Non-Fungsional (Non-Functional Requirements)

### 3.1 Kinerja (Performance)
- **NFR-01:** Waktu muat halaman dasbor admin dengan daftar tiket (hingga 1.000 tiket) tidak boleh melebihi 3 detik.
- **NFR-02:** Tugas latar belakang (pengambilan email, webhook WA) tidak boleh mengganggu responsivitas antarmuka web.

### 3.2 Antarmuka (Usability)
- **NFR-03:** UI harus dirancang agar responsif penuh, dapat diakses dari perangkat Desktop, Tablet, maupun Mobile.
- **NFR-04:** UI harus mendukung pergerakan dinamis (drag-drop) tanpa memuat ulang (reload) halaman secara keseluruhan (mengutamakan penggunaan Alpine.js & HTMX).

### 3.3 Keamanan (Security)
- **NFR-05:** Password pengguna harus dienkripsi menggunakan algo hashing standar (PBKDF2/Argon2).
- **NFR-06:** File yang diunggah harus dibatasi maksimal ukurannya (ex: 10MB) dan difilter ekstensi filenya untuk mencegah eksesusi malicious script.
- **NFR-07:** Akses ke panel admin harus dilindungi oleh verifikasi session yang ketat.

---

## 4. Analisis Sistem

### 4.1 Tumpukan Teknologi (Tech Stack)
- **Backend:** Python + Django Framework
- **Frontend:** Tailwind CSS (Styling), Alpine.js (State management UI), HTMX (Interaksi asinkron tanpa full reload).
- **Rich Text Editor:** Quill.js
- **Task Queue / Background Jobs:** Celery + Redis
- **Database:** PostgreSQL / SQLite (Untuk dev).

---
*Dokumen SRS ini berlaku sebagai *blueprint* utama yang akan diubahsuaikan berdasarkan evolusi aplikasi.*
