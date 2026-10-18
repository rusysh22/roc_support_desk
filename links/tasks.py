"""
Links App — Celery Tasks
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(soft_time_limit=600, time_limit=660)
def compress_portal_video(link_id, media_name):
    """
    Re-encode a redirect-portal video uploaded for ``link_id``. ``media_name``
    guards against a race: if the link's media was replaced or removed while
    this ran, the result is discarded.
    """
    from .media import compress_stored_video
    from .models import ShortLink

    link = ShortLink.objects.filter(pk=link_id, portal_media=media_name).first()
    if not link:
        return

    compressed = compress_stored_video(link.portal_media)
    if compressed is None:
        return

    storage = link.portal_media.storage
    new_name = storage.save(link.portal_media.field.generate_filename(link, compressed.name), compressed)
    updated = ShortLink.objects.filter(pk=link_id, portal_media=media_name).update(portal_media=new_name)
    # Drop whichever file is no longer referenced
    storage.delete(media_name if updated else new_name)
    if updated:
        logger.info("Compressed portal video for link %s -> %s", link_id, new_name)
