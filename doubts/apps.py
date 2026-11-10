from django.apps import AppConfig


class DoubtsConfig(AppConfig):
    """
    T3.4 (D8) — the unified Doubts hub.

    The platform grew five independent "ask a doubt" systems, each with its
    own model, endpoint shape, status vocabulary and notification path:

        classroom_chat  message.DoubtQuestion       (group "Doubt Queue", upvotable)
        campus          campus.CampusDoubt (+Reply) (school subject-class, private by default)
        tuition_class   tuitionclass.ClassQuery     (live-class doubts, teacher answers)
        feed_post       post.Post(type=doubt)+PostAnswer (public "Ask a doubt" feed card)
        testseries      message.DoubtQuestion(context_type="testseries_attempt")

    This app is NOT a sixth system and owns NO tables. It is a thin
    aggregation + dispatch layer that sits *above* those apps — the same
    way `leaderboard` and `core` do — and gives clients ONE list, ONE
    detail shape, ONE ask endpoint and ONE set of verbs:

      * reads   -> per-source adapters query each app's own visibility-
                   scoped rows and normalise them to a single shape
                   (`hub.py`), merged newest-first with a cursor.
      * writes  -> dispatched in-process to the SOURCE app's own view
                   (`dispatch.py`), so every permission check, throttle,
                   notification and WS broadcast that already exists keeps
                   working and is never re-implemented here.

    Every cross-app import is lazy (inside functions) so this app never
    forces a load-order dependency — same convention as `leaderboard/apps.py`.
    """

    name = "doubts"
    verbose_name = "Doubts hub"
    default_auto_field = "django.db.models.BigAutoField"
