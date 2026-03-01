# Alur Kerja (Workflow) Sistem
**Proyek:** RoC Support Desk

Dokumen ini menjelaskan alur kerja operasional utama dalam sistem **RoC Support Desk**.

---

## 1. Alur Pembuatan dan Penanganan Tiket (Core Ticketing Workflow)

1. **Pembuatan Tiket (Klien):**
   - Klien login ke Portal Klien (`/portal/`).
   - Klien mengklik tombol **"Create New Ticket"**.
   - Mengisi formulir: Subject, Kategori (contoh: *Hardware*, *Software*, *Network*, *Billing*), Prioritas (*Low, Normal, High, Urgent*), Deskripsi, dan Lampiran pendukung.
   - Mengklik submit. Sistem menghasilkan ID unik (contoh: `CASE-1042`) dan mengirim email konfirmasi bahwa tiket diterima.

2. **Penerimaan Tiket (Admin):**
   - Tiket baru masuk ke tampilan Kanban Board pada list **"Open"** di Dasbor Admin (`/desk/`).
   - Agen atau Supervisor meninjau tiket yang baru masuk.

3. **Penugasan (Assignment):**
   - Supervisor/Agen membuka tiket.
   - Menugaskan tiket (assign) kepada Agen teknisi yang relevan berdasarkan Kategori/Beban kerja.
   - Status diubah ke **"In Progress"**.

4. **Pengerjaan & Komunikasi:**
   - Agen mengeksekusi penyelesaian teknis.
   - Agen menambah balasan/pesan pembaruan kepada klien jika ada informasi baru (ditandai dengan indikator pesan Publik).
   - Klien dan Agen saling membalas pada *thread* kasus.
   - Jika butuh respons dari klien, Agen memindahkan tiket ke kolom Kanban **"Waiting on Customer"**.

5. **Penyelesaian:**
   - Setelah isu teratasi secara tuntas, Agen (atau Klien) mengubah status tiket menjadi **"Resolved"**.
   - Waktu penyelesaian (Resolution Time) dihentikan (untuk penghitungan performa SLA).

6. **Penutupan (Closed):**
   - Jika dalam beberapa hari tidak ada sanggahan lebih lanjut dari Klien, admin secara permanen mengubah status ke **"Closed"**.

---

## 2. Alur Integrasi WhatsApp (Evolution API Workflow)

1. **Klien Menghubungi WA Admin:**
   - Klien mengirim pesan (Teks / Media) ke nomor server WhatsApp Admin perusahaan (nomor yang di-scan QR-nya oleh API Evolution).
2. **Webhook Memeriksa Kontak:**
   - API Evolution menembak (`POST`) *payload JSON* ke server Django (`/api/whatsapp/webhook/`).
   - Django mencari apakah `remoteJid` (No. Telepon) sudah terdaftar pada pengguna (User) sistem.
     - Jika belum ada: Tiket dengan status **"New"** baru otomatis dibuat, menggunakan nomor HP sebagai nama referensi sementara.
     - Jika sudah ada tiket terbuka (terakhir) untuk nomor tersebut: Pesan dimasukkan sebagai balasan (message) pada kasus tersebut.
3. **Balasan Admin Via Web:**
   - Admin melihat pesan WA baru masuk di dalam antarmuka tiket Dasbor Admin.
   - Admin mengetik balasan pada *text editor* sistem dan mengirimkannya.
   - Sistem mengirim instruksi *Send Text* API ke endpoint Evolution API. Klien menerima balasan via WhatsApp-nya sendiri.

---

## 3. Alur Dynamic Form Builder (Custom Forms)

1. **Membuat Konstruksi Form:**
   - Admin masuk ke menu "Forms" lalu mengklik **"Design New Form"**.
   - Menyesuaikan pengaturan dasar: Judul, URL Slug, Background.
2. **Merancang Bidang Pertanyaan (Fields):**
   - Menambahkan kotak/kolom pertanyaan (seperti Text, Rating Scale, File Attachment) via UI *Drag & Drop*.
   - Mengisi deskripsi dan konfigurasi tiap tipe field melalui laci panel properti di kanan (termasuk *help_text* melalui Quill.js Rich Editor).
   - Menyesuaikan urutan halaman (opsional via tipe `page_break`).
3. **Publikasi & Distribusi:**
   - Admin mengubah status ke `Is Published (Live)`.
   - Mengcopy *Shareable Link* (`/f/slug-url/`) atau menge-klik tombol *Send Invitation* untuk mendistribusikan link form per email borongan.
4. **Pengisian oleh Responden (User-Side):**
   - Responden membuka link form.
   - Form merender seluruh interaksi *dynamic state* via framework Alpine.js.
   - Jika multi-halaman aktif, form divalidasi dan diarahkan antar seksi (*Next-Prev*).
   - Submit -> JSON Data diformat dengan *FormData* -> Masuk ke backend database.
5. **Analisis Jawaban:**
   - Admin membuka form -> Klik "Responses". 
   - Melihat jawaban dari pengguna yang mendaftar berupa list submission entry.
