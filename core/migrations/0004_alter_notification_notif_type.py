# Generated manually (hand-written to match Django 6.0.6's own
# generated format) — TASK 1: adds the choices-only AlterField for
# `notif_type` covering every NotifType value that accumulated since
# 0003_initial without ever getting its own migration (POST_LIKED/
# POST_COMMENTED, the campus block, the testseries/assignment block),
# plus this pass's 5 new FOLLOW_* / *_FROM_FOLLOWED values. See
# core/models.py's module docstring (point 5) for the full "what's new"
# writeup.
#
# NOTE: this is a state-only change. `choices=` is not a database
# constraint on a plain CharField — no column type, length, index, or
# CHECK constraint changes, so this migration issues zero SQL against
# Postgres/SQLite. It exists purely so Django's migration state (and
# `makemigrations --check` in CI) matches the model as written, the
# same reasoning already given inline in core/models.py for every prior
# "pure addition, no migration needed for the schema" choices change —
# except this one *is* being recorded, to stop the drift from
# compounding turn after turn.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='notif_type',
            field=models.CharField(choices=[('join_request_received', 'Join Request Received'), ('join_request_accepted', 'Join Request Accepted'), ('join_request_rejected', 'Join Request Rejected'), ('pass_refunded', 'Pass Refunded'), ('session_reminder', 'Session Reminder'), ('assignment_graded', 'Assignment Graded'), ('query_answered', 'Doubt Answered'), ('certificate_issued', 'Certificate Issued'), ('waitlist_promoted', 'Waitlist Promoted'), ('classroom_flagged', 'Classroom Flagged'), ('notice_posted', 'Notice Posted'), ('session_live', 'Class Started'), ('session_cancelled', 'Session Cancelled'), ('assignment_posted', 'New Assignment'), ('submission_received', 'New Submission'), ('staff_added', 'Added As Staff'), ('review_posted', 'New Review'), ('report_reviewed', 'Report Reviewed'), ('withdrawal_approved', 'Withdrawal Approved'), ('withdrawal_rejected', 'Withdrawal Rejected'), ('withdrawal_paid', 'Withdrawal Paid'), ('classroom_shared', 'Classroom Shared With You'), ('pass_gift_received', 'Pass Gift Received'), ('pass_gift_claimed', 'Pass Gift Claimed'), ('pass_auto_renewed', 'Pass Auto-Renewed'), ('auto_renew_failed', 'Auto-Renewal Failed'), ('pass_gift_expired', 'Gift Expired & Refunded'), ('generic', 'Generic'), ('chat_message', 'New Message'), ('mention', 'You Were Mentioned'), ('incoming_call', 'Incoming Call'), ('post_liked', 'Post Liked'), ('post_commented', 'Post Commented'), ('campus_session_scheduled', 'Campus Session Scheduled'), ('campus_session_live', 'Campus Session Live'), ('low_attendance_alert', 'Low Attendance Alert'), ('assignment_posted_campus', 'New Campus Assignment'), ('assignment_due_reminder', 'Assignment Due Reminder'), ('result_published', 'Result Published'), ('fee_due_reminder', 'Fee Due Reminder'), ('staff_assignment_approved', 'Staff Assignment Approved'), ('staff_assignment_rejected', 'Staff Assignment Rejected'), ('testseries_posted', 'New Test Series'), ('testseries_checked', 'Test Series Checked'), ('testseries_payout_released', 'Test Series Payout Released'), ('assignment_due_soon', 'Assignment Due Soon'), ('campus_reward_earned', 'Campus Reward Earned'), ('testseries_review_received', 'New Test Series Review'), ('testseries_query_received', 'New Test Series Query'), ('testseries_query_answered', 'Test Series Query Answered'), ('follow_request_received', 'Follow Request Received'), ('follow_request_accepted', 'Follow Request Accepted'), ('new_post_from_followed', 'New Post From Someone You Follow'), ('classroom_created_by_followed', 'New Classroom From Someone You Follow'), ('testseries_created_by_followed', 'New Test Series From Someone You Follow')], db_index=True, default='generic', max_length=30),
        ),
    ]