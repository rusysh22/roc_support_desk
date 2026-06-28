# Changelog — RoC Support Desk

All notable changes to this project will be documented in this file.

---

## [1.4.7] — 2026-05-24

### New Features

- **Ticket Category Management** — Ticket categories and sub-categories can now be fully managed from the desk UI without needing to access Django admin.
  - Create, edit, and delete root categories and sub-categories directly from the Categories page.
  - Configure icon (emoji picker), name, parent category, description, and prefix code per category.
  - Toggle behaviour flags per category: Confidential, Attachment Required, and Change Request (supporting letter).
  - Set a ticket template subject and pre-filled body text per category from the same form.
  - A confirmation screen with safety checks prevents deletion of categories that still have active tickets or sub-categories.

- **Document Template Management** — Document templates for supporting letters can now be created and edited entirely from the desk UI.
  - Write the document body using a full rich-text (Quill) editor or switch to raw HTML source mode.
  - Dynamic field variables (`{{placeholder}}`) are detected automatically and shown as chips while editing.
  - Define input fields users must fill in when completing the document: label, placeholder hint, optional default content, and required toggle. Up to 10 fields per template.
  - Assign templates to one or more ticket categories from the same form.

### Improvements

- **Categories sidebar link** — A new "🗂️ Categories" shortcut is now available in the sidebar for Manager and Super Admin roles.
- **Document Template list** — Removed broken Approval Flow and Required columns (fields removed in a previous migration). The list now correctly shows field count and applicable categories.

---

## [1.4.0] — 2026-05-14

### New Features

- **User Manual** — A dedicated documentation section is now available at `/usermanual/`.
  - Staff can create multiple application manuals (e.g. D365, HR System, etc.), each with their own set of pages and sub-pages organized in a flexible hierarchy — no limit on how many levels deep it can go.
  - Each page is written using a full-featured editor that supports headings, bold/italic text, bullet and numbered lists, quotes, code snippets, tables, images, and embedded YouTube videos.
  - Any page can be marked as an FAQ to distinguish it from regular guide pages.
  - Pages stay in draft until a Manager or Super Admin explicitly publishes them. Only published pages are visible to regular users.
  - Staff (Support Desk and above) can create and edit pages; publishing and deletion require Manager or Super Admin access.
  - A "📖 User Manual" shortcut is available in the sidebar for all users and opens in a new tab.

- **Timezone preference per user** — Each user can now set their preferred timezone from their Profile page.
  - All dates and times shown throughout the system — tickets, chat messages, activity logs — will automatically display in the user's selected timezone.
  - The system also detects the user's browser timezone and suggests applying it automatically on the Profile page.

### Improvements

- **Knowledge Base editor** — When writing a Knowledge Base article, staff can now switch between a visual rich-text editor and a plain Markdown editor depending on their preference. Both modes are supported on the article's public view as well.

- **My Tickets — unread message indicator** — The notification badge showing unread messages has been redesigned. It now sits neatly inside the "Open Chat" button itself, making it cleaner and easier to spot without cluttering the surrounding layout.

### Fixes

- **Chat widget stability** — Fixed an issue where the chat widget would sometimes show errors in the background when a user's session had expired or they were not logged in. The widget now checks session validity before attempting to connect.

---

## [1.3.4] — 2026-05-12

### New Features

- **Short Link** — Staff can create shortened URLs for any link and share them with users. Each short link has a dedicated public redirect page and can generate a QR code.
- **Form Creator** — Staff can design custom intake forms with configurable fields, drag-and-drop ordering, and publish them for users to fill out.
- **Auditor role** — A new read-only role that can view tickets, forms, and links without being able to make any changes.
- **Subscription management** — A licensing system tracks whether the installation is on a trial, active subscription, or has expired, with grace period handling and automatic verification.

### Improvements

- **Chat portal** — Typing indicators, message quoting, file attachments, and unread message tracking were added to the chat experience.
- **Staff online presence** — The system now tracks and displays which staff members are currently active in a chat session.
- **My Tickets page** — Portal users (customers) can now view all their submitted tickets in one place and continue chatting from there.

---

## [1.3.3] — 2026-04-30

### New Features

- **Embeddable chat widget** — A chat bubble that can be embedded on any external website, allowing customers to submit tickets directly from there.
- **Live two-way chat** — Real-time messaging between staff and customers, with message delivery confirmation and online presence detection.
- **Closing note** — Staff can write a summary note when marking a ticket as resolved.

### Improvements

- **Ticket search** — The dashboard and case list now support full-text search across ticket subject, employee NIK, and company unit.
- **Image viewer in chat** — Images attached to chat messages can be expanded into a full-screen lightbox view.

---

## [1.3.2] — 2026-04-23

### New Features

- **Knowledge Base public portal** — A searchable, categorized knowledge base that any user can browse without logging in.
- **Auto-generate KB articles from resolved tickets** — When a ticket is resolved, its root cause analysis can be promoted directly into a Knowledge Base article.
- **Single sign-on (SSO)** — Users can log in using their existing Microsoft or Google account.
- **Forgot password with OTP** — Users who forget their password can reset it via a one-time code sent to their registered email.
- **Forced password change on first login** — Newly created accounts are required to set a new password before accessing the system.

### Improvements

- **Outbound email credentials** — SMTP email settings can now be managed directly from the admin panel without touching server configuration files.

---

## [1.3.1] — 2026-04-10

### New Features

- **WhatsApp integration** — Tickets are created automatically when a customer sends a WhatsApp message to the connected number. An acknowledgment reply with the ticket number is sent back immediately.
- **Email integration** — Tickets are created automatically from inbound emails. An acknowledgment email with the ticket number is sent back to the sender.
- **Ticket categories with SLA** — Each ticket category can have a configurable service level target (response and resolution time).
- **Company unit management** — Departments and units can be structured in a tree, used for routing and filtering tickets.

---

## [1.3.0] — 2026-03-15

### New Features

- Initial release of RoC Support Desk.
- Ticket submission via three channels: WhatsApp, Email, and Web Form.
- Role-based access for all staff levels: Super Admin, Manager, Support Desk, Auditor, and Portal User.
- Split-panel ticket detail view with live chat on the left and root cause analysis form on the right.
- Real-time notifications for new tickets and messages.
- System-wide branding configuration (site name, logo, favicon) manageable from the admin panel.
- Sensitive configuration (email and gateway passwords) stored encrypted in the database.
