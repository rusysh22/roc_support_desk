from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils.html import strip_tags

from knowledge_base.models import Article

from .models import Announcement
from .services import post_announcement


@receiver(pre_save, sender=Article)
def remember_article_published(sender, instance, **kwargs):
    instance._was_published = bool(
        instance.pk and Article.objects.filter(pk=instance.pk, is_published=True).exists()
    )


@receiver(post_save, sender=Article)
def announce_published_kb_announcement(sender, instance, **kwargs):
    """A KB article of type Announcement joins the announcement feed the first time it is published."""
    if instance.article_type != Article.ArticleType.ANNOUNCEMENT or not instance.is_published:
        return
    if getattr(instance, "_was_published", False):
        return
    already_posted = Announcement.objects.filter(
        source_content_type=ContentType.objects.get_for_model(Article), source_object_id=str(instance.pk),
    ).exists()
    if already_posted:
        return
    summary = " ".join(strip_tags(instance.problem_summary or "").split())[:300]
    post_announcement(
        title=instance.title,
        summary=summary,
        source_type=Announcement.SourceType.KNOWLEDGE_BASE,
        source=instance,
        link_url=reverse("knowledge_base:article_detail", kwargs={"slug": instance.slug}),
        visibility=Announcement.Visibility.PUBLIC,
        actor=instance.updated_by or instance.created_by,
    )
