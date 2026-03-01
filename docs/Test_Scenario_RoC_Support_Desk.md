# Skenario Pengujian (Test Scenario)
**Proyek:** RoC Support Desk

Dokumen ini memandu tahapan verifikasi kualitas (*Quality Assurance*) untuk memastikan fungsionalitas aplikasi RoC Support Desk berjalan sesuai standar spesifikasi.

---

## Modul 1: Autentikasi dan Manajemen Pengguna

| ID Test | Skenario Pengujian | Langkah Eksekusi | Hasil yang Diharapkan (Expected Result) | Status |
|---|---|---|---|---|
| AUTH-01 | Login pengguna valid (Klien) | 1. Buka halaman login web.<br>2. Masukkan email & sandi valid akun Klien.<br>3. Tekan Login. | Sistem mengarahkan user ke `/portal/` (Dasbor Klien). | [ ] |
| AUTH-02 | Login dengan kredensial salah | 1. Masukkan email benar, sandi salah.<br>2. Tekan Login. | Muncul notifikasi "Invalid Credentials". Akses ditolak. | [ ] |
| AUTH-03 | Lupa Password (OTP Email) | 1. Klik "Lupa Sandi".<br>2. Masukkan email tujuan.<br>3. Input OTP dari kotak masuk.<br>4. Reset password baru. | Sandi otomatis diperbarui. User dapat masuk ke sistem dengan password baru. | [ ] |
| AUTH-04 | Validasi Peran Admin | 1. Login menggunakan akun dengan privilege `is_staff=True`. | Sistem mengarahkan user ke `/desk/` (Dasbor Admin). | [ ] |

---

## Modul 2: Uji Pembentukan Kasus (Ticketing)

| ID Test | Skenario Pengujian | Langkah Eksekusi | Hasil yang Diharapkan (Expected Result) | Status |
|---|---|---|---|---|
| TCK-01 | Klien membuat tiket reguler (Tanpa file) | 1. Di Portal, klik **New Ticket**.<br>2. Isi Form Teks Penuh lalu submit. | Muncul popup Success. Tiket baru dengan ID ter-generate berstatus 'Open' tampil di tabel Klien. Desk berisi tiket baru. | [ ] |
| TCK-02 | Klien membuat lampiran ganda (Multiple Files) | 1. Di Portal, upload 3 file `.png` (ukuran <5MB).<br>2. Submit form. | Semua lampiran tersimpan utuh dan dapat didownload pada detail tiket. | [ ] |
| TCK-03 | Verifikasi ukuran file lebih dari batas maksimum batas server | 1. Unggah file video berukuran 50MB.<br>2. Klik Submit. | Frontend Client / Validasi Server menolak file, menampilkan error *File too large*. | [ ] |
| TCK-04 | Admin Memindahkan Status Tiket via Kanban | 1. Admin login ke Desk Kanban.<br>2. Drag kartu tiket dari "Open" ke "In Progress". | Kartu berpindah kolom. Status diatur ulang dan tercatat di History Timeline bahwa tiket sedang dikerjakan. | [ ] |
| TCK-05 | Percakapan Two-Way pada Detail Tiket | 1. Agen mengetik balasan Publik. Submit.<br>2. Klien melihat tiket dari sisi portal.<br>3. Klien membalas pesan. | Chat termuat secara kronologis Real-time atau reload, pesan agen dengan status biru (publik), dari klien putih/abu-abu beda pihak. | [ ] |
| TCK-06 | Admin Internal Note | 1. Agen mengirim balasan dengan *toggle* kuning *Internal Note*.<br>2. Klien membuka portal. | Pesan kuning/Internal Note SAMA SEKALI tidak muncul di dashboard klien portal. Privasi agen aman. | [ ] |

---

## Modul 3: Dynamic Form Builder

| ID Test | Skenario Pengujian | Langkah Eksekusi | Hasil yang Diharapkan (Expected Result) | Status |
|---|---|---|---|---|
| FRM-01 | Membuat Formulir Baru Kosong | 1. Masuk fitur Forms -> Create Form.<br>2. Isi title dan centang Published.<br>3. Buka link (copy). | Lembar Preview publik terbuka tapi menampilkan peringatan "Belum ada field" tanpa error server. | [ ] |
| FRM-02 | Input & Render Dropdown Tersortir | 1. Admin seret elemen "Dropdown" ke susunan Builder.<br>2. Beri 4 opsi choices (A, B, C, D). Save.<br>3. Beralih ke Klien URL form. | Element dropdown interaktif (*searchable Alpine.js component*) bisa diketik/diklik tanpa menutupi element di bawah (Z-index issue solved). | [ ] |
| FRM-03 | Testing HTML Editor Deskripsi | 1. Admin mengetik deskripsi tebal (Bold) bertitik angka (Ordered List) lewat panel properties Editor Quill.<br>2. Lihat Form via mode `/f/link`. | Format teks (HTML, Ul/Li List) dikonversi dan tampil dengan tepat layaknya di Editor. | [ ] |
| FRM-04 | Halaman Terpisah Berjejak (Page Break) | 1. Beri "Page Break" elemen tepat di nomor urut 3 bidang isian. Simpan.<br>2. Pada Client URL, coba isi sebagian dari bagian P1. | Halaman hanya ter-display P1. Ada step navigasi *(1/2)* dan tombol **Next** di bagian bawah. Validasi 'Required' mencegah next step bila field wajib di page 1 belum komplit . | [ ] |
| FRM-05 | Submit Responden ke Sistem | 1. Klien melengkapi seluruhan Multi-page Forms.<br>2. Tekan **Submit** di akhir.<br>3. Admin menekan **Responses** di builder. | Tab Response admin menyimpan rekap JSON lengkap data input yang di input oleh Tester barusan. File upload berhasil di simpan (Terdapat anchor link ke media dir). | [ ] |

---

## Modul 4: Integrasi Automatis WhatsApp

| ID Test | Skenario Pengujian | Langkah Eksekusi | Hasil yang Diharapkan (Expected Result) | Status |
|---|---|---|---|---|
| WA-01  | Connect WhatsApp Core Evolution API | 1. User masuk Settings -> WhatsApp.<br>2. Scan Barcode QR Code.<br>3. Cek ping network WA. | Status hijau `Connected` dengan status instance WA aktif, Webhook tertanam otomatis di Evolution Container. | [ ] |
| WA-02  | Nomor HP Baru membuat Obrolan "Halo" | 1. Pengirim anonim (Belum terdaftar User Sistem) men-chat WA. | Tiket baru `CASE-WA` tercipta di Dasbor Admin berstatus `New`. Subject menampung pesan WA "Halo". | [ ] |
| WA-03  | Balasan via Admin Desk Web Tembus ke WA Relasi | 1. Admin masuk ke Obrolan Tiket WA-02 tadi.<br>2. Ketik *Public Reply* dan Kirim. | Chat diterima secara organik oleh pengirim anonim di device Smartphone/WhatsApp mereka 1x1 detik kemudian. | [ ] |

---
**Catatan Penguji Khusus:** Untuk simulasi WA Webhook, jalankan image `evolution-api` docker background secara parallel sebelum pengujian WA-01 dieksekusi. Gagalnya koneksi bisa merestart status di backend.
