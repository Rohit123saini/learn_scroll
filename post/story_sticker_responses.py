"""
post/story_sticker_responses.py

STORIES UPGRADE - PART 2b. What viewers send back to an interactive sticker,
and how it is shown:

    poll      one vote per viewer, final. The story owner always sees the
              counts; a viewer sees them only after voting (so the result does
              not sway the vote).
    question  one text answer per viewer (max 300 chars). Answers are private:
              only the story owner can list them.

The owner can never vote on / answer their own sticker.

This module owns the rules; the views in post/views.py are thin wrappers.
`build_interaction_stats` is what keeps the read path cheap: one grouped query
for all polls and one for all questions, however many stories are serialised.
"""
import re

from django.db import IntegrityError, transaction
from django.db.models import Count

from .models import StoryPollVote, StoryQuestionAnswer, StorySticker

MAX_ANSWER_LENGTH = 300

# Everything except \n and \t (an answer may have line breaks).
_ANSWER_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class ResponseError(Exception):
    """A vote / answer was refused. `status` is the HTTP status to answer with."""

    def __init__(self, message, status=400, code="invalid"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def parse_option_index(raw, option_count):
    """`raw` comes from JSON (int) or a form (digit string). Returns a valid
    0-based option index or raises ResponseError(400)."""
    if isinstance(raw, bool):
        raise ResponseError("'option' must be a whole number.", code="bad_option")
    if isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    if not isinstance(raw, int):
        raise ResponseError("'option' must be a whole number.", code="bad_option")
    if not (0 <= raw < option_count):
        raise ResponseError("That option does not exist.", code="bad_option")
    return raw


def clean_answer_text(raw):
    if not isinstance(raw, str):
        raise ResponseError("'text' is required.", code="bad_answer")
    text = _ANSWER_CONTROL_CHARS.sub("", raw).strip()
    if not text:
        raise ResponseError("'text' is required.", code="bad_answer")
    if len(text) > MAX_ANSWER_LENGTH:
        raise ResponseError(f"'text' can be at most {MAX_ANSWER_LENGTH} characters.", code="bad_answer")
    return text


def cast_vote(sticker, user, option_raw):
    """Record `user`'s vote on a poll sticker. Raises ResponseError."""
    if sticker.kind != StorySticker.KIND_POLL:
        raise ResponseError("This sticker is not a poll.", status=404, code="not_a_poll")
    if sticker.story.user_id == user.id:
        raise ResponseError("You can't vote on your own poll.", code="own_sticker")
    index = parse_option_index(option_raw, len((sticker.data or {}).get("options", [])))
    try:
        with transaction.atomic():
            return StoryPollVote.objects.create(sticker=sticker, user=user, option_index=index)
    except IntegrityError:
        # Two taps racing, or a retry after a lost response: the first vote stands.
        raise ResponseError("You have already voted on this poll.", status=409, code="already_voted")


def submit_answer(sticker, user, text_raw):
    """Record `user`'s answer to a question sticker. Raises ResponseError."""
    if sticker.kind != StorySticker.KIND_QUESTION:
        raise ResponseError("This sticker is not a question.", status=404, code="not_a_question")
    if sticker.story.user_id == user.id:
        raise ResponseError("You can't answer your own question.", code="own_sticker")
    text = clean_answer_text(text_raw)
    try:
        with transaction.atomic():
            return StoryQuestionAnswer.objects.create(sticker=sticker, user=user, text=text)
    except IntegrityError:
        raise ResponseError("You have already answered this question.", status=409, code="already_answered")


def build_interaction_stats(stickers, viewer):
    """{sticker_id: stats} for every poll / question in `stickers` (any other
    kind is skipped). At most 2 queries per kind, independent of how many
    stickers or stories are passed in.

        poll     -> {"counts": [n, n, ..], "total": n, "my_vote": int | None}
        question -> {"answers_count": n, "my_answered": bool}
    """
    polls = [s for s in stickers if s.kind == StorySticker.KIND_POLL]
    questions = [s for s in stickers if s.kind == StorySticker.KIND_QUESTION]
    stats = {}
    user_id = getattr(viewer, "id", None)

    if polls:
        ids = [s.id for s in polls]
        tally = {}
        for row in (
            StoryPollVote.objects.filter(sticker_id__in=ids)
            .order_by().values("sticker_id", "option_index").annotate(n=Count("id"))
        ):
            tally.setdefault(row["sticker_id"], {})[row["option_index"]] = row["n"]
        mine = dict(
            StoryPollVote.objects.filter(sticker_id__in=ids, user_id=user_id)
            .values_list("sticker_id", "option_index")
        ) if user_id else {}
        for s in polls:
            size = len((s.data or {}).get("options", []))
            per = tally.get(s.id, {})
            counts = [per.get(i, 0) for i in range(size)]
            stats[s.id] = {"counts": counts, "total": sum(counts), "my_vote": mine.get(s.id)}

    if questions:
        ids = [s.id for s in questions]
        totals = dict(
            StoryQuestionAnswer.objects.filter(sticker_id__in=ids)
            .order_by().values_list("sticker_id").annotate(n=Count("id"))
        )
        answered = set(
            StoryQuestionAnswer.objects.filter(sticker_id__in=ids, user_id=user_id)
            .values_list("sticker_id", flat=True)
        ) if user_id else set()
        for s in questions:
            stats[s.id] = {"answers_count": totals.get(s.id, 0), "my_answered": s.id in answered}

    return stats


def sticker_payload(sticker, stats, viewer_id, owner_id):
    """The kind-specific `data` for a poll / question, as the viewer may see it."""
    data = sticker.data or {}
    is_owner = viewer_id is not None and viewer_id == owner_id
    stat = stats.get(sticker.id) or {}

    if sticker.kind == StorySticker.KIND_POLL:
        my_vote = stat.get("my_vote")
        show_results = is_owner or my_vote is not None
        return {
            "question": data.get("question", ""),
            "options": list(data.get("options", [])),
            "my_vote": my_vote,
            "results": (
                {"counts": stat.get("counts", []), "total": stat.get("total", 0)} if show_results else None
            ),
        }

    return {
        "prompt": data.get("prompt", ""),
        "my_answered": bool(stat.get("my_answered")),
        # The number of answers is the owner's business only.
        "answers_count": stat.get("answers_count", 0) if is_owner else None,
    }
