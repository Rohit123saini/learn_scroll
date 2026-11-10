# campus/tests_resources.py
"""
Class resources (content share): folders, resources, visibility, open/viewers,
the `campus_resource_posted` push and "copy to other sections".

Run: `python manage.py test campus.tests_resources`

Celery / FCM are never touched: `dispatch_after_commit` is mocked on the API
paths and `campus.bridge.notify` / `push_to_parent_devices` on the push path —
we assert WHO is pushed and WITH push=True, not delivery.
"""
import shutil
import tempfile
import uuid
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status

from . import resource_rules
from . import tasks as campus_tasks
from .bridge import NotifTypes
from .models import (
    ClassResource,
    ClassResourceView,
    ClassTeacherassigments,
    ResourceFolder,
    StaffProfile,
)
from .tests_t4_efg import T4Base, rows


def pdf(name="notes.pdf", size=None):
    data = b"%PDF-1.4 test" if size is None else b"x" * size
    return SimpleUploadedFile(name, data, content_type="application/pdf")


class ResourceBase(T4Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        self._media = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._media)
        self._override.enable()
        self.addCleanup(self._cleanup_media)

    def _cleanup_media(self):
        self._override.disable()
        shutil.rmtree(self._media, ignore_errors=True)

    # ---- factories --------------------------------------------------------
    def make_folder(self, *, section=None, subject=None, name="Chapter 1", parent=None, user=None):
        return ResourceFolder.objects.create(
            section=section or self.secA, subject=subject or self.math, name=name,
            parent=parent, created_by=user or self.teacher,
        )

    def make_resource(self, *, folder=None, title="Worksheet", poster=None, **kw):
        folder = folder or self.make_folder()
        values = dict(
            folder=folder, posted_by=poster or self.teacher, kind=ClassResource.Kind.LINK,
            title=title, external_url="https://example.com/a",
        )
        values.update(kw)
        return ClassResource.objects.create(**values)

    # ---- request helpers ----------------------------------------------------
    def post_folder(self, user, **body):
        self.client.force_authenticate(user)
        return self.client.post(reverse("campus-resource-folder-list"), body, format="json")

    def post_link(self, user, folder, **overrides):
        data = {"folder": str(folder.id), "title": "Lecture 1", "external_url": "https://youtu.be/abc", "kind": "video"}
        data.update(overrides)
        data = {key: value for key, value in data.items() if value is not None}  # None = "leave it out"
        self.client.force_authenticate(user)
        with mock.patch("campus.resources.dispatch_after_commit") as dispatch:
            resp = self.client.post(reverse("campus-class-resource-list"), data, format="json")
        return resp, dispatch

    def post_file(self, user, folder, upload, **extra):
        data = {"folder": str(folder.id), "title": "Notes", "file": upload}
        data.update(extra)
        self.client.force_authenticate(user)
        with mock.patch("campus.resources.dispatch_after_commit") as dispatch:
            resp = self.client.post(reverse("campus-class-resource-list"), data, format="multipart")
        return resp, dispatch


# ===========================================================================
# Folders
# ===========================================================================

class FolderTests(ResourceBase):
    def test_subject_teacher_creates_root_folder_scope_is_derived(self):
        resp = self.post_folder(self.teacher, section=str(self.secA.id), subject=str(self.math.id), name="Algebra")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        folder = ResourceFolder.objects.get(pk=resp.data["id"])
        self.assertEqual(folder.campus_id, self.campus.id)
        self.assertEqual(folder.session_id, self.session.id)
        self.assertEqual(folder.created_by_id, self.teacher.id)
        self.assertTrue(resp.data["can_manage"])
        self.assertEqual(resp.data["subject_name"], "Maths")
        self.assertEqual(resp.data["section_label"], "10 - A")

    def test_sub_folder_inherits_section_and_subject_and_nests_only_one_level(self):
        root = self.make_folder(name="Algebra")
        resp = self.post_folder(self.teacher, parent=str(root.id), name="Ch 3")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        child = ResourceFolder.objects.get(pk=resp.data["id"])
        self.assertEqual((child.section_id, child.subject_id), (self.secA.id, self.math.id))
        deeper = self.post_folder(self.teacher, parent=str(child.id), name="Too deep")
        self.assertEqual(deeper.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_name_among_active_siblings_rejected_but_reusable_after_delete(self):
        self.make_folder(name="Algebra")
        resp = self.post_folder(self.teacher, section=str(self.secA.id), subject=str(self.math.id), name="algebra")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        ResourceFolder.objects.filter(name="Algebra").update(is_active=False)
        resp = self.post_folder(self.teacher, section=str(self.secA.id), subject=str(self.math.id), name="Algebra")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_teacher_cannot_create_for_unapproved_subject_or_other_section(self):
        resp = self.post_folder(self.teacher, section=str(self.secA.id), subject=str(self.phy.id), name="Optics")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # PENDING assignment
        resp = self.post_folder(self.teacher, section=str(self.secB.id), subject=str(self.math.id), name="X")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_class_teacher_any_subject_admin_anywhere(self):
        ct_staff = StaffProfile.objects.create(
            campus=self.campus, user=self.other_teacher, role=StaffProfile.Role.CLASS_TEACHER
        )
        ClassTeacherassigments.objects.create(section=self.secA, staff=ct_staff)
        resp = self.post_folder(self.other_teacher, section=str(self.secA.id), subject=str(self.phy.id), name="Optics")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        resp = self.post_folder(self.admin, section=str(self.secB.id), subject=str(self.math.id), name="Any")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_student_and_parent_cannot_write(self):
        for user in (self.sa1, self.parent):
            resp = self.post_folder(user, section=str(self.secA.id), subject=str(self.math.id), name="Nope")
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, user.username)
        folder = self.make_folder()
        self.client.force_authenticate(self.sa1)
        self.assertEqual(
            self.client.patch(reverse("campus-resource-folder-detail", args=[folder.id]), {"name": "x"}, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.delete(reverse("campus-resource-folder-detail", args=[folder.id])).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_section_subject_cannot_be_changed_on_edit(self):
        folder = self.make_folder()
        self.client.force_authenticate(self.teacher)
        url = reverse("campus-resource-folder-detail", args=[folder.id])
        self.assertEqual(self.client.patch(url, {"section": str(self.secB.id)}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(url, {"name": "Renamed"}, format="json").status_code, 200)

    def test_delete_is_soft_and_hides_sub_folders_and_resources(self):
        root = self.make_folder(name="Algebra")
        child = self.make_folder(name="Ch 3", parent=root)
        res = self.make_resource(folder=child)
        self.client.force_authenticate(self.teacher)
        resp = self.client.delete(reverse("campus-resource-folder-detail", args=[root.id]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        root.refresh_from_db(); child.refresh_from_db(); res.refresh_from_db()
        self.assertFalse(root.is_active)
        self.assertFalse(child.is_active)
        self.assertFalse(res.is_active)
        self.assertTrue(ResourceFolder.objects.filter(pk=root.pk).exists())  # never hard-deleted

    def test_counts_only_published_resources(self):
        folder = self.make_folder()
        self.make_resource(folder=folder, title="a")
        self.make_resource(folder=folder, title="b")
        self.make_resource(folder=folder, title="draft", is_published=False)
        self.client.force_authenticate(self.sa1)
        got = rows(self.client.get(reverse("campus-resource-folder-list"), {"section": str(self.secA.id)}))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["resource_count"], 2)
        self.assertFalse(got[0]["can_manage"])

    def test_student_cannot_see_other_sections_folders(self):
        self.make_folder(section=self.secB, subject=self.math, name="B only", user=self.other_teacher)
        self.client.force_authenticate(self.sa1)
        self.assertEqual(rows(self.client.get(reverse("campus-resource-folder-list"))), [])

    def test_subjects_landing_lists_approved_subjects_with_can_post(self):
        self.make_folder()
        url = reverse("campus-resource-folder-subjects")
        self.client.force_authenticate(self.teacher)
        resp = self.client.get(url, {"section": str(self.secA.id)})
        self.assertEqual(resp.status_code, 200, resp.data)
        by_name = {r["name"]: r for r in resp.data["subjects"]}
        self.assertEqual(set(by_name), {"Maths"})  # Physics is only PENDING
        self.assertEqual(by_name["Maths"]["folder_count"], 1)
        self.assertTrue(by_name["Maths"]["can_post"])
        self.client.force_authenticate(self.sa1)
        resp = self.client.get(url, {"section": str(self.secA.id)})
        self.assertFalse(resp.data["subjects"][0]["can_post"])
        # a student can't peek into another section, and a missing section is a 400
        self.assertEqual(self.client.get(url, {"section": str(self.secB.id)}).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 400)


# ===========================================================================
# Resources
# ===========================================================================

class ResourceCreateTests(ResourceBase):
    def test_link_resource_and_derived_scope(self):
        folder = self.make_folder()
        resp, dispatch = self.post_link(self.teacher, folder)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        res = ClassResource.objects.get(pk=resp.data["id"])
        self.assertEqual(res.kind, "video")
        self.assertEqual((res.section_id, res.subject_id, res.campus_id), (self.secA.id, self.math.id, self.campus.id))
        self.assertEqual(res.posted_by_id, self.teacher.id)
        self.assertTrue(resp.data["can_manage"])
        self.assertEqual(resp.data["opened_by_count"], 0)
        dispatch.assert_called_once_with(campus_tasks.send_class_resource_posted, str(res.pk))

    def test_link_without_kind_defaults_to_link(self):
        resp, _ = self.post_link(self.teacher, self.make_folder(), kind=None)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["kind"], "link")

    def test_scope_ids_in_body_are_ignored(self):
        folder = self.make_folder()
        resp, _ = self.post_link(
            self.teacher, folder, section=str(self.secB.id), subject=str(self.phy.id), campus=str(self.campus.id)
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        res = ClassResource.objects.get(pk=resp.data["id"])
        self.assertEqual((res.section_id, res.subject_id), (self.secA.id, self.math.id))

    def test_pdf_upload_kind_size_and_name_recorded(self):
        resp, _ = self.post_file(self.teacher, self.make_folder(), pdf("worksheet.pdf"))
        self.assertEqual(resp.status_code, 201, resp.data)
        res = ClassResource.objects.get(pk=resp.data["id"])
        self.assertEqual(res.kind, "pdf")
        self.assertEqual(res.original_name, "worksheet.pdf")
        self.assertEqual(res.file_size, len(b"%PDF-1.4 test"))
        self.assertEqual(res.external_url, "")

    def test_video_upload_is_kind_video(self):
        up = SimpleUploadedFile("lec.mp4", b"\x00\x00\x00\x18ftypmp42", content_type="video/mp4")
        resp, _ = self.post_file(self.teacher, self.make_folder(), up)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["kind"], "video")

    def test_disallowed_extension_rejected(self):
        up = SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream")
        resp, dispatch = self.post_file(self.teacher, self.make_folder(), up)
        self.assertEqual(resp.status_code, 400)
        dispatch.assert_not_called()

    def test_oversize_file_rejected(self):
        with mock.patch.object(resource_rules, "RESOURCE_MAX_MB", 0):
            resp, _ = self.post_file(self.teacher, self.make_folder(), pdf(size=10))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(ClassResource.objects.exists())

    def test_exactly_one_of_file_or_link(self):
        folder = self.make_folder()
        resp, _ = self.post_file(self.teacher, folder, pdf(), external_url="https://example.com/x")
        self.assertEqual(resp.status_code, 400)
        resp, _ = self.post_link(self.teacher, folder, external_url="")
        self.assertEqual(resp.status_code, 400)

    def test_kind_must_match_file(self):
        resp, _ = self.post_file(self.teacher, self.make_folder(), pdf(), kind="video")
        self.assertEqual(resp.status_code, 400)

    def test_non_http_link_rejected(self):
        resp, _ = self.post_link(self.teacher, self.make_folder(), external_url="javascript:alert(1)", kind="link")
        self.assertEqual(resp.status_code, 400)

    def test_draft_is_not_pushed(self):
        resp, dispatch = self.post_link(self.teacher, self.make_folder(), is_published=False)
        self.assertEqual(resp.status_code, 201, resp.data)
        dispatch.assert_not_called()

    def test_removed_folder_rejected(self):
        folder = self.make_folder()
        folder.is_active = False
        folder.save()
        resp, _ = self.post_link(self.teacher, folder)
        self.assertIn(resp.status_code, (400, 403))
        self.assertFalse(ClassResource.objects.exists())

    def test_folder_cap(self):
        folder = self.make_folder()
        self.make_resource(folder=folder, title="one")
        with mock.patch("campus.resource_serializers.MAX_RESOURCES_PER_FOLDER", 1):
            resp, _ = self.post_link(self.teacher, folder, title="two")
        self.assertEqual(resp.status_code, 400)

    def test_permissions(self):
        folder = self.make_folder()
        for user in (self.sa1, self.parent, self.other_teacher):
            resp, dispatch = self.post_link(user, folder)
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, user.username)
            dispatch.assert_not_called()
        resp, _ = self.post_link(self.admin, folder)
        self.assertEqual(resp.status_code, 201, resp.data)


class ResourceEditTests(ResourceBase):
    def test_publish_a_draft_pushes_once(self):
        res = self.make_resource(is_published=False)
        self.client.force_authenticate(self.teacher)
        url = reverse("campus-class-resource-detail", args=[res.id])
        with mock.patch("campus.resources.dispatch_after_commit") as dispatch:
            resp = self.client.patch(url, {"is_published": True}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        dispatch.assert_called_once_with(campus_tasks.send_class_resource_posted, str(res.pk))

    def test_plain_edit_or_already_notified_does_not_push(self):
        res = self.make_resource()
        self.client.force_authenticate(self.teacher)
        url = reverse("campus-class-resource-detail", args=[res.id])
        with mock.patch("campus.resources.dispatch_after_commit") as dispatch:
            self.client.patch(url, {"title": "Renamed"}, format="json")
            self.client.patch(url, {"is_pinned": True}, format="json")
        dispatch.assert_not_called()

    def test_replace_file_with_link_drops_the_upload(self):
        resp, _ = self.post_file(self.teacher, self.make_folder(), pdf())
        res = ClassResource.objects.get(pk=resp.data["id"])
        self.client.force_authenticate(self.teacher)
        url = reverse("campus-class-resource-detail", args=[res.id])
        resp = self.client.patch(url, {"external_url": "https://example.com/new", "kind": "link"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        res.refresh_from_db()
        self.assertFalse(res.file)
        self.assertEqual(res.external_url, "https://example.com/new")

    def test_move_only_within_same_section_and_subject(self):
        res = self.make_resource()
        other_same = self.make_folder(name="Chapter 2")
        other_section = self.make_folder(section=self.secB, subject=self.math, name="B", user=self.other_teacher)
        self.client.force_authenticate(self.teacher)
        url = reverse("campus-class-resource-detail", args=[res.id])
        self.assertEqual(self.client.patch(url, {"folder": str(other_same.id)}, format="json").status_code, 200)
        self.assertEqual(self.client.patch(url, {"folder": str(other_section.id)}, format="json").status_code, 400)

    def test_other_subject_teacher_cannot_edit_or_delete(self):
        res = self.make_resource()
        self.client.force_authenticate(self.other_teacher)  # approved only for secB
        url = reverse("campus-class-resource-detail", args=[res.id])
        self.assertEqual(self.client.patch(url, {"title": "hack"}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(url).status_code, 403)

    def test_delete_is_soft(self):
        res = self.make_resource()
        self.client.force_authenticate(self.teacher)
        resp = self.client.delete(reverse("campus-class-resource-detail", args=[res.id]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        res.refresh_from_db()
        self.assertFalse(res.is_active)
        self.client.force_authenticate(self.sa1)
        self.assertEqual(rows(self.client.get(reverse("campus-class-resource-list"))), [])


# ===========================================================================
# Visibility
# ===========================================================================

class VisibilityTests(ResourceBase):
    def setUp(self):
        super().setUp()
        self.folder = self.make_folder()
        self.pub = self.make_resource(folder=self.folder, title="Published")
        self.draft = self.make_resource(folder=self.folder, title="Draft", is_published=False)
        self.url = reverse("campus-class-resource-list")

    def titles(self, user, **params):
        self.client.force_authenticate(user)
        return {r["title"] for r in rows(self.client.get(self.url, params))}

    def test_student_sees_published_only(self):
        self.assertEqual(self.titles(self.sa1), {"Published"})

    def test_parent_sees_childs_section_published_only(self):
        self.assertEqual(self.titles(self.parent), {"Published"})

    def test_student_of_other_section_sees_nothing_and_detail_is_404(self):
        self.assertEqual(self.titles(self.sb), set())
        self.client.force_authenticate(self.sb)
        self.assertEqual(self.client.get(reverse("campus-class-resource-detail", args=[self.pub.id])).status_code, 404)

    def test_managers_and_poster_see_drafts(self):
        self.assertEqual(self.titles(self.teacher), {"Published", "Draft"})
        self.assertEqual(self.titles(self.admin), {"Published", "Draft"})

    def test_draft_detail_hidden_from_student(self):
        self.client.force_authenticate(self.sa1)
        self.assertEqual(self.client.get(reverse("campus-class-resource-detail", args=[self.draft.id])).status_code, 404)

    def test_filters(self):
        self.make_resource(folder=self.folder, title="Video", kind="video", external_url="https://youtu.be/x", is_pinned=True)
        self.assertEqual(self.titles(self.sa1, kind="video"), {"Video"})
        self.assertEqual(self.titles(self.sa1, q="publ"), {"Published"})
        self.assertEqual(self.titles(self.sa1, pinned="1"), {"Video"})
        self.assertEqual(self.titles(self.sa1, folder=str(self.folder.id)), {"Published", "Video"})

    def test_parent_asks_by_child_id(self):
        self.assertEqual(self.titles(self.parent, student=str(self.sa1.id)), {"Published"})
        # someone else's child -> nothing leaks
        self.assertEqual(self.titles(self.parent, student=str(self.sb.id)), set())

    def test_garbage_ids_are_a_400_not_a_500(self):
        self.client.force_authenticate(self.sa1)
        self.assertEqual(self.client.get(self.url, {"folder": "not-a-uuid"}).status_code, 400)
        self.assertEqual(self.client.get(self.url, {"student": "not-a-uuid"}).status_code, 400)

    def test_pinned_first(self):
        self.make_resource(folder=self.folder, title="Pinned", is_pinned=True)
        self.client.force_authenticate(self.sa1)
        first = rows(self.client.get(self.url))[0]
        self.assertEqual(first["title"], "Pinned")


# ===========================================================================
# Open tracking + viewers
# ===========================================================================

class OpenAndViewersTests(ResourceBase):
    def setUp(self):
        super().setUp()
        self.res = self.make_resource()
        self.open_url = reverse("campus-class-resource-open", args=[self.res.id])
        self.viewers_url = reverse("campus-class-resource-viewers", args=[self.res.id])

    def test_student_open_counts_once_per_user_and_bumps_view_count(self):
        self.client.force_authenticate(self.sa1)
        resp = self.client.post(self.open_url)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["viewed_by_me"])
        self.assertIsNone(resp.data["opened_by_count"])  # students never see counts
        self.client.post(self.open_url)
        row = ClassResourceView.objects.get(resource=self.res, user=self.sa1)
        self.assertEqual(row.view_count, 2)
        self.assertEqual(ClassResourceView.objects.filter(resource=self.res).count(), 1)

    def test_parent_open_is_counted(self):
        self.client.force_authenticate(self.parent)
        self.assertEqual(self.client.post(self.open_url).status_code, 200)
        self.assertTrue(ClassResourceView.objects.filter(resource=self.res, user=self.parent).exists())

    def test_teacher_open_is_not_counted(self):
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.client.post(self.open_url).status_code, 200)
        self.assertFalse(ClassResourceView.objects.exists())

    def test_open_of_hidden_resource_is_404(self):
        self.client.force_authenticate(self.sb)
        self.assertEqual(self.client.post(self.open_url).status_code, 404)
        draft = self.make_resource(title="d", is_published=False)
        self.client.force_authenticate(self.sa1)
        self.assertEqual(self.client.post(reverse("campus-class-resource-open", args=[draft.id])).status_code, 404)

    def test_viewers_managers_only_and_lists_who_has_not_opened(self):
        self.client.force_authenticate(self.sa1)
        self.client.post(self.open_url)
        self.assertEqual(self.client.get(self.viewers_url).status_code, 403)
        self.client.force_authenticate(self.teacher)
        resp = self.client.get(self.viewers_url)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["opened_count"], 1)
        self.assertEqual(resp.data["opened"][0]["user"]["username"], "sa1")
        self.assertEqual([u["username"] for u in resp.data["not_opened"]], ["sa2"])
        self.assertEqual(resp.data["not_opened_count"], 1)

    def test_opened_by_count_visible_to_manager_in_list(self):
        self.client.force_authenticate(self.sa1)
        self.client.post(self.open_url)
        self.client.force_authenticate(self.sa2)
        self.client.post(self.open_url)
        self.client.force_authenticate(self.teacher)
        got = rows(self.client.get(reverse("campus-class-resource-list")))
        self.assertEqual(got[0]["opened_by_count"], 2)


# ===========================================================================
# Push
# ===========================================================================

class PushTests(ResourceBase):
    def setUp(self):
        super().setUp()
        self.folder = self.make_folder()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_push_goes_to_students_and_parents_not_the_poster(self, notify, devices):
        res = self.make_resource(folder=self.folder, title="Quadratics")
        out = campus_tasks.send_class_resource_posted(str(res.pk))
        self.assertGreater(out["sent"], 0)
        notify.assert_called_once()
        kw = notify.call_args.kwargs
        self.assertEqual(kw["notif_type"], NotifTypes.CAMPUS_RESOURCE_POSTED)
        self.assertTrue(kw["push"])
        self.assertEqual({u.pk for u in kw["users"]}, {self.sa1.pk, self.sa2.pk, self.parent.pk})
        self.assertNotIn(self.teacher.pk, {u.pk for u in kw["users"]})
        self.assertNotIn(self.sb.pk, {u.pk for u in kw["users"]})  # other section
        data = kw["data"]
        self.assertEqual(data["resource_id"], str(res.pk))
        self.assertEqual(data["folder_id"], str(self.folder.pk))
        self.assertEqual(data["section_id"], str(self.secA.id))
        self.assertEqual(data["subject_id"], str(self.math.id))
        self.assertIn("Maths", kw["title"])
        self.assertIn("Quadratics", kw["body"])
        self.assertEqual(devices.call_count, 2)  # loginless parent devices, once per student
        res.refresh_from_db()
        self.assertIsNotNone(res.notified_at)

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_never_pushed_twice(self, notify, devices):
        res = self.make_resource(folder=self.folder)
        campus_tasks.send_class_resource_posted(str(res.pk))
        again = campus_tasks.send_class_resource_posted(str(res.pk))
        self.assertEqual(again, {"sent": 0})
        notify.assert_called_once()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_draft_deleted_or_hidden_folder_sends_nothing(self, notify, devices):
        draft = self.make_resource(folder=self.folder, title="d", is_published=False)
        gone = self.make_resource(folder=self.folder, title="g", is_active=False)
        self.assertEqual(campus_tasks.send_class_resource_posted(str(draft.pk)), {"sent": 0})
        self.assertEqual(campus_tasks.send_class_resource_posted(str(gone.pk)), {"sent": 0})
        hidden_folder = self.make_folder(name="Hidden")
        in_hidden = self.make_resource(folder=hidden_folder, title="h")
        ResourceFolder.objects.filter(pk=hidden_folder.pk).update(is_active=False)
        self.assertEqual(campus_tasks.send_class_resource_posted(str(in_hidden.pk)), {"sent": 0})
        notify.assert_not_called()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_published_later_draft_is_pushed_then(self, notify, devices):
        res = self.make_resource(folder=self.folder, is_published=False)
        self.assertEqual(campus_tasks.send_class_resource_posted(str(res.pk)), {"sent": 0})
        ClassResource.objects.filter(pk=res.pk).update(is_published=True)
        self.assertGreater(campus_tasks.send_class_resource_posted(str(res.pk))["sent"], 0)

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_burst_of_uploads_goes_quiet_after_the_limit(self, notify, devices):
        with override_settings(CAMPUS_RESOURCE_PUSH_BURST_LIMIT=2):
            ids = [self.make_resource(folder=self.folder, title=f"r{i}").pk for i in range(4)]
            results = [campus_tasks.send_class_resource_posted(str(pk)) for pk in ids]
        self.assertEqual(notify.call_count, 2)
        self.assertEqual(results[2].get("skipped"), "burst")
        self.assertEqual(results[3].get("skipped"), "burst")
        # the quiet ones are still claimed, so they can never push later either
        self.assertFalse(ClassResource.objects.filter(pk__in=ids, notified_at__isnull=True).exists())

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_burst_is_per_folder(self, notify, devices):
        other = self.make_folder(name="Other")
        with override_settings(CAMPUS_RESOURCE_PUSH_BURST_LIMIT=1):
            a = self.make_resource(folder=self.folder, title="a")
            b = self.make_resource(folder=other, title="b")
            campus_tasks.send_class_resource_posted(str(a.pk))
            campus_tasks.send_class_resource_posted(str(b.pk))
        self.assertEqual(notify.call_count, 2)

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify", side_effect=RuntimeError("boom"))
    def test_push_failure_never_raises_out_of_the_task(self, notify, devices):
        res = self.make_resource(folder=self.folder)
        self.assertEqual(campus_tasks.send_class_resource_posted(str(res.pk)), {"sent": 0})

    def test_notif_type_is_in_core_and_in_the_classroom_category(self):
        from core.models import Notification

        nt = Notification.NotifType
        self.assertEqual(NotifTypes.CAMPUS_RESOURCE_POSTED, nt.CAMPUS_RESOURCE_POSTED.value)
        self.assertLessEqual(len(nt.CAMPUS_RESOURCE_POSTED.value), 30)  # Notification.notif_type max_length
        self.assertEqual(Notification.category_for(nt.CAMPUS_RESOURCE_POSTED), "classroom")


# ===========================================================================
# Copy to other sections
# ===========================================================================

class CopyTests(ResourceBase):
    def setUp(self):
        super().setUp()
        self.root = self.make_folder(name="Algebra")
        self.child = self.make_folder(name="Ch 3", parent=self.root)
        resp, _ = self.post_file(self.teacher, self.child, pdf("worksheet.pdf"), title="Worksheet 1", description="do it")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.src = ClassResource.objects.get(pk=resp.data["id"])

    def copy(self, user, resource=None, sections=None):
        resource = resource or self.src
        self.client.force_authenticate(user)
        body = {"sections": [str(s.id) for s in (sections if sections is not None else [self.secB])]}
        with mock.patch("campus.resources.dispatch_after_commit") as dispatch:
            resp = self.client.post(reverse("campus-class-resource-copy-to-sections", args=[resource.id]), body, format="json")
        return resp, dispatch

    def test_admin_copies_with_folder_path_file_and_push(self):
        resp, dispatch = self.copy(self.admin)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["skipped"], [])
        self.assertEqual(len(resp.data["created"]), 1)
        dup = ClassResource.objects.get(pk=resp.data["created"][0]["id"])
        self.assertEqual(dup.section_id, self.secB.id)
        self.assertEqual(dup.subject_id, self.math.id)
        self.assertEqual(dup.kind, "pdf")
        self.assertEqual(dup.title, "Worksheet 1")
        self.assertEqual(dup.description, "do it")
        self.assertEqual(dup.posted_by_id, self.admin.id)
        self.assertEqual(dup.original_name, "worksheet.pdf")
        self.assertEqual(dup.file_size, self.src.file_size)
        self.assertFalse(dup.is_pinned)
        # the file is a DUPLICATE in storage, never shared with the source
        self.assertTrue(dup.file)
        self.assertNotEqual(dup.file.name, self.src.file.name)
        with dup.file.open("rb") as handle:
            self.assertEqual(handle.read(), b"%PDF-1.4 test")
        # the folder path was recreated in the target section
        target_child = dup.folder
        self.assertEqual(target_child.name, "Ch 3")
        self.assertEqual(target_child.parent.name, "Algebra")
        self.assertEqual((target_child.section_id, target_child.subject_id), (self.secB.id, self.math.id))
        # published copy -> its own push
        dispatch.assert_called_once_with(campus_tasks.send_class_resource_posted, str(dup.pk))

    def test_replacing_the_source_file_does_not_break_the_copy(self):
        resp, _ = self.copy(self.admin)
        dup = ClassResource.objects.get(pk=resp.data["created"][0]["id"])
        self.client.force_authenticate(self.teacher)
        patch = self.client.patch(
            reverse("campus-class-resource-detail", args=[self.src.id]),
            {"file": pdf("v2.pdf")}, format="multipart",
        )
        self.assertEqual(patch.status_code, 200, patch.data)
        with dup.file.open("rb") as handle:  # storage object is still there
            self.assertEqual(handle.read(), b"%PDF-1.4 test")

    def test_second_copy_is_skipped_as_already_there(self):
        self.copy(self.admin)
        resp, dispatch = self.copy(self.admin)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["created"], [])
        self.assertEqual(resp.data["skipped"], [{"section": str(self.secB.id), "reason": "already_exists"}])
        dispatch.assert_not_called()
        self.assertEqual(ClassResource.objects.filter(section=self.secB).count(), 1)

    def test_reuses_existing_target_folders_by_name(self):
        existing_root = self.make_folder(section=self.secB, subject=self.math, name="algebra", user=self.other_teacher)
        resp, _ = self.copy(self.admin)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(
            ResourceFolder.objects.filter(section=self.secB, subject=self.math, parent__isnull=True).count(), 1
        )
        dup = ClassResource.objects.get(pk=resp.data["created"][0]["id"])
        self.assertEqual(dup.folder.parent_id, existing_root.pk)

    def test_link_resource_copy_keeps_the_url(self):
        link = self.make_resource(folder=self.root, title="Lecture", kind="video", external_url="https://youtu.be/abc")
        resp, _ = self.copy(self.admin, resource=link)
        self.assertEqual(resp.status_code, 201, resp.data)
        dup = ClassResource.objects.get(pk=resp.data["created"][0]["id"])
        self.assertEqual((dup.kind, dup.external_url, dup.file.name or ""), ("video", "https://youtu.be/abc", ""))
        self.assertEqual(dup.folder.name, "Algebra")
        self.assertIsNone(dup.folder.parent_id)

    def test_source_section_and_unknown_sections_are_skipped_with_reasons(self):
        self.client.force_authenticate(self.admin)
        body = {"sections": [str(self.secA.id), str(uuid.uuid4()), "garbage"]}
        with mock.patch("campus.resources.dispatch_after_commit"):
            resp = self.client.post(reverse("campus-class-resource-copy-to-sections", args=[self.src.id]), body, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        reasons = {s["reason"] for s in resp.data["skipped"]}
        self.assertEqual(reasons, {"same_section", "section_not_found"})
        self.assertEqual(resp.data["created"], [])

    def test_teacher_cannot_copy_into_a_section_where_they_do_not_teach_the_subject(self):
        resp, dispatch = self.copy(self.teacher)  # approved for secA only
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["skipped"], [{"section": str(self.secB.id), "reason": "not_allowed"}])
        dispatch.assert_not_called()
        self.assertFalse(ClassResource.objects.filter(section=self.secB).exists())
        self.assertFalse(ResourceFolder.objects.filter(section=self.secB).exists())

    def test_teacher_copies_to_a_sibling_section_they_teach(self):
        from .models import Section, SubjectTeacherassigments

        sec_a2 = Section.objects.create(school_class=self.cls, name="A2")
        SubjectTeacherassigments.objects.create(
            section=sec_a2, subject=self.math, staff=self.t_staff, status=SubjectTeacherassigments.Status.APPROVED
        )
        resp, _ = self.copy(self.teacher, sections=[sec_a2])
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(len(resp.data["created"]), 1)
        self.assertEqual(resp.data["skipped"], [])

    def test_students_parents_and_other_teachers_cannot_copy(self):
        for user in (self.sa1, self.parent, self.other_teacher):
            resp, dispatch = self.copy(user)
            self.assertIn(resp.status_code, (403, 404), user.username)
            dispatch.assert_not_called()
        self.assertFalse(ClassResource.objects.filter(section=self.secB).exists())

    def test_validation(self):
        url = reverse("campus-class-resource-copy-to-sections", args=[self.src.id])
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"sections": []}, format="json").status_code, 400)
        many = [str(uuid.uuid4()) for _ in range(11)]
        self.assertEqual(self.client.post(url, {"sections": many}, format="json").status_code, 400)

    def test_draft_copy_stays_a_draft_and_is_not_pushed(self):
        ClassResource.objects.filter(pk=self.src.pk).update(is_published=False)
        resp, dispatch = self.copy(self.admin)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertFalse(resp.data["created"][0]["is_published"])
        dispatch.assert_not_called()

    def test_target_folder_full_is_reported(self):
        with mock.patch("campus.resources.MAX_RESOURCES_PER_FOLDER", 0):
            resp, _ = self.copy(self.admin)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["skipped"], [{"section": str(self.secB.id), "reason": "folder_full"}])
