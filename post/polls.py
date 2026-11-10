"""
post/polls.py - Poll + Quiz logic (everything the views / serializers share).

What lives here
---------------
* parse/validate the create payload  : `clean_poll_payload()` (2-6 options, image options, duration 1h-7d,
                                       anonymous / public, multiple choice, results visibility, quiz mode)
* create the rows                    : `create_poll()`
* the double-vote guard + grading    : `cast_vote()`  (row lock on the poll + DB UniqueConstraint(poll, user, option))
* who may see what                   : `results_visible_for()` / `answers_revealed_for()`
* creator results screen             : `build_results()` (+ `export_rows()` for the CSV)
* quiz leaderboard / score           : `build_leaderboard()`, `my_quiz_score()`
* "convert to Test Series question"  : `convert_to_question()` (talks to `testseries.bridge`, never to its models)

Privacy rule for anonymous polls: the voter of an anonymous poll is NEVER returned by any function here (the
vote rows exist only because they ARE the double-vote guard).
"""
import csv
import io
import json
import re
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.contrib.auth import get_user_model
from django.db.models import Min
from django.utils import timezone
from rest_framework import serializers

from .models import PostPoll, PostPollOption, PostPollQuestionLink, PostPollVote, recount_poll_votes

MAX_OPTION_TEXT = 200
MAX_EXPLANATION = 500
MAX_OPTION_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_OPTION_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif"}
OPTION_IMAGE_FIELD = "poll_option_image_%d"  # multipart file key for option #index (0-based)
DEFAULT_DURATION_HOURS = 24
LEADERBOARD_SIZE = 20


class PollError(Exception):
    """A business-rule failure the view turns into `{"detail", "code"}` + `status`."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# Create payload
# ---------------------------------------------------------------------------
def _as_bool(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_int(value, field):
    if isinstance(value, bool):
        raise serializers.ValidationError({"poll_settings": f"{field} must be a whole number."})
    try:
        return int(value)
    except (TypeError, ValueError):
        raise serializers.ValidationError({"poll_settings": f"{field} must be a whole number."})


def _check_option_image(file):
    name = getattr(file, "name", "") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_OPTION_IMAGE_EXT:
        raise serializers.ValidationError({"poll_options": "Option images must be JPG, PNG, WEBP or GIF."})
    if file.size > MAX_OPTION_IMAGE_BYTES:
        raise serializers.ValidationError({"poll_options": "An option image can be at most 5 MB."})
    try:  # really an image? (extension alone is not proof)
        from PIL import Image

        file.seek(0)
        Image.open(file).verify()
        file.seek(0)
    except Exception:
        raise serializers.ValidationError({"poll_options": "That option image could not be read."})


def option_image_files(files):
    """Every poll option image in `request.FILES` (`poll_option_image_0` .. `_5`), in option order."""
    out = []
    if files is None:
        return out
    for i in range(PostPoll.MAX_OPTIONS):
        f = files.get(OPTION_IMAGE_FIELD % i)
        if f is not None:
            out.append(f)
    return out


def clean_poll_payload(options, raw_settings, files):
    """Validate `poll_options` + `poll_settings` + option image files.

    `options`      list of {"text": str, "is_correct": bool?} (client's `votes` key is ignored)
    `raw_settings` dict or None. None = a legacy client: single choice, results after voting, no expiry.
    `files`        request.FILES (option #i image = files[poll_option_image_<i>])

    Returns `(options, settings)` - cleaned, ready for `create_poll()`. Raises DRF ValidationError.
    """
    if not isinstance(options, list):
        raise serializers.ValidationError({"poll_options": "poll_options must be a list."})
    n = len(options)
    if n < PostPoll.MIN_OPTIONS:
        raise serializers.ValidationError({"poll_options": f"A poll needs at least {PostPoll.MIN_OPTIONS} options."})
    if n > PostPoll.MAX_OPTIONS:
        raise serializers.ValidationError({"poll_options": f"A poll can have at most {PostPoll.MAX_OPTIONS} options."})

    cleaned, seen = [], set()
    for idx, opt in enumerate(options):
        if not isinstance(opt, dict):
            raise serializers.ValidationError({"poll_options": "Every option must be an object."})
        text = str(opt.get("text") or "").strip()
        image = files.get(OPTION_IMAGE_FIELD % idx) if files is not None else None
        if image is not None:
            _check_option_image(image)
        if not text and image is None:
            raise serializers.ValidationError({"poll_options": "Poll options cannot be empty."})
        if len(text) > MAX_OPTION_TEXT:
            raise serializers.ValidationError(
                {"poll_options": f"Poll options must be {MAX_OPTION_TEXT} characters or fewer."})
        if text:
            if text.lower() in seen:
                raise serializers.ValidationError({"poll_options": "Poll options cannot be the same."})
            seen.add(text.lower())
        cleaned.append({"text": text, "image": image, "is_correct": _as_bool(opt.get("is_correct"))})

    legacy = raw_settings is None
    if not legacy and not isinstance(raw_settings, dict):
        raise serializers.ValidationError({"poll_settings": "poll_settings must be an object."})
    raw = raw_settings or {}

    # ---- duration: 1 hour .. 7 days (legacy: no expiry)
    if legacy:
        duration = None
    else:
        duration = raw.get("duration_hours")
        duration = DEFAULT_DURATION_HOURS if duration in (None, "") else _as_int(duration, "duration_hours")
        if not PostPoll.MIN_DURATION_HOURS <= duration <= PostPoll.MAX_DURATION_HOURS:
            raise serializers.ValidationError(
                {"poll_settings": "Poll duration must be between 1 hour and 7 days."})

    visibility = str(raw.get("results_visibility") or PostPoll.RESULTS_AFTER_VOTE)
    if visibility not in dict(PostPoll.RESULTS_CHOICES):
        raise serializers.ValidationError({"poll_settings": "results_visibility must be always, after_vote or after_end."})

    is_quiz = _as_bool(raw.get("is_quiz"))
    allow_multiple = _as_bool(raw.get("allow_multiple"))
    correct = [i for i, o in enumerate(cleaned) if o["is_correct"]]
    if is_quiz:
        if not correct:
            raise serializers.ValidationError({"poll_options": "A quiz needs at least one correct option."})
        if not allow_multiple and len(correct) != 1:
            raise serializers.ValidationError(
                {"poll_options": "A single-answer quiz needs exactly one correct option."})
        if allow_multiple and len(correct) == n:
            raise serializers.ValidationError({"poll_options": "At least one option must be wrong."})
    else:
        for o in cleaned:
            o["is_correct"] = False  # a normal poll has no right answer, whatever the client sent

    if allow_multiple:
        if is_quiz:
            max_choices = n  # an exact-set quiz must let the person tick every option
        else:
            raw_max = raw.get("max_choices")
            max_choices = n if raw_max in (None, "") else _as_int(raw_max, "max_choices")
            if not 2 <= max_choices <= n:
                raise serializers.ValidationError(
                    {"poll_settings": f"max_choices must be between 2 and {n} for a multiple-choice poll."})
    else:
        max_choices = 1

    explanation = str(raw.get("explanation") or "").strip() if is_quiz else ""
    if len(explanation) > MAX_EXPLANATION:
        raise serializers.ValidationError(
            {"poll_settings": f"The explanation can be at most {MAX_EXPLANATION} characters."})

    return cleaned, {
        "duration_hours": duration,
        "allow_multiple": allow_multiple,
        "max_choices": max_choices,
        "is_anonymous": _as_bool(raw.get("is_anonymous")),
        "results_visibility": visibility,
        "is_quiz": is_quiz,
        "explanation": explanation,
    }


def create_poll(post, options, settings):
    """Persist a validated poll (see `clean_poll_payload`). Returns the PostPoll."""
    hours = settings.get("duration_hours")
    poll = PostPoll.objects.create(
        post=post,
        expires_at=(timezone.now() + timedelta(hours=hours)) if hours else None,
        allow_multiple=settings["allow_multiple"],
        max_choices=settings["max_choices"],
        is_anonymous=settings["is_anonymous"],
        results_visibility=settings["results_visibility"],
        is_quiz=settings["is_quiz"],
        explanation=settings["explanation"],
    )
    for idx, opt in enumerate(options):
        PostPollOption.objects.create(
            poll=poll, text=opt["text"], image=opt["image"], is_correct=opt["is_correct"], display_order=idx)
    return poll


# ---------------------------------------------------------------------------
# Who may see what
# ---------------------------------------------------------------------------
def is_owner(poll, user):
    return bool(user and getattr(user, "is_authenticated", False) and poll.post.user_id == user.id)


def results_visible_for(poll, user, voted):
    """Live numbers (votes_count / total) - the creator always, others per `results_visibility`."""
    if is_owner(poll, user):
        return True
    if poll.results_visibility == PostPoll.RESULTS_ALWAYS:
        return True
    if poll.results_visibility == PostPoll.RESULTS_AFTER_VOTE:
        return bool(voted)
    return poll.is_expired  # after_end


def answers_revealed_for(poll, user, voted):
    """Quiz verdict (correct options + explanation): the creator, whoever has answered, or everyone once it ended.
    Independent of `results_visibility` - instant feedback is the point of a quiz."""
    return poll.is_quiz and (is_owner(poll, user) or bool(voted) or poll.is_expired)


def _load_my_votes(poll, user):
    rows = list(PostPollVote.objects.filter(poll=poll, user=user).values_list("option_id", "is_correct"))
    poll._my_option_ids = [r[0] for r in rows]
    poll._my_is_correct = rows[0][1] if rows else None


def my_option_ids(poll, user):
    if not (user and getattr(user, "is_authenticated", False)):
        return []
    if getattr(poll, "_my_option_ids", None) is None:
        _load_my_votes(poll, user)
    return poll._my_option_ids


def my_vote_is_correct(poll, user):
    """Quiz verdict of the viewer's own answer (None = not answered / not a quiz)."""
    if not poll.is_quiz or not my_option_ids(poll, user):
        return None
    return poll._my_is_correct


def stamp_polls(posts, user):
    """Feed pages: ONE query for the options of every poll on the page and ONE for the viewer's votes (the card
    serializer would otherwise run both per poll). `posts` must already have `poll` prefetched."""
    from django.db.models import prefetch_related_objects

    found = []
    for p in posts:
        try:
            found.append(p.poll)
        except PostPoll.DoesNotExist:
            continue
    if not found:
        return
    prefetch_related_objects(found, "options", "post")
    mine, verdict = {}, {}
    if user is not None and getattr(user, "is_authenticated", False):
        for poll_id, option_id, ok in PostPollVote.objects.filter(
                user=user, poll_id__in=[p.pk for p in found]).values_list("poll_id", "option_id", "is_correct"):
            mine.setdefault(poll_id, []).append(option_id)
            verdict[poll_id] = ok
    for poll in found:
        poll._my_option_ids = mine.get(poll.pk, [])
        poll._my_is_correct = verdict.get(poll.pk)


# ---------------------------------------------------------------------------
# Voting (the double-vote guard)
# ---------------------------------------------------------------------------
def cast_vote(poll_id, user, option_ids):
    """Cast / change a vote. Returns the refreshed PostPoll.

    Guards, in order:
      * ended poll                          -> 400 poll_ended
      * the quiz creator answering own quiz -> 400 own_quiz
      * unknown / foreign option ids        -> 400 invalid_option
      * 0 or too many choices               -> 400 invalid_choice_count (single choice = exactly 1)
      * quiz already answered (different)   -> 409 already_answered   (a quiz answer is final)
      * the SAME selection sent again       -> 200 no-op (a double tap / retry never errors or double counts)
      * race between two requests           -> the poll row is locked (`select_for_update`) for the whole
                                               transaction, and `UniqueConstraint(poll, user, option)` is the
                                               hard backstop -> IntegrityError becomes 409 duplicate_vote
    A normal poll lets the voter CHANGE their vote: the old rows are replaced as one set.
    """
    with transaction.atomic():
        try:
            poll = PostPoll.objects.select_for_update().select_related("post").get(pk=poll_id)
        except PostPoll.DoesNotExist:
            raise PollError("no_poll", "This post has no poll.", 404)
        if poll.is_expired:
            raise PollError("poll_ended", "This poll has ended.", 400)
        if poll.is_quiz and poll.post.user_id == user.id:
            raise PollError("own_quiz", "You can't answer your own quiz.", 400)

        options = {o.id: o for o in poll.options.all()}
        ids = []
        for raw in option_ids:
            if raw not in options:
                raise PollError("invalid_option", "That option does not belong to this poll.", 400)
            if raw not in ids:
                ids.append(raw)
        if not ids or len(ids) > (poll.max_choices if poll.allow_multiple else 1):
            limit = poll.max_choices if poll.allow_multiple else 1
            raise PollError(
                "invalid_choice_count",
                f"Pick exactly 1 option." if limit == 1 else f"Pick between 1 and {limit} options.",
                400,
            )

        existing = list(PostPollVote.objects.filter(poll=poll, user=user))
        existing_ids = {v.option_id for v in existing}
        if existing_ids == set(ids):
            poll._my_option_ids = list(existing_ids)
            poll._my_is_correct = existing[0].is_correct if existing else None
            return poll  # idempotent repeat
        if existing and poll.is_quiz:
            raise PollError("already_answered", "You have already answered this quiz.", 409)

        is_correct = None
        if poll.is_quiz:
            is_correct = set(ids) == {oid for oid, o in options.items() if o.is_correct}
        try:
            with transaction.atomic():  # savepoint: an IntegrityError must not poison the outer transaction
                for vote in existing:
                    vote.delete()  # fires post_delete (counters); rows are few (<= 6)
                PostPollVote.objects.bulk_create([
                    PostPollVote(poll=poll, user=user, option_id=oid, is_correct=is_correct) for oid in ids
                ])
        except IntegrityError:
            raise PollError("duplicate_vote", "Your vote was already recorded.", 409)
        recount_poll_votes(poll.id)  # bulk_create sends no post_save
    poll = PostPoll.objects.select_related("post").prefetch_related("options").get(pk=poll_id)
    poll._my_option_ids = list(ids)
    poll._my_is_correct = is_correct
    return poll


# ---------------------------------------------------------------------------
# Creator results screen + CSV
# ---------------------------------------------------------------------------
def _pct(part, whole):
    return round(part * 100.0 / whole, 1) if whole else 0.0


def _display_name(user):
    full = ""
    try:
        full = (user.get_full_name() or "").strip()
    except Exception:
        pass
    return full or user.username


def _voter_payload(user, request):
    from .serializers import get_profile_pic_url

    return {
        "id": str(user.id),
        "username": user.username,
        "name": _display_name(user),
        "profile_picture": get_profile_pic_url(user, request),
    }


def option_image_url(option, request):
    if not option.image:
        return None
    try:
        url = option.image.url
    except ValueError:
        return None
    return request.build_absolute_uri(url) if request is not None else url


def build_results(poll, request=None, limit=50, offset=0):
    """What the creator sees: per-option counts / %, and (public polls only) who picked what, paginated by voter."""
    options = list(poll.options.all())
    total = poll.total_votes_count
    out = {
        "poll_id": str(poll.id),
        "post_id": str(poll.post_id),
        "is_anonymous": poll.is_anonymous,
        "is_quiz": poll.is_quiz,
        "allow_multiple": poll.allow_multiple,
        "is_expired": poll.is_expired,
        "expires_at": poll.expires_at,
        "total_voters": total,
        "explanation": poll.explanation if poll.is_quiz else "",
        "options": [
            {
                "id": str(o.id),
                "text": o.text,
                "image": option_image_url(o, request),
                "is_correct": o.is_correct if poll.is_quiz else None,
                "votes_count": o.votes_count,
                "percent": _pct(o.votes_count, total),
            }
            for o in options
        ],
        "voters_visible": not poll.is_anonymous,
        "voters": None,
        "voters_total": total,
        "limit": limit,
        "offset": offset,
    }
    if poll.is_quiz:
        correct_voters = (
            PostPollVote.objects.filter(poll=poll, is_correct=True).values("user_id").distinct().count())
        out["correct_voters"] = correct_voters
        out["accuracy_percent"] = _pct(correct_voters, total)
    if poll.is_anonymous:
        return out

    page = list(
        PostPollVote.objects.filter(poll=poll).values("user_id")
        .annotate(first=Min("created_at")).order_by("first", "user_id")[offset:offset + limit]
    )
    user_ids = [r["user_id"] for r in page]
    rows = PostPollVote.objects.filter(poll=poll, user_id__in=user_ids).select_related("user", "option")
    by_user = {}
    for v in rows:
        by_user.setdefault(v.user_id, []).append(v)
    option_order = {o.id: i for i, o in enumerate(options)}
    voters = []
    for r in page:
        votes = sorted(by_user.get(r["user_id"], []), key=lambda v: option_order.get(v.option_id, 0))
        if not votes:
            continue
        voters.append({
            "user": _voter_payload(votes[0].user, request),
            "option_ids": [str(v.option_id) for v in votes],
            "option_texts": [v.option.text or "(image)" for v in votes],
            "is_correct": votes[0].is_correct if poll.is_quiz else None,
            "voted_at": min(v.created_at for v in votes),
        })
    out["voters"] = voters
    return out


def _csv_safe(value):
    """Spreadsheet formula injection guard: a cell that starts with = + - @ is read as a formula by Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_rows(poll, summary=False):
    """`(header, rows)` for the CSV. Anonymous polls (and `summary=True`) export per-option totals only."""
    options = list(poll.options.all())
    total = poll.total_votes_count
    if poll.is_anonymous or summary:
        header = ["option", "votes", "percent_of_voters"] + (["is_correct"] if poll.is_quiz else [])
        rows = []
        for o in options:
            row = [o.text or "(image)", o.votes_count, _pct(o.votes_count, total)]
            if poll.is_quiz:
                row.append("yes" if o.is_correct else "no")
            rows.append(row)
        rows.append(["TOTAL VOTERS", total, ""] + ([""] if poll.is_quiz else []))
        return header, rows

    header = ["username", "name", "selected_options"] + (["is_correct"] if poll.is_quiz else []) + ["voted_at"]
    votes = PostPollVote.objects.filter(poll=poll).select_related("user", "option").order_by("created_at", "id")
    option_order = {o.id: i for i, o in enumerate(options)}
    grouped = {}
    for v in votes:
        grouped.setdefault(v.user_id, []).append(v)
    rows = []
    for user_votes in grouped.values():
        user_votes.sort(key=lambda v: option_order.get(v.option_id, 0))
        first = user_votes[0]
        row = [first.user.username, _display_name(first.user), " | ".join(v.option.text or "(image)" for v in user_votes)]
        if poll.is_quiz:
            row.append("yes" if first.is_correct else "no")
        row.append(min(v.created_at for v in user_votes).isoformat())
        rows.append(row)
    rows.sort(key=lambda r: r[-1])
    return header, rows


def build_csv(poll, summary=False):
    """UTF-8 CSV text with a BOM so Excel shows Hindi option text correctly."""
    header, rows = export_rows(poll, summary=summary)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_csv_safe(c) for c in row])
    return "\ufeff" + buf.getvalue()


# ---------------------------------------------------------------------------
# Quiz leaderboard / score
# ---------------------------------------------------------------------------
def build_leaderboard(poll, user, request=None, size=LEADERBOARD_SIZE):
    """Correct answerers ranked by who got it right FIRST. Anonymous quizzes show everyone else as "Anonymous"
    (the viewer still sees their own row). Raises PollError unless it's a quiz and the viewer may see it."""
    if not poll.is_quiz:
        raise PollError("not_a_quiz", "This poll is not a quiz.", 400)
    voted = bool(my_option_ids(poll, user))
    if not (is_owner(poll, user) or voted or poll.is_expired):
        raise PollError("answer_first", "Answer the quiz to see the leaderboard.", 403)

    ranked = list(
        PostPollVote.objects.filter(poll=poll, is_correct=True).values("user_id")
        .annotate(answered_at=Min("created_at")).order_by("answered_at", "user_id")
    )
    wanted_ids = [r["user_id"] for r in ranked[:size]]
    users = {u.id: u for u in get_user_model().objects.filter(id__in=wanted_ids)}
    me_id = user.id if user and user.is_authenticated else None
    rows, my_rank = [], None
    for rank, r in enumerate(ranked, start=1):
        if r["user_id"] == me_id:
            my_rank = rank
        if rank > size:
            continue
        mine = r["user_id"] == me_id
        hide = poll.is_anonymous and not mine
        rows.append({
            "rank": rank,
            "score": 1,
            "is_me": mine,
            "user": None if hide else _voter_payload(users[r["user_id"]], request),
            "label": "Anonymous" if hide else None,
            "answered_at": r["answered_at"],
        })
    my_votes = my_option_ids(poll, user)
    total = poll.total_votes_count
    return {
        "poll_id": str(poll.id),
        "total_voters": total,
        "correct_voters": len(ranked),
        "accuracy_percent": _pct(len(ranked), total),
        "rows": rows,
        "me": {
            "answered": bool(my_votes),
            "is_correct": my_rank is not None if my_votes else None,
            "rank": my_rank,
            "score": 1 if my_rank is not None else 0,
        },
    }


def my_quiz_score(user):
    """Across every quiz poll the user answered: `{attempted, correct, accuracy_percent}`."""
    per_poll = {}
    for poll_id, is_correct in PostPollVote.objects.filter(user=user, poll__is_quiz=True).values_list(
            "poll_id", "is_correct"):
        per_poll[poll_id] = bool(is_correct)  # every row of one answer carries the same verdict
    attempted = len(per_poll)
    correct = sum(1 for ok in per_poll.values() if ok)
    return {"attempted": attempted, "correct": correct, "accuracy_percent": _pct(correct, attempted)}


# ---------------------------------------------------------------------------
# Poll -> Test Series question
# ---------------------------------------------------------------------------
# Test Series question text is PLAIN text (its screens do not render the post composer's markup), so a post caption
# like "**What** is the SI unit of force? #physics @asha" must not travel into a question verbatim.
_QT_FENCE = re.compile(r"```[^\n`]*\n?|```")
_QT_BULLET = re.compile(r"(?m)^([ \t]*)[*\u2022][ \t]+")
_QT_EMPHASIS = re.compile(r"(\*{1,3})(?=\S)(.+?)(?<=\S)\1")
_QT_TAG = r"#[\w\u0900-\u097F]{1,50}"
_QT_MENTION = r"@[A-Za-z0-9_.]{1,30}"
_QT_TAG_ONLY_LINE = re.compile(r"(?m)^[ \t]*(?:(?:%s|%s)[ \t]*)+$" % (_QT_TAG, _QT_MENTION))
_QT_HASHTAG = re.compile(r"(?<![\w#])#([\w\u0900-\u097F]{1,50})")
_QT_AT_MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9_.]{1,30})")
_QT_BLANKS = re.compile(r"\n{3,}")
QUESTION_OPTION_IMAGE_MAX = 500  # testseries Question._validate_option_items: longer URLs fail the whole question


def plain_question_text(markup):
    """The post composer's rich markup as plain text for a Test Series question (pure, no DB, idempotent).

    * **bold** / *italic* / ***both*** / `code` / ``` fences lose their markers, the words stay;
    * "* item" and "\u2022 item" bullets become "- item";
    * a line that only holds #hashtags / @mentions is dropped; an inline #tag / @name keeps the word, loses the sign
      (so "C#" and "a@b.com" are untouched);
    * $maths$, \\ce{...}, links and line breaks stay as they are (the question screens show them as typed).
    """
    s = str(markup or "").replace("\r\n", "\n").replace("\r", "\n")
    s = _QT_FENCE.sub("", s)
    s = _QT_BULLET.sub(lambda m: m.group(1) + "- ", s)
    for _ in range(2):  # "***x***" first pass eats the outer pair, a nested "**a *b* c**" needs the second
        s = _QT_EMPHASIS.sub(lambda m: m.group(2), s)
    s = s.replace("`", "")
    s = _QT_TAG_ONLY_LINE.sub("", s)
    s = _QT_HASHTAG.sub(lambda m: m.group(1), s)
    s = _QT_AT_MENTION.sub(lambda m: m.group(1), s)
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    return _QT_BLANKS.sub("\n\n", s).strip()


def _question_option_image(url):
    """`url` if the Test Series accepts it as an option image (http(s) / site path, <= 500 chars), else None."""
    if not url or not isinstance(url, str) or len(url) > QUESTION_OPTION_IMAGE_MAX:
        return None
    return url if url.startswith(("http://", "https://", "/")) else None


def convert_to_question(poll, user, series_id, *, request=None, marks=1, negative_marks=0, correct_option_ids=None,
                        question_text="", topic="", difficulty=""):
    """Turn the poll into a Question appended to one of the creator's DRAFT series. Returns
    `{"question_id", "series_id", "order", "question_type"}`.

    * the question text is the post's own text as PLAIN text (`plain_question_text`: no **bold** / #tag / @mention
      markup), or `question_text` if given (taken as typed);
    * an option image the Test Series cannot take (signed URL > 500 chars) is dropped when the option also has text;
      an image-only option like that fails the convert with `option_image_unusable` (400);
    * quiz polls bring their correct option(s) + explanation; for a normal poll the creator names the right
      one(s) in `correct_option_ids`;
    * one correct option -> `mcq`, several -> `msq`;
    * the same poll can be added to a given series only once (409).
    """
    from testseries.bridge import (  # lazy: `post` never imports testseries at module load
        SeriesLockedError, SeriesNotEditableError, SeriesNotFoundError, append_question_to_series,
    )

    if not is_owner(poll, user):
        raise PollError("not_owner", "Only the poll's creator can convert it.", 403)
    if PostPollQuestionLink.objects.filter(poll=poll, series_id=series_id).exists():
        raise PollError("already_converted", "This poll is already a question in that test series.", 409)

    text = (question_text or "").strip() or plain_question_text(poll.post.content) or plain_question_text(poll.post.title)
    if not text:
        raise PollError("no_question_text", "Add the question text - this poll's post has none.", 400)

    options = list(poll.options.all())
    if poll.is_quiz:
        correct = [o for o in options if o.is_correct]
    else:
        wanted = {str(x) for x in (correct_option_ids or [])}
        correct = [o for o in options if str(o.id) in wanted]
        if not correct or len(correct) != len(wanted):
            raise PollError(
                "correct_option_required", "Tell us which option(s) are correct (correct_option_ids).", 400)
    if len(correct) == len(options):
        raise PollError("all_correct", "At least one option must be wrong.", 400)

    q_options, ids = [], {}
    for i, o in enumerate(options, start=1):
        oid = f"o{i}"
        ids[o.id] = oid
        item = {"id": oid, "text": o.text}
        raw_img = option_image_url(o, request)
        img = _question_option_image(raw_img)
        if img:
            item["image"] = img
        elif raw_img and not (o.text or "").strip():
            # nothing left of this option: refuse with a clear reason instead of a generic validation failure
            raise PollError(
                "option_image_unusable",
                "An image-only option has an image link the test series cannot store. Add text to that option and try again.",
                400)
        q_options.append(item)
    multi = len(correct) > 1
    payload = {
        "question_type": "msq" if multi else "mcq",
        "text": text,
        "marks": marks,
        "negative_marks": negative_marks,
        "options": q_options,
        "correct_answer": ({"option_ids": [ids[o.id] for o in correct]} if multi
                           else {"option_id": ids[correct[0].id]}),
        "explanation": poll.explanation if poll.is_quiz else "",
        "topic": (topic or "")[:80],
        "difficulty": difficulty if difficulty in ("easy", "medium", "hard") else "",
    }
    try:
        with transaction.atomic():
            question = append_question_to_series(series_id=series_id, user=user, question=payload)
            PostPollQuestionLink.objects.create(
                poll=poll, series_id=question.series_id, question_id=question.id, created_by=user)
    except SeriesNotFoundError:
        raise PollError("series_not_found", "Test series not found.", 404)
    except SeriesNotEditableError:
        raise PollError("series_forbidden", "You can only add questions to your own test series.", 403)
    except SeriesLockedError:
        raise PollError("series_locked", "Questions can only be added while the series is a draft.", 400)
    except IntegrityError:
        raise PollError("already_converted", "This poll is already a question in that test series.", 409)
    except DjangoValidationError as exc:
        raise PollError("invalid_question", " ".join(exc.messages) or "Invalid question.", 400)
    return {
        "question_id": str(question.id),
        "series_id": str(question.series_id),
        "order": question.order,
        "question_type": question.question_type,
    }


def parse_json_dict(value):
    """Multipart sends `poll_settings` as a JSON string; a JSON body sends a real object."""
    if isinstance(value, (dict, type(None))):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        return parsed
    return value
