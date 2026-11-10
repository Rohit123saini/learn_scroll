# `assigments` → `assignments` spelling cleanup (T3.10)

## Done (safe, no data change)
* Python package directory `assigments/` → `assignments/`; every `from assigments.… import` / dotted string
  (`assigments.tasks`, `include("assigments.urls")`, `patch("assigments.tasks…")`) updated; checked with
  `python deploy/tools/check_imports.py`.
* `AssignmentsConfig` sets `name = "assignments"` **and `label = "assigments"`**. The label is what Django uses for
  table names (`assigments_*`), `'assigments.Model'` FK strings, migration dependencies, content types and
  `CONFIG_DRIFT_APPS` – so none of that changes and **no migration is needed**.
* Stray duplicate `assigments/send_assignment_due_reminders.py` (outside `management/commands/`, never runnable) removed.
* `INSTALLED_APPS` now lists `'assignments'`.
* Run tests with the PACKAGE path now: `python manage.py test assignments` (the label `assigments` no longer matches a module).

## Deliberately NOT done yet (needs a coordinated release – the typo is part of the wire/data format)
| Where the typo lives | Why it can't be a blind rename |
|---|---|
| URL prefix `assigments/…`, router paths `assigmentss`, `assigments-submissions`, `class-teacher-assigmentss` | The shipped Flutter app calls these (`campus_service.dart`). Add the correct paths as aliases, ship an app version using them, retire the old ones later. |
| Notification types `assigments_posted`, `assigments_graded`, `staff_assigments_approved` … | Stored in `core_notification.notif_type` rows and matched by the app (`notifications_screen.dart`). Needs `UPDATE` migration + app change. |
| Search source id `'assigments'`, JSON keys such as `assigments_title`, query param `assigments` | API contract with the app. |
| Model / class names `assigments`, `assigmentsSource`, `assigmentsSubmission`, `Teacherassigments` … | `RenameModel` + table renames (`db_table`) on live data. |
| App label `assigments` | Would rename every table + rewrite the `django_migrations` / `django_content_type` rows. |

Recommended order: (1) accept both spellings on the API (aliases / serializer aliases), (2) ship the app using the
correct names, (3) one data migration for notif types, (4) `RenameModel`s, (5) finally the label (with
`RunSQL` table renames). Every step is independently deployable.
