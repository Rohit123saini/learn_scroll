"""P-F7 -- tests of the unified search.

Run:  python manage.py test core.tests_unified_search

  * UnifiedQueryParsingTests   pure functions (no DB)
  * UnifiedSearchApiTests      GET /core/search/unified/   (people / posts / classes / tests / doubts / all)
  * UnifiedSearchPostgresTests pg_trgm typo tolerance (skipped on SQLite)
  * SearchTrendingTests        GET /core/search/trending/

Search is a visibility surface, so most tests here are "must NOT be found" tests (blocked, muted, private,
deleted, hidden, flagged, draft).
"""
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core import unified_search as us
from post.models import Hashtag, MutedAccount, Post, PostDoubt
from testseries.models import TestSeries
from tuitionclass.models import Classroom
from user_profile.models import BlockUser, Follow, PrivacySettings

User = get_user_model()

SEARCH_URL = "/core/search/unified/"
TRENDING_URL = "/core/search/trending/"


def mk_user(name, **kw):
    return User.objects.create_user(username=name, password="pw12345!", **kw)


def follow(a, b):
    Follow.objects.create(follower=a, following=b, status=Follow.Status.ACCEPTED)


def mk_post(user, title="", content="", **kw):
    return Post.objects.create(user=user, title=title, content=content, **kw)


def mk_doubt(user, content, subject="Physics", solved=False, urgent=False):
    post = Post.objects.create(user=user, content=content, kind="doubt")
    PostDoubt.objects.create(post=post, subject=subject, is_urgent=urgent,
                             solved_at=timezone.now() if solved else None)
    return post


def mk_class(teacher, title, **kw):
    kw.setdefault("subject", "Physics")
    return Classroom.objects.create(teacher=teacher, title=title, **kw)


def mk_series(creator, title, **kw):
    base = dict(source=TestSeries.Source.INDIVIDUAL, status=TestSeries.Status.PUBLISHED)
    base.update(kw)
    return TestSeries.objects.create(creator=creator, title=title, **base)


# ---------------------------------------------------------------------------------------------- pure functions
class UnifiedQueryParsingTests(SimpleTestCase):
    def test_normalize_strips_control_chars_collapses_space_and_caps_length(self):
        self.assertEqual(us.normalize_query("  hello \x00 \t  world\x07 "), "hello world")
        self.assertEqual(len(us.normalize_query("a" * 500)), us.MAX_QUERY_CHARS)
        self.assertEqual(us.normalize_query(None), "")

    def test_hash_query_is_a_tag(self):
        p = us.parse_query("#Physics")
        self.assertEqual(p.tag, "physics")
        self.assertEqual(p.term, "Physics")
        self.assertFalse(p.handle)

    def test_hash_with_spaces_is_not_a_tag(self):
        self.assertEqual(us.parse_query("#two words").tag, "")

    def test_at_query_is_a_handle(self):
        p = us.parse_query("@rahul")
        self.assertTrue(p.handle)
        self.assertEqual(p.term, "rahul")

    def test_tokens_are_capped(self):
        p = us.parse_query("a b c d e f g h")
        self.assertEqual(len(p.tokens), us.MAX_TOKENS)

    def test_usable_needs_min_chars_after_the_prefix(self):
        self.assertFalse(us.parse_query("a").usable)
        self.assertFalse(us.parse_query("#a").usable)
        self.assertFalse(us.parse_query("@").usable)
        self.assertTrue(us.parse_query("ab").usable)
        self.assertTrue(us.parse_query("#ab").usable)

    def test_clamp_limit_and_offset(self):
        self.assertEqual(us.clamp_limit(None), us.DEFAULT_LIMIT)
        self.assertEqual(us.clamp_limit("abc"), us.DEFAULT_LIMIT)
        self.assertEqual(us.clamp_limit(0), 1)
        self.assertEqual(us.clamp_limit(10_000), us.MAX_LIMIT)
        self.assertEqual(us.clamp_offset(-5), 0)
        self.assertEqual(us.clamp_offset("x"), 0)
        self.assertEqual(us.clamp_offset(10_000), us.MAX_OFFSET)

    def test_clean_filters_keeps_only_the_tabs_own_filters(self):
        params = {"kind": "reel", "price": "free", "sort": "recent", "junk": "1"}
        self.assertEqual(us.clean_filters("posts", params), {"kind": "reel", "sort": "recent"})
        self.assertEqual(us.clean_filters("tests", params), {"price": "free"})
        self.assertEqual(us.clean_filters("people", params), {})

    def test_throttle_scopes_are_configured(self):
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertIn("search_unified", rates)
        self.assertIn("search_trending", rates)


# ---------------------------------------------------------------------------------------------- HTTP
class _Base(TestCase):
    def setUp(self):
        cache.clear()  # throttle counters + trending cache
        self.me = mk_user("zz_me")
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def search(self, q, tab="all", **extra):
        return self.client.get(SEARCH_URL, {"q": q, "tab": tab, **extra})

    def ids(self, q, tab, **extra):
        res = self.search(q, tab, **extra)
        self.assertEqual(res.status_code, 200, res.data)
        return [i["id"] for i in res.data["items"]]

    def titles(self, q, tab, **extra):
        res = self.search(q, tab, **extra)
        self.assertEqual(res.status_code, 200, res.data)
        return [i["title"] for i in res.data["items"]]


class UnifiedSearchApiTests(_Base):
    # ----- contract
    def test_login_required(self):
        res = APIClient().get(SEARCH_URL, {"q": "physics"})
        self.assertIn(res.status_code, (401, 403))

    def test_short_query_is_400_with_code(self):
        res = self.search("a")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "query_too_short")
        self.assertEqual(res.data["min_chars"], us.MIN_QUERY_CHARS)

    def test_missing_query_is_400(self):
        self.assertEqual(self.client.get(SEARCH_URL).status_code, 400)

    def test_unknown_tab_is_400(self):
        res = self.search("physics", tab="nope")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "bad_tab")

    def test_no_match_is_200_with_empty_items(self):
        res = self.search("qqqzzzxxx", "posts")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["items"], [])
        self.assertFalse(res.data["has_more"])

    def test_filters_of_another_tab_are_ignored_not_an_error(self):
        mk_series(mk_user("zz_t1"), "Algebra drill", is_paid=False)
        res = self.search("algebra", "tests", kind="reel", sort="popular", price="free")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["items"]), 1)

    # ----- people
    def test_people_by_username_and_name(self):
        mk_user("zz_rahul", first_name="Rahul", last_name="Verma")
        self.assertEqual(self.titles("zz_rahul", "people"), ["zz_rahul"])
        self.assertEqual(self.titles("verma", "people"), ["zz_rahul"])

    def test_people_never_include_self(self):
        self.assertNotIn(str(self.me.id), self.ids("zz_me", "people"))

    def test_people_blocked_either_way_are_hidden(self):
        a, b = mk_user("zz_blocked_by_me"), mk_user("zz_blocked_me")
        BlockUser.objects.create(blocker=self.me, blocked=a)
        BlockUser.objects.create(blocker=b, blocked=self.me)
        found = self.titles("zz_block", "people")
        self.assertNotIn("zz_blocked_by_me", found)
        self.assertNotIn("zz_blocked_me", found)

    def test_people_hide_from_search_is_respected_unless_connected(self):
        hidden, friend = mk_user("zz_hidden"), mk_user("zz_hidden_friend")
        for u in (hidden, friend):
            PrivacySettings.objects.update_or_create(user=u, defaults={"hide_from_search": True})
        follow(self.me, friend)
        found = self.titles("zz_hidden", "people")
        self.assertNotIn("zz_hidden", found)
        self.assertIn("zz_hidden_friend", found)

    def test_people_connections_filter_and_relation(self):
        friend, other = mk_user("zz_pal_friend"), mk_user("zz_pal_other")
        follow(self.me, friend)
        res = self.search("zz_pal", "people", relation="connections")
        self.assertEqual([i["title"] for i in res.data["items"]], ["zz_pal_friend"])
        self.assertEqual(res.data["items"][0]["meta"]["relation"], "following")
        res = self.search("zz_pal", "people")
        by_name = {i["title"]: i for i in res.data["items"]}
        self.assertEqual(by_name["zz_pal_other"]["meta"]["relation"], "none")
        # connections rank first among equally good matches
        self.assertEqual(res.data["items"][0]["title"], "zz_pal_friend")

    def test_at_handle_searches_people_only(self):
        mk_user("zz_handle")
        mk_post(mk_user("zz_author"), title="zz_handle in a title")
        res = self.search("@zz_handle", "all")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.data["sections"].keys()), {"people"})
        self.assertEqual([i["title"] for i in res.data["sections"]["people"]["items"]], ["zz_handle"])

    # ----- posts
    def test_post_found_by_title_and_content_exact_ranks_first(self):
        author = mk_user("zz_author")
        mk_post(author, title="Newton laws explained", content="mechanics")
        mk_post(author, title="Mechanics", content="a long text that mentions newton once")
        titles = self.titles("newton", "posts")
        self.assertEqual(titles[0], "Newton laws explained")  # title prefix beats a content match
        self.assertEqual(len(titles), 2)

    def test_post_hidden_when_deleted_archived_or_scheduled(self):
        a = mk_user("zz_author")
        mk_post(a, title="zzvis visible")
        mk_post(a, title="zzvis deleted", is_deleted=True)
        mk_post(a, title="zzvis archived", is_archived=True)
        mk_post(a, title="zzvis scheduled", is_scheduled=True)
        self.assertEqual(self.titles("zzvis", "posts"), ["zzvis visible"])

    def test_post_hidden_for_blocked_and_muted_authors(self):
        blocked, muted, ok = mk_user("zz_b"), mk_user("zz_m"), mk_user("zz_ok")
        BlockUser.objects.create(blocker=self.me, blocked=blocked)
        MutedAccount.objects.create(user=self.me, muted_user=muted, mute_posts=True)
        for u in (blocked, muted, ok):
            mk_post(u, title=f"zzfilter by {u.username}")
        self.assertEqual(self.titles("zzfilter", "posts"), ["zzfilter by zz_ok"])

    def test_private_account_posts_only_for_followers(self):
        priv = mk_user("zz_priv", is_private=True)
        mk_post(priv, title="zzsecret notes")
        self.assertEqual(self.titles("zzsecret", "posts"), [])
        follow(self.me, priv)
        self.assertEqual(self.titles("zzsecret", "posts"), ["zzsecret notes"])

    def test_own_post_is_found_even_on_a_private_account(self):
        me = User.objects.get(pk=self.me.pk)
        me.is_private = True
        me.save(update_fields=["is_private"])
        mk_post(me, title="zzmine private")
        self.assertEqual(self.titles("zzmine", "posts"), ["zzmine private"])

    def test_doubts_are_not_in_the_posts_tab(self):
        mk_doubt(mk_user("zz_asker"), "zzdoubtword how does it work")
        self.assertEqual(self.ids("zzdoubtword", "posts"), [])
        self.assertEqual(len(self.ids("zzdoubtword", "doubts")), 1)

    def test_posts_kind_period_and_sort_filters(self):
        a = mk_user("zz_author")
        old = mk_post(a, title="zzf old", kind="reel", likes_count=50)
        Post.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))
        mk_post(a, title="zzf new", kind="post", likes_count=1)
        mk_post(a, title="zzf newer", kind="post", likes_count=9)
        self.assertEqual(set(self.titles("zzf", "posts", kind="reel")), {"zzf old"})
        self.assertNotIn("zzf old", self.titles("zzf", "posts", period="month"))
        self.assertEqual(self.titles("zzf", "posts", sort="popular")[0], "zzf old")
        self.assertEqual(self.titles("zzf", "posts", sort="recent")[0], "zzf newer")

    def test_hashtag_search(self):
        a = mk_user("zz_author")
        mk_post(a, title="Tagged", hashtags=["physics", "jee"])
        mk_post(a, title="Untagged but physics in title")
        self.assertEqual(self.titles("#physics", "posts"), ["Tagged"])
        self.assertEqual(self.titles("#PHYSICS", "posts"), ["Tagged"])  # case-insensitive

    # ----- doubts
    def test_doubt_status_and_urgent_filters(self):
        a = mk_user("zz_asker")
        mk_doubt(a, "zzd solved one", solved=True)
        mk_doubt(a, "zzd open one", solved=False)
        mk_doubt(a, "zzd urgent open", urgent=True)
        self.assertEqual(self.titles("zzd", "doubts", status="solved"), ["zzd solved one"])
        self.assertEqual(set(self.titles("zzd", "doubts", status="open")), {"zzd open one", "zzd urgent open"})
        self.assertEqual(self.titles("zzd", "doubts", urgent="1"), ["zzd urgent open"])

    def test_solved_doubt_ranks_before_open_among_equal_matches(self):
        a = mk_user("zz_asker")
        mk_doubt(a, "zzrank what is entropy", solved=False)
        mk_doubt(a, "zzrank what is entropy", solved=True)
        items = self.search("zzrank", "doubts").data["items"]
        self.assertTrue(items[0]["meta"]["is_solved"])

    def test_doubts_of_blocked_users_are_hidden(self):
        b = mk_user("zz_bad_asker")
        BlockUser.objects.create(blocker=self.me, blocked=b)
        mk_doubt(b, "zzblockeddoubt text")
        self.assertEqual(self.ids("zzblockeddoubt", "doubts"), [])

    # ----- classes
    def test_class_visibility_rules(self):
        t = mk_user("zz_teacher")
        mk_class(t, "zzcls public")
        mk_class(t, "zzcls flagged", is_flagged=True)
        mk_class(t, "zzcls deleted", is_deleted=True)
        mk_class(t, "zzcls personal", is_personal=True)
        mk_class(t, "zzcls inactive", is_active=False)
        self.assertEqual(self.titles("zzcls", "classes"), ["zzcls public"])

    def test_teacher_still_finds_own_inactive_class(self):
        mk_class(self.me, "zzown inactive", is_active=False)
        self.assertEqual(self.titles("zzown", "classes"), ["zzown inactive"])

    def test_classes_of_blocked_teacher_are_hidden(self):
        t = mk_user("zz_blocked_teacher")
        BlockUser.objects.create(blocker=t, blocked=self.me)
        mk_class(t, "zzblockedcls")
        self.assertEqual(self.titles("zzblockedcls", "classes"), [])

    def test_class_min_rating_filter_and_teacher_name_match(self):
        t = mk_user("zz_prof_sharma", first_name="Sharma")
        mk_class(t, "zzrate low", rating_avg=3.2)
        mk_class(t, "zzrate high", rating_avg=4.7)
        self.assertEqual(self.titles("zzrate", "classes", min_rating="4.5"), ["zzrate high"])
        self.assertEqual(len(self.titles("zzrate", "classes", min_rating="garbage")), 2)  # bad value = ignored
        self.assertEqual(len(self.titles("zz_prof_sharma", "classes")), 2)  # matches via the teacher's username

    # ----- tests (test series)
    def test_test_series_scope_and_price_filter(self):
        other = mk_user("zz_creator")
        mk_series(other, "zzts free", is_paid=False)
        mk_series(other, "zzts paid", is_paid=True, price_coins=50)
        mk_series(other, "zzts draft", status=TestSeries.Status.DRAFT)  # someone else's draft: invisible
        mk_series(self.me, "zzts my draft", status=TestSeries.Status.DRAFT)  # my own draft: visible
        self.assertEqual(set(self.titles("zzts", "tests")), {"zzts free", "zzts paid", "zzts my draft"})
        # my own draft has the model default is_paid=False, so it is "free" too (someone else's draft stays hidden)
        self.assertEqual(set(self.titles("zzts", "tests", price="free")), {"zzts free", "zzts my draft"})
        self.assertEqual(self.titles("zzts", "tests", price="paid"), ["zzts paid"])

    # ----- all tab
    def test_all_tab_returns_a_few_rows_per_section(self):
        author = mk_user("zz_all_author")
        for i in range(us.ALL_TAB_PER_SECTION + 3):
            mk_post(author, title=f"zzall post {i}")
        res = self.search("zzall", "all")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.data["sections"]), set(us.SECTION_TABS))
        posts = res.data["sections"]["posts"]
        self.assertEqual(len(posts["items"]), us.ALL_TAB_PER_SECTION)
        self.assertTrue(posts["has_more"])
        self.assertEqual(res.data["errors"], [])

    def test_all_tab_one_broken_section_does_not_break_the_rest(self):
        mk_user("zz_survivor")
        boom = mock.Mock(side_effect=RuntimeError("db exploded"))
        with mock.patch.dict(us.SEARCHERS, {"posts": boom}):
            res = self.search("zz_survivor", "all")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["errors"], ["posts"])
        self.assertEqual(len(res.data["sections"]["people"]["items"]), 1)
        self.assertEqual(res.data["sections"]["posts"]["items"], [])
        self.assertNotIn("did_you_mean", res.data)  # an error is not "nothing matched"

    # ----- paging
    def test_pagination_has_more_next_offset_and_no_duplicates(self):
        a = mk_user("zz_pager")
        for i in range(5):
            mk_post(a, title=f"zzpage {i}")
        first = self.search("zzpage", "posts", limit=2).data
        self.assertEqual(len(first["items"]), 2)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 2)
        second = self.search("zzpage", "posts", limit=2, offset=first["next_offset"]).data
        third = self.search("zzpage", "posts", limit=2, offset=second["next_offset"]).data
        self.assertEqual(len(third["items"]), 1)
        self.assertFalse(third["has_more"])
        self.assertIsNone(third["next_offset"])
        ids = [i["id"] for page in (first, second, third) for i in page["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 5)

    def test_limit_and_offset_are_clamped(self):
        mk_post(mk_user("zz_pager"), title="zzclamp one")
        res = self.search("zzclamp", "posts", limit=100000, offset=999999)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["items"], [])

    # ----- item shape the Flutter models rely on
    def test_item_shape(self):
        mk_post(mk_user("zz_shape"), title="zzshape title", content="body text")
        item = self.search("zzshape", "posts").data["items"][0]
        for key in ("type", "id", "title", "subtitle", "snippet", "image", "created_at", "score", "meta"):
            self.assertIn(key, item)
        self.assertEqual(item["type"], "post")
        self.assertTrue(0.0 <= item["score"] <= 1.0)
        self.assertIsInstance(item["meta"], dict)


@mock.patch.object(us, "_is_postgres", return_value=False)
class UnifiedSearchSqliteFallbackTests(_Base):
    def test_typo_does_not_match_without_pg_trgm_and_does_not_crash(self, _):
        mk_post(mk_user("zz_author"), title="Thermodynamics")
        res = self.search("thermodinamics", "posts")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["items"], [])
        self.assertFalse(res.data["fuzzy"])
        self.assertIsNone(res.data.get("did_you_mean"))


class UnifiedSearchPostgresTests(_Base):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("pg_trgm typo tolerance needs PostgreSQL")
        super().setUp()

    def test_typo_in_title_is_found_and_flagged_fuzzy(self):
        mk_post(mk_user("zz_author"), title="Thermodynamics")
        res = self.search("thermodinamics", "posts")
        self.assertEqual(res.status_code, 200)
        self.assertEqual([i["title"] for i in res.data["items"]], ["Thermodynamics"])
        self.assertTrue(res.data["fuzzy"])

    def test_typo_in_username_is_found(self):
        mk_user("zz_priyanka")
        self.assertIn("zz_priyanka", self.titles("zz_priyanaka", "people"))

    def test_exact_match_still_outranks_a_fuzzy_one(self):
        a = mk_user("zz_author")
        mk_post(a, title="Optics")
        mk_post(a, title="Optic")
        self.assertEqual(self.titles("optics", "posts")[0], "Optics")

    def test_did_you_mean_suggests_a_known_hashtag(self):
        Hashtag.objects.create(name="thermodynamics", posts_count=5)
        res = self.search("thermodinamix", "posts")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data.get("did_you_mean"), "thermodynamics")


# ---------------------------------------------------------------------------------------------- trending
class SearchTrendingTests(_Base):
    def test_login_required(self):
        self.assertIn(APIClient().get(TRENDING_URL).status_code, (401, 403))

    def test_shape_and_ordering(self):
        Hashtag.objects.create(name="jee", posts_count=10)
        Hashtag.objects.create(name="neet", posts_count=30)
        mk_class(mk_user("zz_t1"), "A", subject="Chemistry")
        mk_class(mk_user("zz_t2"), "B", subject="Chemistry")
        mk_class(mk_user("zz_t3"), "C", subject="Maths")
        res = self.client.get(TRENDING_URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual([h["tag"] for h in res.data["hashtags"]], ["neet", "jee"])
        self.assertEqual(res.data["subjects"][0]["subject"], "Chemistry")
        self.assertEqual(res.data["subjects"][0]["count"], 2)

    def test_stale_and_unused_hashtags_are_excluded(self):
        Hashtag.objects.create(name="fresh", posts_count=3)
        old = Hashtag.objects.create(name="stale", posts_count=99)
        Hashtag.objects.filter(pk=old.pk).update(last_used_at=timezone.now() - timedelta(days=30))
        Hashtag.objects.create(name="unused", posts_count=0)
        tags = [h["tag"] for h in self.client.get(TRENDING_URL).data["hashtags"]]
        self.assertEqual(tags, ["fresh"])

    def test_flagged_deleted_and_personal_classes_do_not_make_a_subject_trend(self):
        t = mk_user("zz_t")
        mk_class(t, "x", subject="Hidden1", is_flagged=True)
        mk_class(t, "y", subject="Hidden2", is_deleted=True)
        mk_class(t, "z", subject="Hidden3", is_personal=True)
        self.assertEqual(self.client.get(TRENDING_URL).data["subjects"], [])

    def test_result_is_cached_and_the_empty_case_is_valid(self):
        first = self.client.get(TRENDING_URL)
        self.assertEqual(first.data, {"hashtags": [], "subjects": []})
        Hashtag.objects.create(name="late", posts_count=5)
        self.assertEqual(self.client.get(TRENDING_URL).data["hashtags"], [])  # still the cached answer
        cache.clear()
        self.assertEqual([h["tag"] for h in self.client.get(TRENDING_URL).data["hashtags"]], ["late"])

    def test_a_failing_source_returns_an_empty_list_not_a_500(self):
        with mock.patch.object(us, "timedelta", side_effect=RuntimeError("boom")):  # used inside the hashtag try/except
            res = self.client.get(TRENDING_URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["hashtags"], [])
