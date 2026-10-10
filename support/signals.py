"""Keep FeatureRequest.votes_count a pure function of the FeatureVote rows
(same approach as user_profile/signals.py for follower counts): recomputed on
every vote write from ANY path — API, admin, shell, cascade delete."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import FeatureRequest, FeatureVote


def _recount(request_id):
    n = FeatureVote.objects.filter(request_id=request_id).count()
    FeatureRequest.objects.filter(pk=request_id).update(votes_count=n)


@receiver(post_save, sender=FeatureVote, dispatch_uid="support_vote_saved")
def _vote_saved(sender, instance, **kwargs):
    _recount(instance.request_id)


@receiver(post_delete, sender=FeatureVote, dispatch_uid="support_vote_deleted")
def _vote_deleted(sender, instance, **kwargs):
    _recount(instance.request_id)
