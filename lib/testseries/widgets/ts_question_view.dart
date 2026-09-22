import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../config/testseries_config.dart';
import '../controllers/attempt_controller.dart';
import '../services/testseries_models.dart';
import 'ts_image_viewer.dart';

// ============================================================
// Ek question ka poora card: text, (optional) image, aur type ke hisaab
// se input. Saara state `TestAttemptController` me hai — ye widget
// stateless hai.
// ============================================================

class TsQuestionView extends StatelessWidget {
  final TestAttemptController controller;
  final TsQuestion question;
  final int index;

  const TsQuestionView({super.key, required this.controller, required this.question, required this.index});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final q = question;
    final c = controller;

    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(l10n.questionOf(index + 1, c.questions.length),
              style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: cs.primary)),
          const SizedBox(width: 8),
          if (c.isMarked(q.id)) Icon(Icons.flag_rounded, size: 14, color: lsTokens(context).warning),
          const Spacer(),
          Text(l10n.testAnsweredOf(c.answeredCount, c.answerableCount),
              style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
          const SizedBox(width: 8),
          LsStatusChip(label: l10n.marksShort(q.marks), color: cs.onSurfaceVariant),
        ]),
        const SizedBox(height: 10),
        Text(q.text, style: TextStyle(fontSize: 14, height: 1.5, color: cs.onSurface)),
        if (q.attachment != null) ...[
          const SizedBox(height: 10),
          TsNetworkImage(url: q.attachment, semanticLabel: l10n.tsQuestionImage),
        ],
        const SizedBox(height: 6),
        Text(_hint(l10n), style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
        const SizedBox(height: 12),
        ..._input(context, cs, l10n),
      ]),
    );
  }

  String _hint(AppLocalizations l10n) {
    switch (question.type) {
      case TsQuestionType.mcq:
        return l10n.answerHintSelectOne;
      case TsQuestionType.msq:
        return l10n.answerHintSelectMultiple;
      case TsQuestionType.list:
        return question.listMode == TsListMode.match ? l10n.answerHintMatch : l10n.answerHintArrange;
      case TsQuestionType.text:
        return l10n.answerHintText;
      case TsQuestionType.unknown:
        return '';
    }
  }

  List<Widget> _input(BuildContext context, ColorScheme cs, AppLocalizations l10n) {
    final q = question;
    final c = controller;

    switch (q.type) {
      case TsQuestionType.mcq:
        final selected = c.mcqSelection(q.id);
        return q.choices
            .map((o) => TsChoiceTile(
                  label: o.text,
                  selected: selected == o.id,
                  multi: false,
                  onTap: () => c.setMcq(q.id, o.id),
                ))
            .toList();

      case TsQuestionType.msq:
        final sel = c.msqSelection(q.id);
        return q.choices
            .map((o) => TsChoiceTile(
                  label: o.text,
                  selected: sel.contains(o.id),
                  multi: true,
                  onTap: () => c.toggleMsq(q.id, o.id),
                ))
            .toList();

      case TsQuestionType.list:
        if (q.listMode == TsListMode.match) return _matchInput(cs, l10n);
        return _orderInput(context, cs, l10n);

      case TsQuestionType.text:
        return _textInput(context, cs, l10n);

      case TsQuestionType.unknown:
        // Naya question type jo ye app version nahi jaanta — jhoota text
        // box dikha ke galat answer bhejne se behtar hai saaf message.
        return [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(Icons.system_update_alt_rounded, size: 17, color: lsTokens(context).warning),
            const SizedBox(width: 9),
            Expanded(
              child: Text(l10n.tsQuestionUnsupported,
                  style: TextStyle(fontSize: 12.5, height: 1.45, color: cs.onSurface)),
            ),
          ]),
        ];
    }
  }

  // ---------------- text + photo ----------------

  List<Widget> _textInput(BuildContext context, ColorScheme cs, AppLocalizations l10n) {
    final q = question;
    final c = controller;
    final file = c.fileOf(q.id);

    return [
      TextField(
        controller: c.textController(q.id),
        maxLines: 8,
        minLines: 4,
        textCapitalization: TextCapitalization.sentences,
        inputFormatters: [LengthLimitingTextInputFormatter(TsConfig.maxTextAnswerChars)],
        style: TextStyle(fontSize: 13.5, height: 1.5, color: cs.onSurface),
        decoration: InputDecoration(hintText: l10n.testTypeAnswerHint),
      ),
      const SizedBox(height: 10),
      if (file == null)
        LsOutlineButton(
          label: l10n.testAttachPhoto,
          icon: Icons.photo_camera_outlined,
          onPressed: () => tsPickPhoto(context, c, q.id),
        )
      else
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(12)),
          child: Row(children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: Image.file(file, width: 44, height: 44, fit: BoxFit.cover, cacheWidth: 132,
                  errorBuilder: (_, __, ___) => Icon(Icons.image_outlined, size: 20, color: cs.onSurfaceVariant)),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(file.path.split('/').last,
                  maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12, color: cs.onSurface)),
            ),
            IconButton(
              visualDensity: VisualDensity.compact,
              tooltip: l10n.tsRemovePhoto,
              icon: Icon(Icons.close_rounded, size: 18, color: cs.onSurfaceVariant),
              onPressed: () => c.removeFile(q.id),
            ),
          ]),
        ),
    ];
  }

  // ---------------- order ----------------

  List<Widget> _orderInput(BuildContext context, ColorScheme cs, AppLocalizations l10n) {
    final q = question;
    final c = controller;
    final order = c.orderOf(q);

    String label(String id) {
      for (final o in q.choices) {
        if (o.id == id) return o.text;
      }
      return id;
    }

    return [
      if (!c.orderTouched(q.id))
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Text(l10n.tsOrderNotArranged,
              style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: lsTokens(context).warning)),
        ),
      ReorderableListView(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        buildDefaultDragHandles: false,
        onReorder: (oldIndex, newIndex) => c.reorder(q, oldIndex, newIndex),
        children: [
          for (int i = 0; i < order.length; i++)
            Padding(
              key: ValueKey('${q.id}_${order[i]}'),
              padding: const EdgeInsets.only(bottom: 8),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
                decoration: BoxDecoration(
                  color: cs.surfaceVariant,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: cs.outlineVariant),
                ),
                child: Row(children: [
                  Text('${i + 1}', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.primary)),
                  const SizedBox(width: 12),
                  Expanded(child: Text(label(order[i]), style: TextStyle(fontSize: 12.5, color: cs.onSurface))),
                  ReorderableDragStartListener(
                    index: i,
                    child: Padding(
                      padding: const EdgeInsets.all(6), // 48dp touch target ke kareeb
                      child: Icon(Icons.drag_handle_rounded, size: 20, color: cs.onSurfaceVariant),
                    ),
                  ),
                ]),
              ),
            ),
        ],
      ),
    ];
  }

  // ---------------- match ----------------

  List<Widget> _matchInput(ColorScheme cs, AppLocalizations l10n) {
    final q = question;
    final c = controller;
    final pairs = c.matchPairs(q.id);
    final right = q.matchRight;

    return q.matchLeft.map((left) {
      final current = pairs[left.id];
      return Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: Row(children: [
          Expanded(
            flex: 5,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
              decoration: BoxDecoration(
                color: cs.surfaceVariant,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: cs.outlineVariant),
              ),
              child: Text(left.text, style: TextStyle(fontSize: 12.5, color: cs.onSurface)),
            ),
          ),
          const SizedBox(width: 8),
          Icon(Icons.arrow_forward_rounded, size: 15, color: cs.onSurfaceVariant),
          const SizedBox(width: 8),
          Expanded(
            flex: 5,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10),
              decoration: BoxDecoration(
                color: cs.surface,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: current != null ? cs.primary : cs.outlineVariant),
              ),
              child: DropdownButtonHideUnderline(
                child: DropdownButton<String>(
                  isExpanded: true,
                  value: current,
                  hint: Text(l10n.testMatchSelect, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                  icon: Icon(Icons.expand_more_rounded, size: 18, color: cs.onSurfaceVariant),
                  dropdownColor: cs.surface,
                  style: TextStyle(fontSize: 12.5, color: cs.onSurface),
                  items: [
                    // '' = pair hatao.
                    DropdownMenuItem<String>(
                      value: '',
                      child: Text('—', style: TextStyle(color: cs.onSurfaceVariant)),
                    ),
                    ...right.map((r) => DropdownMenuItem<String>(
                          value: r.id,
                          child: Text(r.text, maxLines: 1, overflow: TextOverflow.ellipsis),
                        )),
                  ],
                  onChanged: (v) {
                    if (v == null) return;
                    c.setMatch(q.id, left.id, v.isEmpty ? null : v);
                  },
                ),
              ),
            ),
          ),
        ]),
      );
    }).toList();
  }
}

// ============================================================
// Choice tile (mcq / msq)
// ============================================================

class TsChoiceTile extends StatelessWidget {
  final String label;
  final bool selected;
  final bool multi;
  final VoidCallback onTap;

  const TsChoiceTile({
    super.key,
    required this.label,
    required this.selected,
    required this.multi,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 9),
      child: Semantics(
        inMutuallyExclusiveGroup: !multi,
        checked: selected,
        button: true,
        label: label,
        excludeSemantics: true,
        onTap: onTap,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () {
            HapticFeedback.selectionClick();
            onTap();
          },
          child: Container(
            constraints: const BoxConstraints(minHeight: 48),
            padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 13),
            decoration: BoxDecoration(
              color: selected ? cs.primary.withOpacity(.12) : cs.surfaceVariant,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: selected ? cs.primary : cs.outlineVariant),
            ),
            child: Row(children: [
              Icon(
                multi
                    ? (selected ? Icons.check_box_rounded : Icons.check_box_outline_blank_rounded)
                    : (selected ? Icons.radio_button_checked_rounded : Icons.radio_button_unchecked_rounded),
                size: 19,
                color: selected ? cs.primary : cs.onSurfaceVariant,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(label,
                    style: TextStyle(
                        fontSize: 13,
                        height: 1.4,
                        fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                        color: cs.onSurface)),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}

// ============================================================
// Photo attach: camera + gallery, size guard, permission errors visible.
// ============================================================

Future<void> tsPickPhoto(BuildContext context, TestAttemptController c, String questionId) async {
  final l10n = AppLocalizations.of(context)!;

  final source = await showModalBottomSheet<ImageSource>(
    context: context,
    backgroundColor: Theme.of(context).colorScheme.surface,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => SafeArea(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        const SizedBox(height: 8),
        ListTile(
          leading: const Icon(Icons.photo_camera_outlined),
          title: Text(l10n.tsFromCamera),
          onTap: () => Navigator.pop(ctx, ImageSource.camera),
        ),
        ListTile(
          leading: const Icon(Icons.photo_library_outlined),
          title: Text(l10n.tsFromGallery),
          onTap: () => Navigator.pop(ctx, ImageSource.gallery),
        ),
        const SizedBox(height: 8),
      ]),
    ),
  );
  if (source == null) return;

  try {
    final XFile? img = await ImagePicker().pickImage(
      source: source,
      imageQuality: TsConfig.photoQuality,
      maxWidth: TsConfig.maxPhotoDimension,
      maxHeight: TsConfig.maxPhotoDimension,
    );
    if (img == null) return;
    final file = File(img.path);
    if (await file.length() > TsConfig.maxPhotoBytes) {
      if (context.mounted) {
        lsSnack(context, l10n.tsPhotoTooLarge(TsConfig.maxPhotoBytes ~/ (1024 * 1024)), error: true);
      }
      return;
    }
    c.setFile(questionId, file);
  } catch (_) {
    // Permission denied / camera unavailable — pehle ye silently swallow hota tha.
    if (context.mounted) lsSnack(context, l10n.tsPhotoError, error: true);
  }
}
