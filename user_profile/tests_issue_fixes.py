# user_profile/tests_issue_fixes.py
#
# Regression tests for the five audit issues:
#   1. admin manual top-up confirm      3. follow notifications
#   2. RestrictUser cross-app effects   4. follow-count signals
#   5. bounded pagination / bulk connections
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.pagination import StandardPagination
from core.models import Notification
from message.models import Conversation, Message, MessageStatus, UserPresence
from post.models import Post, PostComment

from .models import CoinPurchaseRequest, Follow, RestrictUser
from .serializers import accepted_connection_ids, bulk_accepted_connection_ids
from .services import has_restricted, restricted_ids_by, restrictor_ids_of

User = get_user_model()


def mk(name, **kw):
    return User.objects.create_user(username=name, password="pass12345", **kw)


# --------------------------------------------------------------------- #1
class AdminCoinPurchaseConfirmTests(APITestCase):
    def setUp(self):
        self.user = mk("buyer")
        self.staff = mk("ops", is_staff=True)
        self.url = reverse("buy-coin-admin-confirm")
        CoinPurchaseRequest.objects.start_purchase(
            user=self.user, gateway_reference="manual-1", amount="50.00", coins=50, gateway="",
        )

    def test_staff_confirms_blank_gateway_topup_and_wallet_is_credited(self):
        self.client.force_authenticate(self.staff)
        r = self.client.post(self.url, {"gateway_reference": "manual-1", "status": "success"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.coin, 50)

    def test_confirm_is_idempotent(self):
        self.client.force_authenticate(self.staff)
        for _ in range(2):
            self.client.post(self.url, {"gateway_reference": "manual-1", "status": "success"}, format="json")
        self.user.refresh_from_db()
        self.assertEqual(self.user.coin, 50)

    def test_regular_user_cannot_use_admin_endpoint(self):
        self.client.force_authenticate(self.user)
        r = self.client.post(self.url, {"gateway_reference": "manual-1", "status": "success"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.user.refresh_from_db()
        self.assertEqual(self.user.coin, 0)

    def test_gateway_backed_purchase_is_rejected_here(self):
        CoinPurchaseRequest.objects.start_purchase(
            user=self.user, gateway_reference="rzp-1", amount="10.00", coins=10, gateway="razorpay",
        )
        self.client.force_authenticate(self.staff)
        r = self.client.post(self.url, {"gateway_reference": "rzp-1", "status": "success"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)
        self.user.refresh_from_db()
        self.assertEqual(self.user.coin, 0)

    def test_webhook_endpoint_still_refuses_blank_gateway(self):
        r = self.client.post(
            reverse("buy-coin-confirm"), {"gateway_reference": "manual-1", "status": "success"}, format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)


# ----------------------------------------- abuse / input-validation (6-9)
import io
import shutil
import tempfile

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import CoinLedger, CoinWithdrawalRequest, UserPreference
from .throttles import CoinPurchaseBurstThrottle, CoinPurchaseDailyThrottle


def make_image(fmt="PNG", size=(20, 20), name="p.png", content_type="image/png"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, fmt)
    return SimpleUploadedFile(name, buf.getvalue(), content_type=content_type)


class BuyCoinAbuseTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = mk("buyer")
        self.client.force_authenticate(self.user)
        self.url = reverse("buy-coin")

    def buy(self, ref):
        return self.client.post(
            self.url, {"gateway_reference": ref, "amount": "10.00", "coins": 10, "gateway": "razorpay"},
            format="json",
        )

    def test_burst_throttle_returns_429_and_stops_row_creation(self):
        with mock.patch.dict(
            CoinPurchaseBurstThrottle.THROTTLE_RATES, {"profile_coin_purchase": "3/min"},
        ):
            codes = [self.buy(f"ref-{i}").status_code for i in range(5)]
        self.assertEqual(codes, [201, 201, 201, 429, 429])
        self.assertEqual(CoinPurchaseRequest.objects.filter(user=self.user).count(), 3)

    def test_daily_throttle_is_enforced(self):
        with mock.patch.dict(
            CoinPurchaseDailyThrottle.THROTTLE_RATES, {"profile_coin_purchase_daily": "2/day"},
        ):
            codes = [self.buy(f"d-{i}").status_code for i in range(4)]
        self.assertEqual(codes, [201, 201, 429, 429])

    @override_settings(COIN_PURCHASE_MAX_PENDING_PER_USER=2)
    def test_per_user_pending_cap_blocks_new_rows_but_not_idempotent_retries(self):
        self.assertEqual(self.buy("a").status_code, 201)
        self.assertEqual(self.buy("b").status_code, 201)
        self.assertEqual(self.buy("c").status_code, 429)
        self.assertEqual(self.buy("a").status_code, 200)  # retry of an existing ref still works
        self.assertEqual(CoinPurchaseRequest.objects.filter(user=self.user).count(), 2)

    def test_reusing_someone_elses_reference_is_409(self):
        self.assertEqual(self.buy("shared").status_code, 201)
        self.client.force_authenticate(mk("thief"))
        self.assertEqual(self.buy("shared").status_code, 409)

    @override_settings(COIN_PURCHASE_MAX_PENDING_PER_USER=1)
    def test_cap_is_per_user(self):
        self.assertEqual(self.buy("mine").status_code, 201)
        self.client.force_authenticate(mk("other"))
        self.assertEqual(self.buy("theirs").status_code, 201)


class ProfilePhotoValidationTests(APITestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.user = mk("pic")
        self.client.force_authenticate(self.user)
        self.url = reverse("update-profile")

    def patch_photo(self, f, **extra):
        with override_settings(MEDIA_ROOT=self.tmp):
            return self.client.patch(self.url, {"profile_photo": f}, format="multipart", **extra)

    def test_valid_png_is_accepted(self):
        r = self.patch_photo(make_image())
        self.assertEqual(r.status_code, 200, r.data)

    def test_non_image_with_image_extension_is_rejected(self):
        r = self.patch_photo(SimpleUploadedFile("evil.jpg", b"MZ\x90\x00 not an image", content_type="image/jpeg"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("profile_photo", r.data["errors"])

    def test_executable_extension_is_rejected_even_with_valid_image_bytes(self):
        r = self.patch_photo(make_image(name="shell.exe"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("profile_photo", r.data["errors"])

    def test_real_gif_renamed_png_is_rejected_by_format_check(self):
        r = self.patch_photo(make_image(fmt="GIF", name="x.png", content_type="image/png"))
        self.assertEqual(r.status_code, 400)

    @override_settings(PROFILE_PHOTO_MAX_BYTES=500)
    def test_oversize_file_is_rejected(self):
        big = make_image(size=(300, 300))  # noisy-enough PNG > 500 bytes
        self.assertGreater(big.size, 500)
        r = self.patch_photo(big)
        self.assertEqual(r.status_code, 400)
        self.assertIn("at most", str(r.data["errors"]["profile_photo"]))

    @override_settings(PROFILE_PHOTO_MAX_DIMENSION=10)
    def test_huge_dimensions_are_rejected(self):
        r = self.patch_photo(make_image(size=(50, 50)))
        self.assertEqual(r.status_code, 400)

    def test_declared_content_length_over_limit_gets_413_before_parsing(self):
        r = self.patch_photo(make_image(), CONTENT_LENGTH="99999999")
        self.assertEqual(r.status_code, 413)

    def test_other_profile_fields_still_update_without_a_photo(self):
        r = self.client.patch(self.url, {"bio": "hello"}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)


class WithdrawalPayoutValidationTests(APITestCase):
    def setUp(self):
        self.alice = mk("alice")
        CoinLedger.objects.record_transaction(
            user=self.alice, transaction_type=CoinLedger.TransactionType.PURCHASE,
            amount=500, reference="fund",
        )
        self.client.force_authenticate(self.alice)
        self.url = reverse("coin-withdrawal-requests")

    def post(self, **payload):
        payload.setdefault("coins", 150)
        return self.client.post(self.url, payload, format="json")

    def assertRejected(self, r):
        self.assertEqual(r.status_code, 400, getattr(r, "data", r.content))
        self.assertEqual(CoinWithdrawalRequest.objects.count(), 0)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.coin, 500)  # nothing debited

    def test_blank_payout_method_is_rejected(self):
        self.assertRejected(self.post(payout_method="", payout_details={}))

    def test_missing_payout_method_is_rejected(self):
        self.assertRejected(self.post(payout_details={"upi_id": "a@bank"}))

    def test_unknown_payout_method_is_rejected(self):
        self.assertRejected(self.post(payout_method="cash", payout_details={}))

    def test_upi_with_empty_or_whitespace_id_is_rejected(self):
        self.assertRejected(self.post(payout_method="upi", payout_details={"upi_id": "   "}))

    def test_upi_with_bad_shape_is_rejected(self):
        self.assertRejected(self.post(payout_method="upi", payout_details={"upi_id": "not-a-upi"}))

    def test_payout_details_of_wrong_type_is_400_not_500(self):
        self.assertRejected(self.post(payout_method="upi", payout_details=["a@bank"]))
        self.assertRejected(self.post(payout_method="upi", payout_details="a@bank"))

    def test_bank_transfer_requires_valid_fields(self):
        self.assertRejected(self.post(payout_method="bank_transfer", payout_details={
            "account_holder": "", "account_number": "123", "ifsc": "BAD",
        }))

    def test_valid_bank_transfer_is_normalised_and_junk_keys_dropped(self):
        r = self.post(payout_method="bank_transfer", payout_details={
            "account_holder": " Alice Roy ", "account_number": "1234 5678 9012",
            "ifsc": "hdfc0001234", "evil": "x" * 10000,
        })
        self.assertEqual(r.status_code, 201, r.data)
        row = CoinWithdrawalRequest.objects.get()
        self.assertEqual(row.payout_details, {
            "account_holder": "Alice Roy", "account_number": "123456789012", "ifsc": "HDFC0001234",
        })

    def test_valid_upi_is_accepted(self):
        r = self.post(payout_method="upi", payout_details={"upi_id": "alice.roy@okhdfcbank"})
        self.assertEqual(r.status_code, 201, r.data)


class LanguagePreferenceValidationTests(APITestCase):
    def setUp(self):
        self.user = mk("lang")
        self.client.force_authenticate(self.user)
        self.url = reverse("user-preferences")

    def patch_lang(self, value):
        return self.client.patch(self.url, {"language": value}, format="json")

    def test_garbage_language_is_rejected_and_not_saved(self):
        for bad in ("HACKERCODE", "xx", "<script>", "", "en_US", "en-", "e"):
            r = self.patch_lang(bad)
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(UserPreference.for_user(self.user).language, "en")

    def test_supported_language_is_saved(self):
        r = self.patch_lang("hi")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(UserPreference.for_user(self.user).language, "hi")

    def test_region_variant_is_canonicalised(self):
        r = self.patch_lang("EN-us")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(UserPreference.for_user(self.user).language, "en-US")

    @override_settings(SUPPORTED_LANGUAGES=("en",))
    def test_language_outside_configured_list_is_rejected(self):
        self.assertEqual(self.patch_lang("hi").status_code, 400)

    def test_invalid_theme_is_still_rejected(self):
        r = self.client.patch(self.url, {"theme": "neon"}, format="json")
        self.assertEqual(r.status_code, 400)


# ------------------------------------------------- crash-hardening checks
class CrashHardeningTests(APITestCase):
    def test_admin_confirm_view_is_importable_and_routed(self):
        from django.urls import resolve
        from . import views
        self.assertTrue(hasattr(views, "AdminCoinPurchaseConfirmView"))
        self.assertEqual(
            resolve(reverse("buy-coin-admin-confirm")).func.view_class, views.AdminCoinPurchaseConfirmView,
        )

    def _webhook_purchase(self):
        CoinPurchaseRequest.objects.start_purchase(
            user=mk("payer"), gateway_reference="rzp-9", amount="10.00", coins=10, gateway="razorpay",
        )

    @override_settings(
        PAYMENT_GATEWAY_WEBHOOK_SECRETS={"razorpay": "topsecret"},
        PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS={"razorpay": "X-Webhook-Signature"},
    )
    def test_webhook_valid_signature_credits_wallet(self):
        import hashlib, hmac, json
        self._webhook_purchase()
        body = json.dumps({"gateway_reference": "rzp-9", "status": "success"})
        sig = hmac.new(b"topsecret", body.encode(), hashlib.sha256).hexdigest()
        r = self.client.post(
            reverse("buy-coin-confirm"), data=body, content_type="application/json",
            HTTP_X_WEBHOOK_SIGNATURE=sig,
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(User.objects.get(username="payer").coin, 10)

    @override_settings(
        PAYMENT_GATEWAY_WEBHOOK_SECRETS={"razorpay": "topsecret"},
        PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS={"razorpay": "X-Webhook-Signature"},
    )
    def test_webhook_non_ascii_signature_header_is_401_not_500(self):
        self._webhook_purchase()
        r = self.client.post(
            reverse("buy-coin-confirm"),
            {"gateway_reference": "rzp-9", "status": "success"}, format="json",
            HTTP_X_WEBHOOK_SIGNATURE="\u00e9\u00e9\u00e9",
        )
        self.assertEqual(r.status_code, 401)
        self.assertEqual(User.objects.get(username="payer").coin, 0)

    def test_webhook_unreadable_body_fails_closed_with_400(self):
        from django.http.request import RawPostDataException
        self._webhook_purchase()
        with mock.patch(
            "django.core.handlers.wsgi.WSGIRequest.body",
            new_callable=mock.PropertyMock, side_effect=RawPostDataException,
        ):
            r = self.client.post(
                reverse("buy-coin-confirm"),
                {"gateway_reference": "rzp-9", "status": "success"}, format="json",
            )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(User.objects.get(username="payer").coin, 0)

    def test_unfollow_with_drifted_zero_counter_does_not_crash_or_go_negative(self):
        a, b = mk("a"), mk("b")
        Follow.objects.create(follower=a, following=b)
        User.objects.filter(pk__in=[a.pk, b.pk]).update(followers_count=0, following_count=0)  # simulate drift
        self.client.force_authenticate(a)
        r = self.client.post(reverse("follow-user", args=[b.id]))  # POST on an existing follow = unfollow
        self.assertIn(r.status_code, (200, 204))
        b.refresh_from_db(); a.refresh_from_db()
        self.assertEqual((b.followers_count, a.following_count), (0, 0))

    def test_post_and_comment_decrements_clamp_at_zero(self):
        from post.signals import decrement_posts_count_on_soft_delete
        owner = mk("owner")
        post = Post.objects.create(user=owner, content="x")
        User.objects.filter(pk=owner.pk).update(posts_count=0)
        decrement_posts_count_on_soft_delete(post)  # would violate CHECK (>=0) before
        owner.refresh_from_db()
        self.assertEqual(owner.posts_count, 0)

    def test_follow_succeeds_even_if_notification_layer_explodes(self):
        a, b = mk("a"), mk("b")
        self.client.force_authenticate(a)
        with mock.patch("core.services.create_notification", side_effect=RuntimeError("boom")):
            r = self.client.post(reverse("follow-user", args=[b.id]))
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(Follow.objects.filter(follower=a, following=b).exists())

    def test_follow_succeeds_even_if_core_import_fails(self):
        import builtins
        a, b = mk("a"), mk("b")
        self.client.force_authenticate(a)
        real_import = builtins.__import__

        def broken(name, *args, **kw):
            if name == "core.services":
                raise ImportError("core broken")
            return real_import(name, *args, **kw)

        with mock.patch("builtins.__import__", side_effect=broken):
            r = self.client.post(reverse("follow-user", args=[b.id]))
        self.assertEqual(r.status_code, 201, r.data)


# --------------------------------------------------------------------- #4
class FollowCountSignalTests(APITestCase):
    def setUp(self):
        self.a, self.b, self.c = mk("a"), mk("b"), mk("c")

    def counts(self, u):
        u.refresh_from_db()
        return u.followers_count, u.following_count

    def test_create_and_delete_via_orm_keep_counts_exact(self):
        f = Follow.objects.create(follower=self.a, following=self.b)
        self.assertEqual(self.counts(self.b), (1, 0))
        self.assertEqual(self.counts(self.a), (0, 1))
        f.delete()  # what an admin delete does
        self.assertEqual(self.counts(self.b), (0, 0))
        self.assertEqual(self.counts(self.a), (0, 0))

    def test_pending_does_not_count_until_accepted(self):
        f = Follow.objects.create(follower=self.a, following=self.b, status=Follow.Status.PENDING)
        self.assertEqual(self.counts(self.b), (0, 0))
        f.status = Follow.Status.ACCEPTED
        f.save(update_fields=["status"])
        self.assertEqual(self.counts(self.b), (1, 0))

    def test_saving_accepted_twice_never_double_counts(self):
        f = Follow.objects.create(follower=self.a, following=self.b)
        f.save()
        f.save(update_fields=["status"])
        self.assertEqual(self.counts(self.b), (1, 0))

    def test_hard_deleting_a_user_fixes_the_other_sides_counters(self):
        Follow.objects.create(follower=self.a, following=self.b)
        Follow.objects.create(follower=self.b, following=self.c)
        self.b.delete()
        self.assertEqual(self.counts(self.a), (0, 0))
        self.assertEqual(self.counts(self.c), (0, 0))

    def test_repointing_a_follow_recounts_old_and_new_users(self):
        f = Follow.objects.create(follower=self.a, following=self.b)
        f.following = self.c
        f.save()
        self.assertEqual(self.counts(self.b), (0, 0))
        self.assertEqual(self.counts(self.c), (1, 0))

    def test_queryset_delete_and_block_flow(self):
        Follow.objects.create(follower=self.a, following=self.b)
        Follow.objects.create(follower=self.b, following=self.a)
        self.client.force_authenticate(self.a)
        r = self.client.post(reverse("blocked-users"), {"blocked": self.b.id}, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        self.assertEqual(self.counts(self.a), (0, 0))
        self.assertEqual(self.counts(self.b), (0, 0))

    def test_reconcile_task_finds_no_drift_after_signals(self):
        from .tasks import reconcile_follow_counts
        Follow.objects.create(follower=self.a, following=self.b)
        Follow.objects.create(follower=self.c, following=self.b)
        out = reconcile_follow_counts()
        self.assertEqual(out["corrected_followers_count"], 0)
        self.assertEqual(out["corrected_following_count"], 0)

    def test_duplicate_follow_request_returns_clean_200_with_real_row(self):
        # Real DB duplicate (not a mock): make the view's "does it exist"
        # lookup miss so it proceeds to INSERT and hits the unique constraint.
        Follow.objects.create(follower=self.a, following=self.b)
        self.client.force_authenticate(self.a)
        real_filter = Follow.objects.filter
        calls = {"n": 0}

        def flaky_filter(*args, **kwargs):
            qs = real_filter(*args, **kwargs)
            calls["n"] += 1
            if calls["n"] == 1:
                return qs.none()
            return qs

        with mock.patch.object(Follow.objects, "filter", side_effect=flaky_filter):
            r = self.client.post(reverse("follow-user", args=[self.b.id]))
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(r.data["status"], Follow.Status.ACCEPTED)
        self.assertEqual(self.counts(self.b), (1, 0))


class AdminRegistrationTests(APITestCase):
    def test_graph_and_purchase_models_are_registered(self):
        from django.contrib import admin
        for model in (Follow, RestrictUser, CoinPurchaseRequest):
            self.assertIn(model, admin.site._registry)
        ma = admin.site._registry[CoinPurchaseRequest]
        self.assertFalse(ma.has_change_permission(None))

    def test_admin_bulk_delete_of_follows_keeps_counts_exact(self):
        a, b, c = mk("a"), mk("b"), mk("c")
        Follow.objects.create(follower=a, following=c)
        Follow.objects.create(follower=b, following=c)
        Follow.objects.filter(following=c).delete()  # what "delete selected" runs
        c.refresh_from_db()
        self.assertEqual(c.followers_count, 0)


# --------------------------------------------------------------------- #3
class FollowNotificationTests(APITestCase):
    def setUp(self):
        self.alice, self.bob = mk("alice"), mk("bob", is_private=True)
        self.carol = mk("carol")

    def test_follow_request_to_private_account_notifies_target(self):
        self.client.force_authenticate(self.alice)
        self.client.post(reverse("follow-user", args=[self.bob.id]))
        n = Notification.objects.get(recipient=self.bob)
        self.assertEqual(n.notif_type, Notification.NotifType.FOLLOW_REQUEST_RECEIVED)

    def test_accepting_notifies_the_requester(self):
        f = Follow.objects.create(follower=self.alice, following=self.bob, status=Follow.Status.PENDING)
        self.client.force_authenticate(self.bob)
        self.client.post(reverse("accept-request", args=[f.id]))
        n = Notification.objects.get(recipient=self.alice)
        self.assertEqual(n.notif_type, Notification.NotifType.FOLLOW_REQUEST_ACCEPTED)

    def test_public_follow_notifies_target(self):
        self.client.force_authenticate(self.alice)
        self.client.post(reverse("follow-user", args=[self.carol.id]))
        self.assertTrue(Notification.objects.filter(recipient=self.carol).exists())

    def test_no_bell_if_target_restricted_the_follower(self):
        RestrictUser.objects.create(user=self.carol, restricted=self.alice)
        self.client.force_authenticate(self.alice)
        r = self.client.post(reverse("follow-user", args=[self.carol.id]))
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)  # alice sees normal success
        self.assertFalse(Notification.objects.filter(recipient=self.carol).exists())


# --------------------------------------------------------------------- #2
class RestrictHelpersTests(APITestCase):
    def test_helpers_are_one_way(self):
        a, b = mk("a"), mk("b")
        RestrictUser.objects.create(user=a, restricted=b)
        self.assertTrue(has_restricted(a.id, b.id))
        self.assertFalse(has_restricted(b.id, a.id))
        self.assertEqual(restricted_ids_by(a.id), {b.id})
        self.assertEqual(restrictor_ids_of(b.id), {a.id})
        self.assertEqual(restrictor_ids_of(b.id, among=[]), set())
        self.assertEqual(restrictor_ids_of(a.id), set())


class RestrictPostCommentTests(APITestCase):
    def setUp(self):
        self.owner, self.rest, self.other = mk("owner"), mk("rest"), mk("other")
        self.post = Post.objects.create(user=self.owner, content="hello")
        self.c_rest = PostComment.objects.create(post=self.post, user=self.rest, content="from restricted")
        self.c_other = PostComment.objects.create(post=self.post, user=self.other, content="from other")
        self.reply_rest = PostComment.objects.create(
            post=self.post, user=self.rest, parent=self.c_other, content="reply from restricted",
        )
        RestrictUser.objects.create(user=self.owner, restricted=self.rest)

    def ids(self, resp):
        data = resp.data["results"] if isinstance(resp.data, dict) and "results" in resp.data else resp.data
        return {str(c["id"]) for c in data}

    def test_list_hides_restricted_comments_from_others_and_owner(self):
        for viewer in (self.other, self.owner):
            self.client.force_authenticate(viewer)
            r = self.client.get(reverse("comment-list", args=[self.post.id]))
            self.assertEqual(self.ids(r), {str(self.c_other.id)}, viewer.username)

    def test_restricted_user_still_sees_own_comment(self):
        self.client.force_authenticate(self.rest)
        r = self.client.get(reverse("comment-list", args=[self.post.id]))
        self.assertEqual(self.ids(r), {str(self.c_rest.id), str(self.c_other.id)})

    def test_anonymous_viewer_also_gets_filtered_list(self):
        r = self.client.get(reverse("comment-list", args=[self.post.id]))
        self.assertEqual(self.ids(r), {str(self.c_other.id)})

    def test_replies_endpoint_hides_restricted_replies(self):
        self.client.force_authenticate(self.other)
        r = self.client.get(reverse("comment-replies", args=[self.c_other.id]))
        self.assertEqual(self.ids(r), set())
        self.client.force_authenticate(self.rest)
        r = self.client.get(reverse("comment-replies", args=[self.c_other.id]))
        self.assertEqual(self.ids(r), {str(self.reply_rest.id)})

    def test_comment_notification_is_skipped_for_restricted_commenter(self):
        from post.services import notify_post_commented
        notify_post_commented(self.post, self.c_rest)
        self.assertFalse(Notification.objects.filter(recipient=self.owner).exists())
        notify_post_commented(self.post, self.c_other)
        self.assertTrue(Notification.objects.filter(recipient=self.owner).exists())


class RestrictMessageTests(APITestCase):
    """A restricted B."""

    def setUp(self):
        self.a, self.b, self.c = mk("a"), mk("b"), mk("c")
        RestrictUser.objects.create(user=self.a, restricted=self.b)
        self.convo, _ = Conversation.get_or_create_private(self.a, self.b)

    def test_push_and_bell_suppressed_for_restrictor_only(self):
        from message import push_utils
        msg = Message.objects.create(conversation=self.convo, sender=self.b, text="hi")
        with mock.patch.object(push_utils, "_send_single_chat_push") as push, \
                mock.patch.object(push_utils, "send_chat_digest_push"):
            push_utils.send_chat_message_push(
                [self.a.id, self.c.id], "b", "hi", "text", self.convo.id, msg.id,
            )
        self.assertFalse(Notification.objects.filter(recipient=self.a).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.c).exists())
        pushed = {str(x) for call in push.call_args_list for x in call.args[0]}
        self.assertEqual(pushed, {str(self.c.id)})

    def test_mention_push_suppressed_for_restrictor(self):
        from message import push_utils
        msg = Message.objects.create(conversation=self.convo, sender=self.b, text="@a")
        with mock.patch.object(push_utils, "_send_multicast") as mc:
            push_utils.send_mention_push([self.a.id], "b", "@a", self.convo.id, msg.id)
        self.assertFalse(Notification.objects.filter(recipient=self.a).exists())
        mc.assert_not_called()

    def test_restriction_is_not_applied_in_the_other_direction(self):
        from message import push_utils
        msg = Message.objects.create(conversation=self.convo, sender=self.a, text="yo")
        with mock.patch.object(push_utils, "_send_single_chat_push"), \
                mock.patch.object(push_utils, "send_chat_digest_push"):
            push_utils.send_chat_message_push([self.b.id], "a", "yo", "text", self.convo.id, msg.id)
        self.assertTrue(Notification.objects.filter(recipient=self.b).exists())

    def test_read_status_hides_read_receipt_of_restrictor_from_restricted_sender(self):
        msg = Message.objects.create(conversation=self.convo, sender=self.b, text="hi")
        MessageStatus.objects.create(message=msg, user=self.a, is_delivered=True, is_read=True)
        self.client.force_authenticate(self.b)
        r = self.client.get(reverse("message-read-status", args=[msg.id]))
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(len(r.data["read_by"]), 0)
        self.assertEqual(len(r.data["delivered_to"]), 1)  # delivery tick is untouched

    def test_read_status_still_shows_receipt_when_not_restricted(self):
        msg = Message.objects.create(conversation=self.convo, sender=self.a, text="hi")
        MessageStatus.objects.create(message=msg, user=self.b, is_delivered=True, is_read=True)
        self.client.force_authenticate(self.a)
        r = self.client.get(reverse("message-read-status", args=[msg.id]))
        self.assertEqual(len(r.data["read_by"]), 1)

    def test_presence_hidden_from_restricted_user_only(self):
        UserPresence.objects.update_or_create(user=self.a, defaults={"is_online": True})
        self.client.force_authenticate(self.b)
        r = self.client.get(reverse("user-presence", args=[self.a.id]))
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertFalse(r.data["is_online"])
        self.client.force_authenticate(self.c)
        r = self.client.get(reverse("user-presence", args=[self.a.id]))
        self.assertTrue(r.data["is_online"])


# --------------------------------------------------------------------- #5
class BoundedPaginationTests(APITestCase):
    def test_page_size_is_clamped_to_max(self):
        from rest_framework.test import APIRequestFactory
        from rest_framework.request import Request
        with override_settings(MAX_PAGE_SIZE=7):
            req = Request(APIRequestFactory().get("/x/", {"page_size": "10000"}))
            self.assertEqual(StandardPagination().get_page_size(req), 7)
            req = Request(APIRequestFactory().get("/x/", {"page_size": "3"}))
            self.assertEqual(StandardPagination().get_page_size(req), 3)
            req = Request(APIRequestFactory().get("/x/", {"limit": "10000"}))
            self.assertEqual(StandardPagination().get_page_size(req), 20)

    def test_followers_endpoint_never_returns_more_than_max(self):
        target = mk("target")
        for i in range(12):
            Follow.objects.create(follower=mk(f"u{i}"), following=target)
        viewer = mk("viewer")
        self.client.force_authenticate(viewer)
        url = reverse("user-followers", args=[target.username])
        with override_settings(MAX_PAGE_SIZE=5):
            r = self.client.get(url, {"page_size": 10000})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r.data["data"]["results"]), 5)

    def test_bulk_connections_restricted_matches_full_mutual_counts(self):
        me, x, y, m1, m2, other = (mk(n) for n in ("me", "x", "y", "m1", "m2", "other"))
        Follow.objects.create(follower=me, following=m1)
        Follow.objects.create(follower=m2, following=me)
        Follow.objects.create(follower=x, following=m1)
        Follow.objects.create(follower=m2, following=x)
        Follow.objects.create(follower=y, following=other)  # nothing in common with me
        mine = accepted_connection_ids(me)
        full = bulk_accepted_connection_ids([x.id, y.id])
        bounded = bulk_accepted_connection_ids([x.id, y.id], restrict_to_user=me)
        for uid in (x.id, y.id):
            self.assertEqual(
                len(mine & full.get(uid, set())), len(mine & bounded.get(uid, set())),
            )
        self.assertEqual(bounded[x.id], {m1.id, m2.id})
        self.assertNotIn(other.id, bounded.get(y.id, set()))  # unrelated rows not loaded

    def test_followers_list_reports_correct_mutual_friends(self):
        me, f, mutual = mk("me"), mk("f"), mk("mutual")
        target = mk("target")
        Follow.objects.create(follower=me, following=mutual)
        Follow.objects.create(follower=f, following=mutual)
        Follow.objects.create(follower=f, following=target)
        self.client.force_authenticate(me)
        r = self.client.get(reverse("user-followers", args=[target.username]))
        row = next(x for x in r.data["data"]["results"] if x["username"] == "f")
        self.assertEqual(row["mutual_friends"], 1)
