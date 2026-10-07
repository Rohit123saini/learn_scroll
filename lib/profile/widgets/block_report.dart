// lib/profile/widgets/block_report.dart
//
// Shared "Block" / "Report" UI, so every place that can block someone
// (profile ⋮, comment ⋮, story viewers, notifications) behaves identically:
//
//   showBlockDialog()  — confirm + two optional checkboxes
//                        ("Also report this account", "Also block new accounts
//                        they may create").
//   blockUserFlow()    — dialog -> POST /profile/blocked-users/ -> snackbars.
//                        Returns true if the block went through.
//   showReportSheet()  — reason list -> POST /profile/reports/.
//
// Nothing here tells the other person anything — block/report are silent.

import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../api_service.dart';

/// What the person picked in the block dialog.
class BlockChoice {
  final bool blockNewAccounts;

  /// Backend reason code (spam, harassment, …) or null when not reporting.
  final String? reportReason;
  const BlockChoice({this.blockNewAccounts = false, this.reportReason});
}

/// Backend reason codes in display order.
const List<String> kReportReasonCodes = [
  'spam',
  'harassment',
  'hate',
  'nudity',
  'violence',
  'self_harm',
  'scam',
  'impersonation',
  'other',
];

String reportReasonLabel(AppLocalizations l10n, String code) {
  switch (code) {
    case 'spam':
      return l10n.reportReasonSpam;
    case 'harassment':
      return l10n.reportReasonHarassment;
    case 'hate':
      return l10n.reportReasonHate;
    case 'nudity':
      return l10n.reportReasonNudity;
    case 'violence':
      return l10n.reportReasonViolence;
    case 'self_harm':
      return l10n.reportReasonSelfHarm;
    case 'scam':
      return l10n.reportReasonScam;
    case 'impersonation':
      return l10n.reportReasonImpersonation;
    default:
      return l10n.reportReasonOther;
  }
}

/// Confirm dialog. Returns null when cancelled.
Future<BlockChoice?> showBlockDialog(BuildContext context, {required String username}) {
  final l10n = AppLocalizations.of(context)!;
  final name = username.isNotEmpty ? username : l10n.chatThisUser;
  bool alsoReport = false;
  bool newAccounts = false;
  String? reason;

  return showDialog<BlockChoice>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setLocal) {
        final canSubmit = !alsoReport || reason != null;
        return AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          title: Text(l10n.chatBlockTitle),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l10n.chatBlockBody(name)),
                const SizedBox(height: 8),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  dense: true,
                  value: newAccounts,
                  onChanged: (v) => setLocal(() => newAccounts = v ?? false),
                  title: Text(l10n.reportBlockNewAccounts),
                ),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  dense: true,
                  value: alsoReport,
                  onChanged: (v) => setLocal(() {
                    alsoReport = v ?? false;
                    if (!alsoReport) reason = null;
                  }),
                  title: Text(l10n.reportAlsoReport),
                ),
                if (alsoReport)
                  DropdownButtonFormField<String>(
                    isExpanded: true,
                    value: reason,
                    hint: Text(l10n.reportTitle),
                    items: [
                      for (final code in kReportReasonCodes)
                        DropdownMenuItem(value: code, child: Text(reportReasonLabel(l10n, code))),
                    ],
                    onChanged: (v) => setLocal(() => reason = v),
                  ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)),
            TextButton(
              onPressed: canSubmit
                  ? () => Navigator.pop(
                        ctx,
                        BlockChoice(blockNewAccounts: newAccounts, reportReason: alsoReport ? reason : null),
                      )
                  : null,
              child: Text(l10n.chatBlock, style: TextStyle(color: canSubmit ? Colors.red : null)),
            ),
          ],
        );
      },
    ),
  );
}

/// Full block flow. Returns true if the user ended up blocked.
Future<bool> blockUserFlow(
  BuildContext context, {
  required int userId,
  required String username,
}) async {
  final l10n = AppLocalizations.of(context)!;
  final choice = await showBlockDialog(context, username: username);
  if (choice == null || !context.mounted) return false;

  final messenger = ScaffoldMessenger.of(context);
  try {
    await ApiService.blockUser(
      userId,
      blockNewAccounts: choice.blockNewAccounts,
      reportReason: choice.reportReason,
    );
  } catch (e) {
    messenger.showSnackBar(SnackBar(content: Text(l10n.chatBlockFailed(e.toString()))));
    return false;
  }
  messenger.showSnackBar(SnackBar(
    content: Text(choice.reportReason != null
        ? '${l10n.chatUserBlocked} ${l10n.reportThanks}'
        : l10n.chatUserBlocked),
  ));
  return true;
}

/// Report sheet for an account / post / comment / story. Returns true when a
/// report was sent (or had already been sent).
Future<bool> showReportSheet(
  BuildContext context, {
  required String targetType,
  required String targetId,
}) async {
  final l10n = AppLocalizations.of(context)!;
  final reason = await showModalBottomSheet<String>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (ctx) => SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.of(ctx).size.height * 0.8),
        child: ListView(
          shrinkWrap: true,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
              child: Text(l10n.reportTitle, style: Theme.of(ctx).textTheme.titleMedium),
            ),
            for (final code in kReportReasonCodes)
              ListTile(
                title: Text(reportReasonLabel(l10n, code)),
                trailing: const Icon(Icons.chevron_right_rounded),
                onTap: () => Navigator.pop(ctx, code),
              ),
          ],
        ),
      ),
    ),
  );
  if (reason == null || !context.mounted) return false;

  final messenger = ScaffoldMessenger.of(context);
  try {
    final isNew = await ApiService.reportContent(targetType: targetType, targetId: targetId, reason: reason);
    messenger.showSnackBar(SnackBar(content: Text(isNew ? l10n.reportThanks : l10n.reportAlreadySent)));
    return true;
  } catch (e) {
    messenger.showSnackBar(SnackBar(content: Text(l10n.reportFailed(e.toString()))));
    return false;
  }
}
