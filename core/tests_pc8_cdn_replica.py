"""P-C8 -- tests: replica router rules, read-your-writes pin, fail-safe, API cache default, release_check rules.

    python manage.py test core.tests_pc8_cdn_replica
Replica DB is faked (no second database needed): the router only returns an alias, it never connects.
"""
from unittest import mock

from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from core import db_routing
from core.release_check import ERROR, WARN, check_env
from core.tests_release_check import GOOD

REPLICA = dict(DB_REPLICA_ENABLED=True, DB_REPLICA_PATH_PREFIXES=("/post/feed/",), DB_REPLICA_PIN_SECONDS=5,
               DB_REPLICA_DOWN_SECONDS=30, DB_REPLICA_EXCLUDE_APPS=("auth", "sessions", "login"))


class _M:  # fake model
    class _meta:
        app_label = "post"


class _Auth:
    class _meta:
        app_label = "login"


def _view_capturing(box):
    def view(request):
        box["alias"] = db_routing.ReplicaRouter().db_for_read(_M)
        return JsonResponse({"ok": True})
    return view


@override_settings(**REPLICA)
class RouterTests(SimpleTestCase):
    def setUp(self):
        db_routing.reset_replica_state()
        cache.clear()
        self.rf = RequestFactory()

    def _call(self, method, path, **meta):
        box = {}
        mw = db_routing.ReplicaReadMiddleware(_view_capturing(box))
        req = getattr(self.rf, method)(path, **meta)
        resp = mw(req)
        return box.get("alias"), resp

    def test_writes_and_migrations_stay_on_primary(self):
        r = db_routing.ReplicaRouter()
        self.assertIsNone(r.db_for_write(_M))
        self.assertTrue(r.allow_migrate("default", "post"))
        self.assertFalse(r.allow_migrate("replica", "post"))

    def test_outside_request_uses_primary(self):
        self.assertIsNone(db_routing.ReplicaRouter().db_for_read(_M))

    def test_whitelisted_get_uses_replica(self):
        self.assertEqual(self._call("get", "/post/feed/")[0], "replica")

    def test_other_paths_and_methods_stay_primary(self):
        self.assertIsNone(self._call("get", "/post/create/")[0])
        self.assertIsNone(self._call("post", "/post/feed/")[0])

    def test_excluded_app_stays_primary(self):
        box = {}

        def view(request):
            box["a"] = db_routing.ReplicaRouter().db_for_read(_Auth)
            return HttpResponse("x")
        db_routing.ReplicaReadMiddleware(view)(self.rf.get("/post/feed/"))
        self.assertIsNone(box["a"])

    def test_write_pins_client_to_primary_then_expires(self):
        auth = {"HTTP_AUTHORIZATION": "Bearer abc"}
        self.assertEqual(self._call("get", "/post/feed/", **auth)[0], "replica")
        self._call("post", "/post/like/", **auth)
        self.assertIsNone(self._call("get", "/post/feed/", **auth)[0])
        # another client is not pinned
        self.assertEqual(self._call("get", "/post/feed/", HTTP_AUTHORIZATION="Bearer other")[0], "replica")
        cache.clear()  # pin TTL elapsed
        self.assertEqual(self._call("get", "/post/feed/", **auth)[0], "replica")

    def test_cache_failure_fails_safe_to_primary(self):
        with mock.patch("django.core.cache.cache.get", side_effect=RuntimeError("redis down")):
            self.assertIsNone(self._call("get", "/post/feed/", HTTP_AUTHORIZATION="Bearer abc")[0])

    def test_replica_marked_down_goes_back_to_primary(self):
        mw = db_routing.ReplicaReadMiddleware(lambda r: HttpResponse("x"))
        from django.db import OperationalError
        req = self.rf.get("/post/feed/")
        mw(req)  # sets request._db_replica
        mw.process_exception(req, OperationalError("replica gone"))
        self.assertIsNone(self._call("get", "/post/feed/")[0])
        db_routing.reset_replica_state()
        self.assertEqual(self._call("get", "/post/feed/")[0], "replica")

    def test_flag_does_not_leak_after_request(self):
        self._call("get", "/post/feed/")
        self.assertIsNone(db_routing.ReplicaRouter().db_for_read(_M))

    def test_replica_reads_context_manager(self):
        with db_routing.replica_reads():
            self.assertEqual(db_routing.ReplicaRouter().db_for_read(_M), "replica")
        self.assertIsNone(db_routing.ReplicaRouter().db_for_read(_M))

    @override_settings(DB_REPLICA_ENABLED=False)
    def test_disabled_is_inert(self):
        self.assertIsNone(self._call("get", "/post/feed/")[0])


class ApiCacheHeaderTests(SimpleTestCase):
    def _run(self, path, resp):
        from common.api_cache_headers import ApiCacheSafetyMiddleware
        return ApiCacheSafetyMiddleware(lambda r: resp)(RequestFactory().get(path))

    def test_json_without_header_gets_private_no_store(self):
        r = self._run("/post/feed/", JsonResponse({}))
        self.assertEqual(r["Cache-Control"], "private, no-store")
        self.assertIn("Authorization", r["Vary"])

    def test_existing_header_never_overridden(self):
        resp = JsonResponse({})
        resp["Cache-Control"] = "private, no-cache"
        self.assertEqual(self._run("/post/feed/", resp)["Cache-Control"], "private, no-cache")

    def test_media_static_public_pages_and_5xx_untouched(self):
        for path in ("/media/a.jpg", "/static/x.css", "/pmedia/x", "/p/123/", "/sitemap.xml"):
            self.assertFalse(self._run(path, HttpResponse("x")).has_header("Cache-Control"), path)
        self.assertFalse(self._run("/post/feed/", HttpResponse("x", status=500)).has_header("Cache-Control"))

    @override_settings(API_CACHE_DEFAULT_PRIVATE=False)
    def test_toggle_off_removes_middleware(self):
        from django.core.exceptions import MiddlewareNotUsed
        from common.api_cache_headers import ApiCacheSafetyMiddleware
        with self.assertRaises(MiddlewareNotUsed):
            ApiCacheSafetyMiddleware(lambda r: None)


class ReleaseCheckReplicaTests(SimpleTestCase):
    def _levels(self, **extra):
        return {(x.key, x.level) for x in check_env({**GOOD, **extra})}

    def test_enabled_without_url_is_error(self):
        self.assertIn(("DB_REPLICA_URL", ERROR), self._levels(DB_REPLICA_ENABLED="true"))

    def test_enabled_good_url_no_prefixes_warns(self):
        lv = self._levels(DB_REPLICA_ENABLED="true", DB_REPLICA_URL="postgres://u:p@replica:5432/d")
        self.assertIn(("DB_REPLICA_PATH_PREFIXES", WARN), lv)
        self.assertNotIn(("DB_REPLICA_URL", ERROR), lv)

    def test_bad_numbers_error(self):
        self.assertIn(("DB_REPLICA_PIN_SECONDS", ERROR), self._levels(DB_REPLICA_PIN_SECONDS="abc"))

    def test_disabled_is_silent(self):
        self.assertFalse({k for k, _ in self._levels()} & {"DB_REPLICA_URL", "DB_REPLICA_PATH_PREFIXES"})
