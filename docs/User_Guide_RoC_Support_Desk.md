# Panduan Pengguna (User Guide)
**Proyek:** RoC Support Desk

Dokumen panduan ini dirancang untuk memudahkan Admin maupun Klien dalam menggunakan fungsi aplikasi secara praktis dan operasional sehari-hari.

---

## BAGIAN A: PANDUAN KLIEN / PENGGUNA (Client Portal)
Akses melalui **`/portal/`**

### 1. Masuk / Registrasi ke Portal
- Buka dashboard utama web via browser yang diberikan oleh pihak institusi/perusahaan (misal: `domain.com`).
- Jika Anda pelanggan yang dilayani, ketikkan email dan password Anda pada kotak masuk (Login).
- Jika ada kendala lupa sandi, klik tulisan **"Forgot Password?"**, ketik email Anda, dan sistem akan mengirimkan instruksi via surel.

### 2. Membuka Tiket (Create Ticket)
Untuk mengadukan masalah IT / Non-IT:
- Klik tombol biru bertuliskan **"New Ticket"** di kanan atas dasbor portal klien.
- Isi *Judul Masalah* Anda dengan spesifik dan padat. Katakan secara langsung apa inti kendala.
- Pilih **Kategori** rujukan (IT Support, HR, Sales, dll).
- Set Tingkat Darurat/Prioritas (**Low, Normal, High, Urgent**). *Gunakan Urgent jika menyangkut system-down parah*.
- Ceritakan secara meluas pada boks editor *Description*. Bila ada tangkapan layar, masukkan ke kotak **Lampiran File** dengan menyorot area tersebut untuk mengunggah berkas.
- Klik **Submit** (Ajukan Tiket). Setelah di submit, form Anda dialihkan ke Detail Tiket.

### 3. Memantau Status dan Berinteraksi
- Dari halaman utama, lihat list tabel "My Technical Cases".
- **Klik** baris tiket untuk masuk meninjau percakapan.
- Akan tampak kotak area obrolan mirip forum. Pada area terendah, Anda dapat mengetikkan pertanyaan tambahan untuk tim dukungan yang bertugas.

---

## BAGIAN B: PANDUAN ADMIN / AGEN (Dashboard Desk)
Akses melalui **`/desk/`**

### 1. Akses dan Navigasi Dasar
- Anda diharuskan login dengan kredensial staf sistem admin. Begitu login sukses, Anda ditujukan ke antarmuka Dasbor Manajemen Kasus (`/desk/`). Kiri layar adalah tautan pintas (**Sidebar Menu**), dan kanan memuat ruang presentasi kerja utama.

### 2. Pemantauan via Papan Kanban (Kanban View)
Kanban menawarkan pandangan mata burung atas operasional.
- Klik ikon "Tampilan Kolom" (di atas dekat bilah cari).
- Anda mendapati kartu tiket berjajar per kolom status (New, In Progress, Waiting On Customer, Resolved, Closed).
- **Prosedur Seret/Jatuh (Drag & Drop):** Tekan kiri tetikus lalu tarik kartu prioritas dari kolom *Open* ke *In Progress*, maka database akan langsung terbarukan. Sangat efektif untuk manajer mengkoordinir pergerakan tugas tim.

### 3. Cara Membalas / Manajemen Detail Kasus
- Klik salah satu Tiket (`CASE-XXXX`) di list data.
- **Set assignee (Pengerja):** Di tepi menu samping kanan atas bilah Info Case, ada dropdown 'Assignee'. Pilih nama dari sesama teman operator Anda yang paling cocok memegang tiket (misal agen IT vs agen sales).
- Membalas Pesan Klien:
  1. Scroll ke paling bawah thread. Ada editor ketik bawaan (Quill Textbox).
  2. Saat kursor masuk, klik mode **"Public Reply"** (balasan reguler ditujukan ke client). 
  3. Atau tekan panah mode sakelar di tombol send dan ubah jadi **"Add Internal Note"** jika Anda sekadar ingin corat-coret informasi untuk rekan teknisi agar Klien tidak membacanya.

### 4. Setup Dynamic Form Builder (Google Form Alternative)
- Masuk tab bilah Kiri Menu: **"Forms"**.
- Klik tombol hijau "Create Form" (Kanan atas layar).
- Anda disuguhi lembar kosong *Canvas Builder*. Mulailah menekan tombol **"Add Field"** pada *Toolbar Atas*.
- Klik pada sebuah elemen modul pertanyaan (misalnya, teks input 'Alamat'). Jendela sifat/properties terbuka di sisi kiri layar.
- Mengganti pertanyaannya ketuk `Question Label`. Bila opsi jamak, tambahkan baris data pada area array propertinya.
- Untuk keterangan tambahan formulir, gunakan **"Form Description"** pada editor panel sebelah kiri (mendukung HTML rich format layaknya cetak rekam bold dan list).
- Memecah Halaman: Jika formulir teramat sangat panjang, inputkan properti spesial bernama **"Section / Page Break"**. Pada tataran antarmuka pratinjau klien (URL Public), form menjadi dibagi ke model "Next / Prev" button page wizard.
- Jangan Lupa tekan `Preview` (icon mata ungu di Top Header Toolbar) lalu ubah parameter status internal ke **"Is Published / Live"** di properti checkbox jika formulir siap ditelan publisitas umum. Semua link akses form bisa ditekan pada URL Badge `/f/...` di bawah nama form.
