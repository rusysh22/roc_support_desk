# Panduan Pengguna — RoC Support Desk

> Panduan ini ditujukan untuk seluruh pengguna sistem RoC Support Desk, mulai dari cara masuk ke sistem, mengajukan permintaan layanan, hingga pengelolaan tiket oleh staf pendukung (Support Agent dan Manager).

---

## Daftar Isi

1. [Memulai](#1-memulai)
   - 1.1 [Login ke Sistem](#11-login-ke-sistem)
   - 1.2 [Permintaan Akun Baru](#12-permintaan-akun-baru)
   - 1.3 [Lupa Password](#13-lupa-password)
2. [Portal Layanan](#2-portal-layanan)
   - 2.1 [Beranda Portal](#21-beranda-portal)
   - 2.2 [Mengajukan Tiket Baru](#22-mengajukan-tiket-baru)
   - 2.3 [Mengisi Dynamic Form](#23-mengisi-dynamic-form)
3. [Desk — Pengelolaan Tiket (Staf)](#3-desk--pengelolaan-tiket-staf)
   - 3.1 [Dashboard Analitik](#31-dashboard-analitik)
   - 3.2 [Daftar Tiket](#32-daftar-tiket)
   - 3.3 [Tampilan Kanban Board](#33-tampilan-kanban-board)
   - 3.4 [Tampilan Kalender](#34-tampilan-kalender)
   - 3.5 [Detail Tiket](#35-detail-tiket)
   - 3.6 [Membalas & Berkomunikasi](#36-membalas--berkomunikasi)
   - 3.7 [Internal Comment](#37-internal-comment)
   - 3.8 [Analisis Akar Masalah (RCA)](#38-analisis-akar-masalah-rca)
   - 3.9 [Alur Status Tiket](#39-alur-status-tiket)
   - 3.10 [Aksi Massal (Bulk Action)](#310-aksi-massal-bulk-action)
4. [Knowledge Base (Basis Pengetahuan)](#4-knowledge-base-basis-pengetahuan)
   - 4.1 [Melihat Artikel (Portal Publik)](#41-melihat-artikel-portal-publik)
   - 4.2 [Membuat Artikel (Staf)](#42-membuat-artikel-staf)
   - 4.3 [Alur Review & Publikasi](#43-alur-review--publikasi)
5. [Short Link & QR Code](#5-short-link--qr-code)
   - 5.1 [Membuat Short Link](#51-membuat-short-link)
   - 5.2 [Menggunakan & Membagikan Link](#52-menggunakan--membagikan-link)
6. [Notifikasi](#6-notifikasi)

---

## 1. Memulai

### 1.1 Login ke Sistem

Halaman login adalah pintu masuk utama ke sistem RoC Support Desk.

**Langkah-langkah:**

1. Buka alamat sistem di browser Anda (contoh: `https://support.perusahaan.com`).
2. Masukkan **Username** dan **Password** yang telah diberikan oleh administrator.
3. Klik tombol **Sign In**.
4. Jika berhasil, Anda akan diarahkan ke halaman dashboard sesuai peran (role) akun Anda.

> 💡 **Tips:** Jika Anda sudah pernah login sebelumnya dan lupa password, klik tautan **"Forgot password?"** di bawah tombol Sign In.

> ⚠️ **Perhatian:** Akun akan terkunci sementara jika terlalu banyak percobaan login yang gagal. Hubungi administrator jika mengalami masalah.

![Halaman Login](./screenshots/login.png)

---

### 1.2 Permintaan Akun Baru

Jika Anda belum memiliki akun dan perlu mengajukan permohonan, gunakan fitur **Request Account**.

**Langkah-langkah:**

1. Pada halaman login, klik tautan **"Request Account"** atau **"Don't have an account?"**.
2. Isi formulir permintaan akun:
   - **Full Name** — Nama lengkap Anda.
   - **Work Email** — Alamat email kantor yang aktif.
   - **What do you need access to?** — Jelaskan keperluan akses Anda secara singkat.
3. Klik tombol **Submit Request**.
4. Sistem akan menampilkan **nomor tiket** sebagai bukti permohonan Anda.
5. Tim administrator akan meninjau permohonan dan menghubungi Anda melalui email.

> 💡 **Tips:** Simpan nomor tiket yang muncul setelah berhasil mengirim permintaan. Nomor ini dapat digunakan untuk memantau status permohonan Anda.

> ⚠️ **Perhatian:** Satu alamat email hanya dapat digunakan untuk satu permohonan dalam rentang waktu tertentu. Jika tombol kirim tidak berfungsi, tunggu beberapa menit sebelum mencoba kembali.

![Halaman Request Account](./screenshots/request_account.png)

---

### 1.3 Lupa Password

Jika Anda lupa password, ikuti langkah pemulihan berikut:

**Tahap 1 — Minta Kode OTP:**

1. Pada halaman login, klik **"Forgot password?"**.
2. Masukkan **alamat email** yang terdaftar di sistem.
3. Klik **Send OTP**.
4. Sistem akan mengirimkan kode OTP 6 digit ke email Anda.

**Tahap 2 — Verifikasi & Reset Password:**

1. Buka email Anda dan catat kode OTP yang diterima.
2. Masukkan **Kode OTP** (6 digit) pada kolom yang tersedia.
3. Masukkan **Password Baru** yang diinginkan.
4. Masukkan kembali password pada kolom **Konfirmasi Password**.
5. Klik **Reset Password**.
6. Jika berhasil, Anda akan diarahkan kembali ke halaman login dengan pesan sukses.

> ⚠️ **Perhatian:** Kode OTP hanya berlaku selama beberapa menit. Jika kode sudah kedaluwarsa, klik **"Resend Code?"** untuk meminta kode baru.

> 💡 **Tips:** Pastikan email tidak masuk ke folder Spam/Junk. Periksa folder tersebut jika email OTP tidak ditemukan di Inbox dalam 1–2 menit.

![Halaman Forgot Password](./screenshots/forgot_password.png)

---

## 2. Portal Layanan

### 2.1 Beranda Portal

Beranda Portal adalah halaman utama yang dapat diakses oleh siapa saja (termasuk tanpa login, tergantung konfigurasi sistem).

**Yang tersedia di beranda:**

- **Kategori Layanan** — Daftar layanan yang tersedia, ditampilkan dalam bentuk kartu (card). Setiap kartu menampilkan ikon, nama kategori, dan deskripsi singkat.
- **Form Dinamis** — Daftar formulir khusus yang tersedia untuk diisi, jika ada.

**Cara menggunakan:**

1. Klik kartu kategori yang sesuai dengan kebutuhan Anda.
   - Jika kategori memiliki sub-kategori, Anda akan melihat daftar pilihan layanan yang lebih spesifik.
   - Jika kategori langsung memiliki formulir, Anda akan diarahkan ke halaman pengajuan tiket.
2. Ikuti navigasi hingga sampai di formulir pengajuan.

![Beranda Portal Layanan](./screenshots/client_dashboard.png)

---

### 2.2 Mengajukan Tiket Baru

Formulir pengajuan tiket digunakan untuk melaporkan masalah atau mengajukan permintaan layanan.

**Langkah-langkah:**

**Bagian 1 — Informasi Pemohon:**

1. Isi **Nama Lengkap** Anda.
2. Isi **Email Kantor** yang aktif — konfirmasi hasil pengerjaan akan dikirim ke email ini.
3. Pilih **Unit/Divisi** dari daftar yang tersedia (dapat dicari dengan mengetik).
4. Isi **Jabatan/Job Role** Anda.

**Bagian 2 — Detail Tiket:**

5. Pilih **Kategori Layanan** yang sesuai.
6. Isi **Subject** — ringkasan singkat masalah atau permintaan Anda.
   - Klik **"Generate Template"** jika tersedia, untuk mengisi otomatis berdasarkan kategori yang dipilih.
7. Isi **Deskripsi Masalah** — jelaskan masalah atau permintaan secara lengkap dan rinci.
8. Isi **Tautan Referensi** (opsional) — jika ada tautan yang mendukung laporan Anda.
9. Unggah **Lampiran** (opsional) — file pendukung seperti foto, dokumen, atau tangkapan layar.
   - Klik area unggah atau seret file ke dalamnya.
   - Anda dapat menambah file satu per satu tanpa menghapus file sebelumnya.
   - Klik ikon ✕ pada file untuk menghapusnya dari daftar.
10. Klik tombol **Submit**.

**Setelah Berhasil:**

- Sistem menampilkan halaman konfirmasi dengan **Nomor Tiket** Anda.
- Klik **"Email Ticket Info"** untuk menerima ringkasan tiket di email Anda.
- Klik **"Back to Service Portal"** untuk kembali ke beranda.

> 💡 **Tips:** Semakin detail deskripsi yang Anda tulis, semakin cepat tim dapat memahami dan menangani masalah Anda.

> ⚠️ **Perhatian:** Pastikan email yang dimasukkan aktif dan dapat diterima, karena pembaruan status tiket akan dikomunikasikan melalui email tersebut.

![Formulir Pengajuan Tiket](./screenshots/create_case.png)

---

### 2.3 Mengisi Dynamic Form

Selain tiket, sistem menyediakan formulir dinamis (Dynamic Form) untuk kebutuhan tertentu seperti survei, pendaftaran, atau pengumpulan data.

**Cara mengisi:**

1. Klik formulir yang tersedia di beranda portal atau buka tautan langsung yang diberikan.
2. Baca judul dan deskripsi formulir untuk memahami tujuannya.
3. Isi setiap pertanyaan sesuai instruksi:
   - Pertanyaan bertanda **\*** (bintang) adalah wajib diisi.
   - Beberapa formulir terdiri dari beberapa halaman — ikuti indikator halaman di bagian atas.
4. Klik **"Next"** untuk berpindah ke halaman berikutnya (jika ada).
5. Setelah semua halaman terisi, klik **"Submit"**.
6. Halaman konfirmasi akan muncul setelah pengiriman berhasil.

> ⚠️ **Perhatian:** Jangan menutup atau menyegarkan (refresh) halaman saat sedang mengisi formulir, karena data yang belum dikirim akan hilang.

---

## 3. Desk — Pengelolaan Tiket (Staf)

> Bagian ini khusus untuk pengguna dengan akses staf: **SupportDesk**, **Manager**, dan **SuperAdmin**.

### 3.1 Dashboard Analitik

Dashboard menampilkan ringkasan kinerja dan status tiket secara keseluruhan.

**Kartu KPI (Key Performance Indicator):**

| Kartu | Keterangan |
|-------|-----------|
| **Total Tickets** | Jumlah seluruh tiket dalam rentang waktu yang dipilih |
| **Active** | Tiket dengan status Terbuka, Sedang Ditangani, atau Menunggu Info |
| **Unassigned** | Tiket yang belum ditugaskan ke staf manapun |
| **Resolved** | Tiket yang sudah diselesaikan (termasuk jumlah hari ini) |
| **SLA Breached** | Tiket yang melewati batas waktu penanganan (SLA) |

**Filter Dashboard:**

- **Dari / Sampai** — Tentukan rentang tanggal analisis.
- **Agent** — Filter berdasarkan staf yang menangani.
- **Category** — Filter berdasarkan kategori layanan.
- **Source** — Filter berdasarkan saluran masuk (WhatsApp, Email, Web Form).
- **Priority** — Filter berdasarkan tingkat prioritas.

Klik **"Filter"** untuk menerapkan filter, atau klik ikon reset untuk kembali ke tampilan default.

![Dashboard Analitik](./screenshots/dashboard.png)

---

### 3.2 Daftar Tiket

Halaman daftar tiket menampilkan seluruh tiket yang ada dalam sistem.

**Cara mengakses:** Klik menu **"Cases"** di sidebar kiri.

**Informasi yang ditampilkan:**

| Kolom | Keterangan |
|-------|-----------|
| **No. Tiket** | Kode unik tiket (contoh: IT-AB12CD34) |
| **Subject** | Ringkasan masalah/permintaan |
| **Kategori** | Kategori layanan |
| **Status** | Status penanganan saat ini |
| **Prioritas** | Tingkat urgensi |
| **Pemohon** | Nama yang mengajukan |
| **Ditugaskan ke** | Nama staf yang menangani |
| **Dibuat** | Tanggal pengajuan |
| **Diperbarui** | Tanggal pembaruan terakhir |

**Filter & Pencarian:**

- Gunakan kotak **Search** untuk mencari berdasarkan subject atau nomor tiket.
- Gunakan filter **Status**, **Priority**, **Source**, **Category**, atau **Assignee** untuk menyempurnakan tampilan.

**Pilihan Tampilan:**

- **Tabel** — Tampilan default berbentuk daftar.
- **Kanban** — Tampilan kolom berdasarkan status.
- **Kalender** — Tampilan berdasarkan tanggal.

**Ekspor Data:**

Klik tombol **"Export"** untuk mengunduh daftar tiket ke format Excel.

![Daftar Tiket](./screenshots/case_list.png)

---

### 3.3 Tampilan Kanban Board

Kanban Board menampilkan tiket dalam bentuk kolom berdasarkan statusnya, memudahkan pemantauan alur penanganan secara visual.

**Cara mengakses:** Klik ikon Kanban di bagian atas halaman daftar tiket, atau navigasi ke menu **Kanban**.

**Kolom yang tersedia:**

| Kolom | Warna | Keterangan |
|-------|-------|-----------|
| **Open** | Kuning | Tiket baru, belum ditangani |
| **Investigating** | Biru | Sedang dalam proses investigasi |
| **Pending Info** | Ungu | Menunggu informasi tambahan dari pemohon |
| **Resolved** | Hijau | Masalah sudah diselesaikan |
| **Closed** | Abu-abu | Tiket ditutup secara final |

**Cara menggunakan:**

- Setiap kartu tiket menampilkan nomor tiket, subject, nama pemohon, prioritas, dan waktu pembaruan.
- Klik kartu untuk membuka detail tiket.
- Seret (drag) kartu dari satu kolom ke kolom lain untuk mengubah status tiket.

![Kanban Board](./screenshots/case_kanban.png)

---

### 3.4 Tampilan Kalender

Kalender menampilkan distribusi tiket berdasarkan tanggal, membantu perencanaan penanganan.

**Cara mengakses:** Klik ikon Kalender di bagian atas halaman daftar tiket, atau navigasi ke menu **Calendar**.

**Cara menggunakan:**

- Tanggal yang memiliki tiket akan ditandai.
- Klik tanggal tertentu untuk melihat daftar tiket pada hari tersebut.
- Gunakan tombol navigasi untuk berpindah bulan.

---

### 3.5 Detail Tiket

Halaman detail tiket adalah tempat utama pengelolaan sebuah tiket secara lengkap.

**Cara mengakses:** Klik nomor atau subject tiket dari daftar tiket atau kanban.

**Informasi Header Tiket:**

- **Nomor Tiket** — Kode unik tiket.
- **Subject** — Judul tiket (dapat diedit langsung).
- **Nama Pemohon, Email, No. Telepon, Jabatan, Unit** — Informasi kontak pemohon.
- **Status** — Status saat ini ditampilkan dengan badge warna.
- **Sumber** — Saluran masuk tiket (WhatsApp / Email / Web Form).
- **Prioritas** — Tingkat urgensi (Low / Medium / High / Critical).
- **Tipe** — Jenis permintaan (Question / Incident / Request).

**Panel Kiri — Thread Percakapan:**

Menampilkan seluruh riwayat pesan antara tim dan pemohon secara kronologis.

**Panel Kanan — Informasi & RCA:**

- Detail dan metadata tiket.
- Formulir Analisis Akar Masalah (RCA).
- Catatan internal (Quick Notes).

---

### 3.6 Membalas & Berkomunikasi

**Cara membalas tiket:**

1. Buka halaman detail tiket.
2. Gulir ke bawah ke area **Reply Composer** di panel kiri.
3. Ketik pesan balasan di kotak teks.
4. (Opsional) Lampirkan file dengan klik ikon lampiran.
5. (Opsional) Tambahkan **CC** jika perlu mengirim ke pihak lain (untuk saluran email).
6. Klik tombol **Send**.

Pesan yang dikirim akan muncul di thread percakapan dan diteruskan ke pemohon melalui saluran yang sama (WhatsApp, Email, atau Web).

**Mengelola pesan di thread:**

- **Edit pesan** — Klik ikon pensil pada pesan untuk memperbaiki isi pesan.
- **Hapus pesan** — Klik ikon hapus untuk menghapus pesan dari thread.

> ⚠️ **Perhatian:** Pesan yang dihapus tidak dapat dikembalikan. Pastikan Anda yakin sebelum menghapus.

---

### 3.7 Internal Comment

Internal Comment adalah catatan yang hanya dapat dilihat oleh sesama staf — tidak terlihat oleh pemohon.

**Cara menambahkan:**

1. Buka halaman detail tiket.
2. Temukan bagian **Internal Comments** (biasanya di bawah thread percakapan).
3. Ketik catatan di kotak yang tersedia.
4. Gunakan **@username** untuk menyebut (mention) rekan staf tertentu.
5. Klik **Add Comment**.

> 💡 **Tips:** Gunakan Internal Comment untuk berdiskusi dengan tim, mencatat temuan sementara, atau eskalasi internal tanpa diketahui oleh pemohon.

---

### 3.8 Analisis Akar Masalah (RCA)

RCA (Root Cause Analysis) wajib diisi sebelum tiket dapat diubah ke status **Resolved**.

**Cara mengisi RCA:**

1. Di panel kanan halaman detail tiket, temukan bagian **RCA**.
2. Isi **Root Cause Analysis** — jelaskan apa penyebab utama masalah (maks. 1.500 karakter).
3. Isi **Solving Steps** — jelaskan langkah-langkah yang dilakukan untuk menyelesaikan masalah (maks. 1.500 karakter).
4. (Opsional) Isi **Quick Notes** — catatan internal tambahan untuk referensi tim.
5. Klik **Save RCA** untuk menyimpan.

**Menggunakan Template RCA:**

Jika tersedia, klik salah satu tombol template di bawah kolom RCA. Template akan mengisi otomatis kolom Root Cause Analysis dan Solving Steps berdasarkan kategori tiket.

> ⚠️ **Perhatian:** Tombol **"Resolved"** tidak akan aktif jika RCA dan Solving Steps belum diisi.

---

### 3.9 Alur Status Tiket

Setiap tiket melewati alur status berikut:

```
Open  →  Investigating  →  Pending Info  →  Resolved  →  Closed
```

| Status | Keterangan |
|--------|-----------|
| **Open** | Tiket baru diterima, belum ada tindakan |
| **Investigating** | Staf sedang menginvestigasi masalah |
| **Pending Info** | Menunggu informasi tambahan dari pemohon |
| **Resolved** | Masalah telah diselesaikan (menunggu konfirmasi pemohon) |
| **Closed** | Tiket ditutup secara final |

**Cara mengubah status:**

1. Di halaman detail tiket, klik tombol status yang sesuai (contoh: **"Set to Investigating"**).
2. Konfirmasi perubahan jika diminta.
3. Status tiket akan diperbarui dan pemohon akan mendapat notifikasi (tergantung konfigurasi).

> ⚠️ **Perhatian:** Tiket yang sudah berstatus **Closed** tidak dapat diedit secara langsung. Diperlukan persetujuan Manager/SuperAdmin untuk melakukan perubahan pada tiket yang sudah ditutup. Lihat [Panduan Admin — Approval Edit Tiket Closed](./PANDUAN_ADMIN.md#9-approval-edit-tiket-closed).

---

### 3.10 Aksi Massal (Bulk Action)

Aksi massal memungkinkan Anda melakukan tindakan pada banyak tiket sekaligus.

**Cara menggunakan:**

1. Di halaman daftar tiket, centang kotak (checkbox) di sisi kiri baris tiket yang ingin dipilih.
2. Untuk memilih semua tiket yang terlihat, centang kotak di baris header tabel.
3. Pilih aksi dari menu **Bulk Actions** yang muncul:

| Aksi | Keterangan |
|------|-----------|
| **Mark as Read** | Tandai tiket sebagai sudah dibaca |
| **Mark as Unread** | Tandai tiket sebagai belum dibaca |
| **Archive** | Arsipkan tiket terpilih |
| **Unarchive** | Kembalikan tiket dari arsip |
| **Mark as Spam** | Tandai sebagai spam |
| **Assign to Agent** | Tugaskan ke staf tertentu |
| **Change Status** | Ubah status tiket sekaligus |
| **Export** | Ekspor tiket terpilih ke Excel |
| **Delete** | Hapus tiket (tidak dapat dikembalikan) |

> ⚠️ **Perhatian:** Aksi **Delete** bersifat permanen. Pastikan Anda telah memilih tiket yang tepat sebelum melanjutkan.

---

## 4. Knowledge Base (Basis Pengetahuan)

Knowledge Base adalah perpustakaan artikel yang berisi solusi, panduan, dan informasi yang dapat membantu pengguna dan staf.

### 4.1 Melihat Artikel (Portal Publik)

1. Klik menu **"Knowledge Base"** atau akses tautan `/kb/` di browser.
2. Gunakan kotak **Search** untuk mencari artikel berdasarkan kata kunci.
3. Atau telusuri artikel berdasarkan kategori yang tersedia.
4. Klik judul artikel untuk membuka dan membacanya.

---

### 4.2 Membuat Artikel (Staf)

Staf dapat membuat artikel knowledge base dari nol atau berdasarkan tiket yang sudah diselesaikan.

**Membuat artikel baru:**

1. Klik menu **"Knowledge Base"** di sidebar.
2. Klik tombol **"+ New Article"**.
3. Isi formulir artikel:
   - **Tipe Artikel** — Pilih *Issue* (panduan penyelesaian masalah) atau *Announcement* (pengumuman).
   - **Title** — Judul artikel yang deskriptif.
   - **Category** — Kategori layanan yang relevan.
   - **Problem Summary** — Ringkasan masalah yang dibahas.
   - **Root Cause** — Penjelasan penyebab masalah.
   - **Solution** — Langkah-langkah penyelesaian secara rinci.
4. Klik **Save as Draft** untuk menyimpan sebagai draf.

**Membuat artikel dari tiket yang sudah diselesaikan:**

1. Buka halaman detail tiket yang sudah berstatus Resolved atau Closed.
2. Klik tombol **"Create KB Article"**.
3. Sistem akan otomatis mengisi formulir artikel berdasarkan data RCA tiket.
4. Tinjau dan lengkapi isian, lalu simpan.

---

### 4.3 Alur Review & Publikasi

Artikel tidak langsung dipublikasikan. Artikel melewati proses review terlebih dahulu:

```
Draft  →  (Submit)  →  Pending Review  →  (Approve)  →  Published
                                        →  (Reject)   →  Rejected
```

| Status | Keterangan |
|--------|-----------|
| **Draft** | Artikel masih dalam tahap penulisan |
| **Pending Review** | Sudah diajukan, menunggu persetujuan Manager/SuperAdmin |
| **Published** | Artikel aktif dan dapat dilihat publik |
| **Rejected** | Artikel dikembalikan dengan catatan perbaikan |

**Cara mengajukan artikel untuk review:**

1. Buka artikel yang sudah selesai ditulis (status: Draft).
2. Klik tombol **"Submit for Review"**.
3. Artikel berubah status menjadi **Pending Review** dan menunggu persetujuan.

> 💡 **Tips:** Jika artikel ditolak (Rejected), baca catatan penolakan, perbaiki artikel, lalu ajukan kembali untuk review.

---

## 5. Short Link & QR Code

Fitur Short Link memungkinkan Anda membuat tautan pendek yang mudah dibagikan, beserta QR Code-nya.

### 5.1 Membuat Short Link

1. Klik menu **"Short Links"** di sidebar.
2. Klik tombol **"+ New Link"**.
3. Isi formulir:
   - **Target URL** — URL tujuan lengkap (wajib diisi).
   - **Custom Slug** — Kode pendek yang diinginkan, contoh: `promo2024` (harus unik).
   - **Card Title** — Judul yang tampil saat link dibagikan di media sosial (opsional).
   - **Card Description** — Deskripsi singkat untuk pratampil link (opsional).
4. Klik **Save**.
5. Short link Anda siap digunakan dengan format: `https://domain.com/s/promo2024`

> 💡 **Tips:** Sistem akan memberi tahu Anda secara langsung apakah slug yang dipilih sudah digunakan atau masih tersedia.

---

### 5.2 Menggunakan & Membagikan Link

**Menyalin link:**

1. Di halaman daftar Short Links, temukan link yang diinginkan.
2. Klik ikon **Copy** untuk menyalin tautan ke clipboard.

**Mengunduh QR Code:**

1. Di daftar Short Links, klik ikon **QR Code** pada link yang diinginkan.
2. Modal QR Code akan terbuka.
3. Klik **Download QR** untuk mengunduh gambar QR Code dalam format PNG.
4. QR Code dapat langsung dicetak atau dibagikan secara digital.

**Memantau klik:**

- Kolom **Clicks** pada daftar Short Links menampilkan jumlah total klik yang terjadi pada setiap tautan.

---

## 6. Notifikasi

Sistem notifikasi memberitahu Anda tentang aktivitas yang memerlukan perhatian.

**Cara mengakses:**

Klik ikon **lonceng (🔔)** di pojok kanan atas halaman.

**Jenis notifikasi:**

| Jenis | Keterangan |
|-------|-----------|
| **Mention** | Seseorang menyebut username Anda dalam Internal Comment |
| **Assignment** | Anda ditugaskan untuk menangani sebuah tiket |
| **Status Change** | Status tiket yang Anda tangani berubah |
| **New Message** | Ada pesan baru pada tiket yang Anda ikuti |

**Cara mengelola:**

- Klik notifikasi untuk langsung membuka tiket atau halaman yang relevan.
- Notifikasi yang sudah dibuka akan otomatis ditandai sebagai sudah dibaca.

---

*Panduan ini dibuat untuk versi terkini RoC Support Desk. Untuk pertanyaan lebih lanjut, hubungi administrator sistem Anda.*
