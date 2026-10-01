from django.apps import AppConfig


class LeaderboardConfig(AppConfig):
    """
    TASK G7 (growth_and_feature_tasks.md — Leaderboards).

    Sits *above* testseries/campus/post the same way `core` sits above
    tuitionclass/message: those three apps never import each other or resolve
    each other's opaque cross-app refs (see testseries.models.TestSeries's
    "golden rule" docstring on `context_type`/`context_id`), but a
    cross-cutting aggregator is allowed to read all three read-only. Every
    import of testseries/campus/post models below is lazy (inside the
    function that needs it, not at module load time) so this app never
    forces a load-order dependency on any of them — same convention
    `core/tasks.py` documents at its own top.
    """

    name = "leaderboard"
    verbose_name = "Leaderboards"
