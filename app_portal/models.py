from django.db import models


ROLE_CHOICES = [
    ("SuperAdmin", "Super Admin"),
    ("Manager", "Manager"),
    ("SupportDesk", "Support Desk"),
    ("Auditor", "Auditor"),
    ("PortalUser", "Portal User"),
]

ACCENT_CHOICES = [
    ("indigo", "Indigo"),
    ("slate", "Slate"),
    ("emerald", "Emerald"),
    ("amber", "Amber"),
    ("rose", "Rose"),
    ("sky", "Sky Blue"),
    ("violet", "Violet"),
    ("teal", "Teal"),
]


class AppPortalEntry(models.Model):
    name = models.CharField(max_length=200, verbose_name="App Name")
    description = models.TextField(verbose_name="Description")
    url = models.URLField(max_length=500, verbose_name="App URL")
    icon_emoji = models.CharField(
        max_length=10,
        default="🌐",
        verbose_name="Icon Emoji",
        help_text="Displayed when no image is uploaded.",
    )
    icon_image = models.ImageField(
        upload_to="app_portal/icons/",
        blank=True,
        null=True,
        verbose_name="Icon Image",
        help_text="If uploaded, takes priority over the emoji icon.",
    )
    badge_text = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Badge",
        help_text="Optional short label, e.g. 'New', 'Beta', 'Internal'.",
    )
    accent_color = models.CharField(
        max_length=20,
        choices=ACCENT_CHOICES,
        default="indigo",
        verbose_name="Accent Color",
        help_text="Card header gradient color theme.",
    )
    # Comma-separated role values, or the special value 'all'.
    # Example: "Manager,SuperAdmin" — only Manager and SuperAdmin can see this app.
    accessible_roles = models.CharField(
        max_length=200,
        default="all",
        verbose_name="Accessible By",
        help_text=(
            "Select which roles can see this app. "
            "Leave as 'all' to show to every logged-in user."
        ),
    )
    order = models.PositiveIntegerField(
        default=0,
        verbose_name="Display Order",
        help_text="Lower numbers appear first.",
    )
    is_active = models.BooleanField(default=True, verbose_name="Active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "App Portal Entry"
        verbose_name_plural = "App Portal Entries"

    def __str__(self):
        return self.name

    def is_accessible_by(self, role):
        if not self.accessible_roles or self.accessible_roles.strip() == "all":
            return True
        return role in [r.strip() for r in self.accessible_roles.split(",")]

    @property
    def accessible_roles_display(self):
        if not self.accessible_roles or self.accessible_roles.strip() == "all":
            return ["Semua Pengguna"]
        role_labels = dict(ROLE_CHOICES)
        return [role_labels.get(r.strip(), r.strip()) for r in self.accessible_roles.split(",")]
