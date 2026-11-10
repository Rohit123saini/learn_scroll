# post/comment_serializers.py


from rest_framework import serializers
from django.db.models import Count
from.models import PostComment, CommentMedia, CommentLike
from django.contrib.auth import get_user_model

User = get_user_model()

class CommentMediaSerializer(serializers.ModelSerializer):
    # Flutter ke liye camelCase me bhi bhej rahe hain + absolute url
    file = serializers.SerializerMethodField()
    file_name = serializers.CharField(read_only=True)
    file_size = serializers.IntegerField(read_only=True)

    # Flutter me fileName fileSize use hota hai
    fileName = serializers.CharField(source='file_name', read_only=True)
    fileSize = serializers.IntegerField(source='file_size', read_only=True)
    mimeType = serializers.CharField(source='mime_type', read_only=True)
    mediaType = serializers.CharField(source='media_type', read_only=True)

    class Meta:
        model = CommentMedia
        fields = ['id', 'media_type', 'mediaType', 'file', 'file_name', 'fileName', 'file_size', 'fileSize',
                  'mime_type', 'mimeType', 'created_at']
        read_only_fields = ['id', 'file_name', 'file_size']

    def get_file(self, obj):
        if not obj.file:
            return None
        request = self.context.get('request')
        try:
            url = obj.file.url
            if request:
                return request.build_absolute_uri(url)
            return url
        except:
            return str(obj.file)

class UserShortSerializer(serializers.ModelSerializer):
    # Tere User model me profile_photo hai
    profile_picture = serializers.SerializerMethodField()
    profilePicture = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'profile_picture', 'profilePicture']

    def get_profile_picture(self, obj):
        request = self.context.get('request')
        field = None
        if hasattr(obj, 'profile_photo') and obj.profile_photo:
            field = obj.profile_photo
        elif hasattr(obj, 'profile_picture') and getattr(obj, 'profile_picture', None):
            field = obj.profile_picture

        if field:
            try:
                url = field.url
                if request:
                    return request.build_absolute_uri(url)
                return url
            except:
                return str(field)
        return None

    def get_profilePicture(self, obj):
        return self.get_profile_picture(obj)

def stamp_reaction_state(comments, request):
    """P-C5: reaction state of a WHOLE page of comments in two queries (was 4 queries PER comment).

    Sets `_reaction_counts_cache` ({like, confuse, wrong, imp, explain, total}) and `_my_reaction_cache` on every
    comment. One grouped COUNT for all comments + one query for the viewer's own reactions, whatever the page size.
    """
    items = [c for c in comments if getattr(c, "pk", None) is not None]
    if not items:
        return
    ids = [c.pk for c in items]
    per_comment = {}
    for comment_id, reaction_type, n in (
        CommentLike.objects.filter(comment_id__in=ids)
        .values_list('comment_id', 'reaction_type')
        .annotate(c=Count('id'))
        .order_by()
    ):
        per_comment.setdefault(comment_id, {})[reaction_type] = n
    mine = {}
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        mine = dict(CommentLike.objects.filter(comment_id__in=ids, user=user).values_list('comment_id', 'reaction_type'))
    for c in items:
        c._reaction_counts_cache = _shape_reaction_counts(per_comment.get(c.pk, {}))
        c._my_reaction_cache = mine.get(c.pk)


def _shape_reaction_counts(counts):
    return {
        'like': counts.get('like', 0),
        'confuse': counts.get('confuse', 0),
        'wrong': counts.get('wrong', 0),
        'imp': counts.get('imp', 0),
        'explain': counts.get('explain', 0),
        'total': sum(counts.values())
    }


class PostCommentListSerializer(serializers.ListSerializer):
    """P-C5: `PostCommentSerializer(many=True)` stamps the reaction state of the whole page up front
    (see `stamp_reaction_state`), so a 50-comment list costs 2 reaction queries instead of ~200."""

    def to_representation(self, data):
        items = list(data.all() if hasattr(data, 'all') else data)
        stamp_reaction_state(items, self.context.get('request'))
        return super().to_representation(items)


class PostCommentSerializer(serializers.ModelSerializer):
    user = UserShortSerializer(read_only=True)
    media = CommentMediaSerializer(many=True, read_only=True)

    # Flutter compatibility - snake + camel dono
    likes_count = serializers.IntegerField(read_only=True)
    replies_count = serializers.IntegerField(read_only=True)
    likesCount = serializers.IntegerField(source='likes_count', read_only=True)
    repliesCount = serializers.IntegerField(source='replies_count', read_only=True)

    # NEW - Comment reaction like post
    my_reaction = serializers.SerializerMethodField()
    myReaction = serializers.SerializerMethodField()
    reaction_counts = serializers.SerializerMethodField()
    reactionCounts = serializers.SerializerMethodField()
    # Reply with a reel: null for an ordinary comment, else {id, available, thumbnail, caption, duration, author}.
    reply_reel = serializers.SerializerMethodField()
    replyReel = serializers.SerializerMethodField()

    class Meta:
        model = PostComment
        fields = [
            'id', 'post', 'user', 'parent', 'content', 'media',
            'likes_count', 'likesCount', 'replies_count', 'repliesCount',
            'is_edited', 'is_pinned', 'is_hidden', 'created_at', 'updated_at',
            'my_reaction', 'myReaction', 'reaction_counts', 'reactionCounts',
            'reply_reel', 'replyReel',
        ]
        read_only_fields = ['id', 'likes_count', 'replies_count', 'is_edited', 'created_at', 'updated_at']
        list_serializer_class = PostCommentListSerializer

    def get_my_reaction(self, obj):
        # Stamped for a whole page by PostCommentListSerializer; the single-object path (create / edit responses)
        # runs the query once and memoizes it, so the camelCase twin `myReaction` never queries again.
        if hasattr(obj, '_my_reaction_cache'):
            return obj._my_reaction_cache
        request = self.context.get('request')
        value = None
        if request and request.user.is_authenticated:
            like = CommentLike.objects.filter(comment=obj, user=request.user).first()
            value = like.reaction_type if like else None
        obj._my_reaction_cache = value
        return value

    def get_myReaction(self, obj):
        return self.get_my_reaction(obj)

    def get_reaction_counts(self, obj):
        if hasattr(obj, '_reaction_counts_cache'):  # stamped per page, or memoized by the first call (camelCase twin)
            return obj._reaction_counts_cache
        qs = CommentLike.objects.filter(comment=obj).values('reaction_type').annotate(c=Count('id')).order_by()
        obj._reaction_counts_cache = _shape_reaction_counts({r['reaction_type']: r['c'] for r in qs})
        return obj._reaction_counts_cache

    def get_reactionCounts(self, obj):
        return self.get_reaction_counts(obj)

    def get_reply_reel(self, obj):
        if not obj.reply_reel_id:
            return None
        from .reply_reels import reply_reel_payload
        return reply_reel_payload(obj.reply_reel, self.context.get('request'))

    def get_replyReel(self, obj):
        return self.get_reply_reel(obj)

class CreateCommentSerializer(serializers.Serializer):
    post_id = serializers.UUIDField(required=False, allow_null=True)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    content = serializers.CharField(required=False, allow_blank=True, default='')
    # Reply with a reel: id of one of MY video posts; only allowed together with `parent_id` (a reply).
    reel_post_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_content(self, value):
        # H23 - HTML / control / bidi-override characters out, length refused (never silently cut)
        from .rich_text import clean_comment
        return clean_comment(value)

    def to_internal_value(self, data):
        mutable = data.copy() if hasattr(data, 'copy') else dict(data)
        if mutable.get('parent_id') in ['', 'string', 'null']:
            mutable['parent_id'] = None
        if mutable.get('post_id') in ['', 'string', 'null']:
            mutable['post_id'] = None
        if mutable.get('reel_post_id') in ['', 'string', 'null', 'None']:
            mutable['reel_post_id'] = None
        return super().to_internal_value(mutable)

    def validate(self, attrs):
        if not attrs.get('post_id') and not attrs.get('parent_id'):
            raise serializers.ValidationError({"post_id": "post_id is required for top-level comment"})
        if attrs.get('reel_post_id') and not attrs.get('parent_id'):
            raise serializers.ValidationError({"reel_post_id": "A reel can only be attached to a reply"})
        return attrs