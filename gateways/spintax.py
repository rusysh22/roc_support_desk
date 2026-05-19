"""
Gateways — WhatsApp Message Template Library

Provides varied message templates for each outbound WhatsApp message type.
Templates are picked deterministically per-case (hash-based) so the same
case always produces the same variant, but different cases get different
variants — avoiding the uniform-template fingerprint that WhatsApp flags.

Usage::

    from gateways.spintax import pick_template, WA_ACK_USER

    text = pick_template(WA_ACK_USER, seed=case.case_number).format(
        name="Budi",
        site="RoC Desk",
        ticket_num="CASE-1234",
        subject="Printer Error",
    )
"""
from __future__ import annotations

import hashlib


def pick_template(templates: list[str], seed: str) -> str:
    """
    Deterministically pick one template from *templates* using *seed*.
    Same seed → same template every time; different seeds spread evenly.
    """
    idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(templates)
    return templates[idx]


# ---------------------------------------------------------------------------
# WA_ACK_USER
# Auto-reply sent to the customer when their new ticket is created.
#
# Required format kwargs: name, site, ticket_num, subject
# ---------------------------------------------------------------------------

WA_ACK_USER: list[str] = [
    # 1 — formal, no emoji flood
    (
        "Halo {name},\n\n"
        "Permintaan Anda sudah kami terima. Berikut detailnya:\n"
        "No. Tiket: {ticket_num}\n"
        "Perihal: {subject}\n\n"
        "Tim {site} akan segera menindaklanjuti. "
        "Balas pesan ini jika ada informasi tambahan yang perlu disampaikan."
    ),
    # 2 — semi-formal, satu emoji
    (
        "Hi {name} 👋\n\n"
        "Tiket Anda sudah masuk ke sistem kami.\n\n"
        "No. Tiket: {ticket_num}\n"
        "Perihal: {subject}\n\n"
        "Kami akan follow up secepatnya. "
        "Silakan balas pesan ini untuk menambahkan keterangan."
    ),
    # 3 — singkat, informal
    (
        "Halo {name}, terima kasih sudah menghubungi {site}.\n\n"
        "Request kamu sudah tercatat dengan nomor {ticket_num}. "
        "Tim kami akan segera memprosesnya — ditunggu ya!"
    ),
    # 4 — paragraf biasa, dua emoji
    (
        "✅ Tiket diterima!\n\n"
        "Halo {name}, pesan Anda ke {site} sudah berhasil kami catat.\n\n"
        "Nomor tiket: {ticket_num}\n"
        "Topik: {subject}\n\n"
        "Mohon ditunggu, tim support kami akan menghubungi Anda setelah meninjau permintaan ini."
    ),
    # 5 — bahasa formal, struktur berbeda
    (
        "Selamat {greet_time}, {name}.\n\n"
        "Kami informasikan bahwa permintaan Anda telah diterima oleh {site}.\n"
        "Nomor referensi tiket Anda adalah {ticket_num} dengan perihal: {subject}.\n\n"
        "Staf kami akan memproses permintaan ini dan memberikan tanggapan secepatnya. "
        "Untuk informasi tambahan, cukup balas pesan ini."
    ),
    # 6 — conversational, pendek
    (
        "Hi {name}! Pesan kamu sudah kami terima 😊\n\n"
        "Tiket #{ticket_num} untuk '{subject}' sudah dibuat. "
        "Tim {site} lagi proses ya. Kalau ada yang ingin ditambahkan, balas aja di sini."
    ),
    # 7 — formal full
    (
        "Kepada {name},\n\n"
        "Dengan hormat, kami ingin mengonfirmasi bahwa permintaan bantuan Anda "
        "telah berhasil kami terima.\n\n"
        "Detail tiket:\n"
        "- Nomor Tiket : {ticket_num}\n"
        "- Perihal     : {subject}\n\n"
        "Tim {site} akan segera menindaklanjuti permintaan Anda. "
        "Terima kasih atas kepercayaan Anda."
    ),
    # 8 — bullet-free, langsung ke poin
    (
        "Halo {name}, tiket Anda sudah kami catat di sistem {site}.\n\n"
        "Nomor Tiket: {ticket_num}\n"
        "Topik: {subject}\n\n"
        "Kami akan segera menghubungi Anda. "
        "Balas pesan ini kapan saja jika ada pertanyaan tambahan."
    ),
]


# ---------------------------------------------------------------------------
# WA_NOTIF_INTERNAL
# Notification sent to internal staff/admin when a new ticket arrives.
#
# Required format kwargs: site, ticket_num, requester_name, subject,
#                         priority, source_label, case_url
# ---------------------------------------------------------------------------

WA_NOTIF_INTERNAL: list[str] = [
    # 1 — minimal emoji, plain
    (
        "Tiket baru masuk — {site}\n\n"
        "No: {ticket_num}\n"
        "Dari: {requester_name}\n"
        "Perihal: {subject}\n"
        "Prioritas: {priority}\n"
        "Sumber: {source_label}\n\n"
        "{case_url}"
    ),
    # 2 — satu emoji di judul
    (
        "🔔 Tiket baru: {ticket_num}\n\n"
        "Dari {requester_name} via {source_label}.\n"
        "Perihal: {subject}\n"
        "Prioritas: {priority}\n\n"
        "Buka: {case_url}"
    ),
    # 3 — format narasi
    (
        "Ada tiket baru masuk ke {site}.\n\n"
        "{requester_name} mengajukan permintaan '{subject}' "
        "dengan prioritas {priority} melalui {source_label}.\n\n"
        "No. Tiket: {ticket_num}\n"
        "Link: {case_url}"
    ),
    # 4 — lebih pendek
    (
        "[{site}] Tiket #{ticket_num}\n"
        "Dari: {requester_name}\n"
        "Hal: {subject} ({priority})\n"
        "{case_url}"
    ),
    # 5 — dengan emoji terbatas
    (
        "📋 {ticket_num} — {subject}\n\n"
        "Pemohon: {requester_name}\n"
        "Prioritas: {priority} | Via: {source_label}\n\n"
        "{case_url}"
    ),
]


# ---------------------------------------------------------------------------
# WA_SESSION_WARNING
# Sent at ~45 minutes of inactivity.
# No format kwargs required (the message is generic).
# ---------------------------------------------------------------------------

WA_SESSION_WARNING: list[str] = [
    "Halo, apakah Anda masih membutuhkan bantuan? 😊 Sesi dukungan Anda akan otomatis berakhir sekitar 15 menit lagi jika tidak ada respons. Balas pesan ini jika masih ada yang ingin ditanyakan.",
    "Hi, masih ada yang bisa kami bantu? Sesi chat ini akan ditutup otomatis dalam sekitar 15 menit karena tidak ada aktivitas. Silakan balas jika masih butuh bantuan.",
    "Sekedar mengingatkan, sesi dukungan Anda akan berakhir dalam kurang dari 15 menit akibat tidak ada aktivitas. Mohon balas jika Anda masih memerlukan bantuan dari tim kami.",
    "Halo! Kami perhatikan belum ada respons dari Anda dalam beberapa saat. Sesi ini akan otomatis ditutup ±15 menit lagi. Balas jika masih ada pertanyaan ya.",
    "Tim kami menunggu respons Anda. Sesi bantuan ini akan segera berakhir karena tidak ada aktivitas. Kirim pesan jika masih ada yang perlu didiskusikan.",
]


# ---------------------------------------------------------------------------
# WA_SESSION_EXPIRED
# Sent at 60 minutes of inactivity (session close).
# No format kwargs required.
# ---------------------------------------------------------------------------

WA_SESSION_EXPIRED: list[str] = [
    "Sesi bantuan Anda telah berakhir karena tidak ada aktivitas selama 60 menit. Jika masih ada pertanyaan atau memerlukan bantuan lebih lanjut, silakan kirim pesan baru dan tiket baru akan dibuat untuk Anda.",
    "Karena tidak ada aktivitas selama satu jam, sesi dukungan ini telah kami tutup secara otomatis. Jangan ragu untuk menghubungi kami kembali kapan saja — tiket baru akan langsung dibuat.",
    "Sesi chat ini telah berakhir (60 menit tidak ada aktivitas). Untuk pertanyaan selanjutnya, cukup kirim pesan ke sini dan tim kami akan membuka tiket baru.",
    "Waktu sesi Anda telah habis setelah 60 menit tanpa aktivitas. Terima kasih telah menghubungi kami. Jika masih ada yang perlu dibantu, mulai percakapan baru ya.",
    "Sesi dukungan telah berakhir otomatis karena tidak ada respons lebih dari satu jam. Silakan hubungi kami kembali jika membutuhkan bantuan — kami siap membantu.",
]


# ---------------------------------------------------------------------------
# Helper: greeting by time of day (used by some WA_ACK_USER templates)
# ---------------------------------------------------------------------------

def greeting_by_hour(hour: int) -> str:
    """Return 'pagi', 'siang', 'sore', or 'malam' based on the WIB hour."""
    if 5 <= hour < 12:
        return "pagi"
    if 12 <= hour < 15:
        return "siang"
    if 15 <= hour < 19:
        return "sore"
    return "malam"
