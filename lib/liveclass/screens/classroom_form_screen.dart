// lib/liveclass/screens/classroom_form_screen.dart
//
// Screen 3 — Create / Edit Classroom (see LIVECLASS_SCREEN_ARCHITECTURE.md
// §3). Teacher-only.
//
// One screen, two modes:
//   - Create: widget.existing == null  -> POST classrooms/
//   - Edit:   widget.existing != null  -> PATCH classrooms/{id}/
// Cover image goes through as multipart (FormData) only when the teacher
// actually picked a new one — see ClassroomApi.create/update.
//
// Edit mode also carries the two lifecycle actions this section of the
// architecture doc calls out:
//   - Close Classroom  -> POST classrooms/{id}/close/  (refunds every
//     active paid pass, then deactivates — the safe way to stop early)
//   - Delete Classroom -> DELETE classrooms/{id}/      (only once 30+ days
//     old AND no active paid pass outstanding; backend 400s otherwise and
//     the error message is surfaced as-is so the teacher knows to Close
//     instead)

import 'dart:io';
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:file_selector/file_selector.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import 'pass_management_screen.dart';
import 'schedule_manager_screen.dart';

const List<String> _kLanguages = [
  'English',
  'Hindi',
  'Hinglish',
  'Tamil',
  'Telugu',
  'Bengali',
  'Marathi',
  'Gujarati',
  'Kannada',
  'Malayalam',
  'Punjabi',
];

InputDecoration _decoration(String label, {String? hint}) => InputDecoration(
      labelText: label,
      hintText: hint,
      filled: true,
      fillColor: Colors.white,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
    );

// ===========================================================================
// SCREEN
// ===========================================================================
class ClassroomFormScreen extends StatefulWidget {
  /// Null = create mode. Non-null = editing this classroom.
  final Classroom? existing;
  const ClassroomFormScreen({super.key, this.existing});

  @override
  State<ClassroomFormScreen> createState() => _ClassroomFormScreenState();
}

class _ClassroomFormScreenState extends State<ClassroomFormScreen> {
  final _formKey = GlobalKey<FormState>();

  final _titleCtrl = TextEditingController();
  final _subjectCtrl = TextEditingController();
  final _descriptionCtrl = TextEditingController();
  final _organisationNameCtrl = TextEditingController();
  final _maxParticipantsCtrl = TextEditingController(text: '100');

  String _classroomType = ClassroomType.individual;
  String _language = 'English';

  bool _whiteboardEnabled = true;
  bool _screenShareEnabled = true;
  bool _chatEnabled = true;
  bool _recordingEnabled = true;

  // FIX (backend cross-check): ClassroomSerializer lists referral_enabled /
  // referral_commission_percent as teacher-writable (same as every other
  // field in this form) but this screen never surfaced them — Refer &
  // Earn could only ever sit at its model default (off, 0%).
  bool _referralEnabled = false;
  final _referralCommissionCtrl = TextEditingController(text: '0');

  XFile? _pickedCover; // newly picked, not yet uploaded
  String? _existingCoverUrl;

  bool get _isEdit => widget.existing != null;

  bool _saving = false;
  bool _closing = false;
  bool _deleting = false;
  bool get _busy => _saving || _closing || _deleting;

  @override
  void initState() {
    super.initState();
    final c = widget.existing;
    if (c != null) {
      _titleCtrl.text = c.title;
      _subjectCtrl.text = c.subject;
      _descriptionCtrl.text = c.description;
      _organisationNameCtrl.text = c.organisationName;
      _maxParticipantsCtrl.text = c.maxParticipants.toString();
      _classroomType = c.classroomType;
      _language = _kLanguages.contains(c.language) ? c.language : 'English';
      _whiteboardEnabled = c.whiteboardEnabled;
      _screenShareEnabled = c.screenShareEnabled;
      _chatEnabled = c.chatEnabled;
      _recordingEnabled = c.recordingEnabled;
      _referralEnabled = c.referralEnabled;
      _referralCommissionCtrl.text = c.referralCommissionPercent.toStringAsFixed(
        c.referralCommissionPercent == c.referralCommissionPercent.roundToDouble() ? 0 : 1,
      );
      _existingCoverUrl = c.coverImage;
    }
  }

  @override
  void dispose() {
    _titleCtrl.dispose();
    _subjectCtrl.dispose();
    _descriptionCtrl.dispose();
    _organisationNameCtrl.dispose();
    _maxParticipantsCtrl.dispose();
    _referralCommissionCtrl.dispose();
    super.dispose();
  }

  // -------------------------------------------------------------------
  // Cover image
  // -------------------------------------------------------------------
  Future<void> _pickCover() async {
    const typeGroup = XTypeGroup(label: 'images', extensions: ['jpg', 'jpeg', 'png', 'webp']);
    try {
      final file = await openFile(acceptedTypeGroups: [typeGroup]);
      if (!mounted) return;
      if (file != null) setState(() => _pickedCover = file);
    } catch (_) {
      _snack('Could not select image.');
    }
  }

  // -------------------------------------------------------------------
  // Save (create or update)
  // -------------------------------------------------------------------
  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    if (_classroomType == ClassroomType.organisation && _organisationNameCtrl.text.trim().isEmpty) {
      _snack('Organisation name is required for an organisation classroom.');
      return;
    }
    final maxParticipants = int.tryParse(_maxParticipantsCtrl.text.trim());
    if (maxParticipants == null || maxParticipants < 1) {
      _snack('Max participants must be a valid number (at least 1).');
      return;
    }
    // referral_commission_percent has a 0-100 MinValueValidator/
    // MaxValueValidator on the model field (serializers.py's comment on
    // this field) — checked client-side too so a bad value doesn't just
    // round-trip as a 400 from the backend.
    final referralCommission = double.tryParse(_referralCommissionCtrl.text.trim()) ?? 0;
    if (_referralEnabled && (referralCommission < 0 || referralCommission > 100)) {
      _snack('Referral commission must be between 0 and 100.');
      return;
    }

    setState(() => _saving = true);
    final draft = Classroom(
      id: widget.existing?.id ?? 0,
      classroomType: _classroomType,
      organisationName: _classroomType == ClassroomType.organisation ? _organisationNameCtrl.text.trim() : '',
      title: _titleCtrl.text.trim(),
      subject: _subjectCtrl.text.trim(),
      description: _descriptionCtrl.text.trim(),
      language: _language,
      whiteboardEnabled: _whiteboardEnabled,
      screenShareEnabled: _screenShareEnabled,
      chatEnabled: _chatEnabled,
      recordingEnabled: _recordingEnabled,
      maxParticipants: maxParticipants,
      referralEnabled: _referralEnabled,
      referralCommissionPercent: _referralEnabled ? referralCommission : 0,
      isActive: widget.existing?.isActive ?? true,
    );

    try {
      final Classroom saved;
      if (_isEdit) {
        saved = await LiveClassApi.classrooms.update(widget.existing!.id, draft, coverImagePath: _pickedCover?.path);
      } else {
        saved = await LiveClassApi.classrooms.create(draft, coverImagePath: _pickedCover?.path);
      }
      if (!mounted) return;
      if (_isEdit) {
        _snack('Classroom updated.');
        Navigator.pop(context, saved);
        return;
      }
      // NOTE (fix): a freshly created classroom has zero passes, so no
      // student can ever request to join it — nothing prompted the teacher
      // to set a price. Push straight into Pass Management with the create
      // sheet auto-opened instead of just popping back.
      _snack('Classroom created — now set up pricing.');
      await Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => PassManagementScreen(
            classroomId: saved.id,
            classroomTitle: saved.title,
            autoOpenCreate: true,
          ),
        ),
      );
      // NOTE (fix): same gap as passes — a fresh classroom had no recurring
      // schedule either, so no ClassSession ever got auto-generated and
      // there was nothing for a student (or the teacher themselves, post
      // the "Enter Class" fix) to ever join. ScheduleManagerScreen already
      // exists and is fully built (reachable today only from the detail
      // screen's Schedule tab → "Manage") — chain into it here too.
      if (!mounted) return;
      _snack('Now set up the class timing/schedule.');
      await Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => ScheduleManagerScreen(classroomId: saved.id, canManage: true),
        ),
      );
      // Teacher done with pricing + schedule — now pop the form with the
      // created classroom, same contract as before for whoever pushed this
      // screen (e.g. refreshing "My Classrooms" list).
      if (mounted) Navigator.pop(context, saved);
    } on LiveClassApiException catch (e) {
      _snack(e.message);
    } catch (_) {
      _snack('Could not save, please try again.');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  // -------------------------------------------------------------------
  // Close classroom (edit mode only)
  // -------------------------------------------------------------------
  Future<void> _confirmClose() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Close Classroom?'),
        content: const Text(
            'All active students will be refunded for their paid passes and the classroom will be deactivated. This cannot be undone.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Close Classroom', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _closing = true);
    try {
      final res = await LiveClassApi.classrooms.close(widget.existing!.id);
      if (!mounted) return;
      _snack('Classroom closed. ${res['passes_refunded'] ?? 0} pass(es) refunded.');
      Navigator.pop(context, 'closed');
    } on LiveClassApiException catch (e) {
      _snack(e.message);
    } catch (_) {
      _snack('Could not close the classroom.');
    } finally {
      if (mounted) setState(() => _closing = false);
    }
  }

  // -------------------------------------------------------------------
  // Delete classroom (edit mode only — soft delete, 30-day/no-active-pass
  // gated server-side; backend's 400 message explains why if it's blocked)
  // -------------------------------------------------------------------
  Future<void> _confirmDelete() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Delete Classroom?'),
        content: const Text(
            'This is only allowed for classrooms older than 30 days with no active paid pass outstanding. Otherwise, use "Close Classroom" instead.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _deleting = true);
    try {
      await LiveClassApi.classrooms.delete(widget.existing!.id);
      if (!mounted) return;
      _snack('Classroom deleted.');
      Navigator.pop(context, 'deleted');
    } on LiveClassApiException catch (e) {
      // Most likely the can_be_deleted() 400 — surface it verbatim and
      // point at Close as the alternative, matching the architecture doc.
      _snack(e.message);
    } catch (_) {
      _snack('Could not delete the classroom.');
    } finally {
      if (mounted) setState(() => _deleting = false);
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(
        _isEdit ? 'Edit Classroom' : 'Create Classroom',
        actions: [
          if (_isEdit)
            PopupMenuButton<String>(
              enabled: !_busy,
              onSelected: (v) {
                if (v == 'close') _confirmClose();
                if (v == 'delete') _confirmDelete();
              },
              itemBuilder: (_) => const [
                PopupMenuItem(value: 'close', child: Text('Close Classroom')),
                PopupMenuItem(value: 'delete', child: Text('Delete Classroom')),
              ],
            ),
        ],
      ),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
          children: [
            _coverPicker(),
            const SizedBox(height: 20),
            _sectionLabel('Classroom Type'),
            const SizedBox(height: 10),
            _typeToggle(),
            if (_classroomType == ClassroomType.organisation) ...[
              const SizedBox(height: 14),
              TextFormField(
                controller: _organisationNameCtrl,
                decoration: _decoration('Organisation Name'),
                validator: (v) {
                  if (_classroomType == ClassroomType.organisation && (v == null || v.trim().isEmpty)) {
                    return 'Organisation name is required';
                  }
                  return null;
                },
              ),
            ],
            const SizedBox(height: 20),
            _sectionLabel('Basic Info'),
            const SizedBox(height: 10),
            TextFormField(
              controller: _titleCtrl,
              decoration: _decoration('Title'),
              validator: (v) => (v == null || v.trim().isEmpty) ? 'Title is required' : null,
            ),
            const SizedBox(height: 12),
            TextFormField(controller: _subjectCtrl, decoration: _decoration('Subject')),
            const SizedBox(height: 12),
            TextFormField(
              controller: _descriptionCtrl,
              decoration: _decoration('Description'),
              maxLines: 4,
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              value: _language,
              decoration: _decoration('Language'),
              items: _kLanguages.map((l) => DropdownMenuItem(value: l, child: Text(l))).toList(),
              onChanged: (v) => setState(() => _language = v ?? _language),
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _maxParticipantsCtrl,
              decoration: _decoration('Max Participants'),
              keyboardType: TextInputType.number,
              validator: (v) {
                final n = int.tryParse((v ?? '').trim());
                if (n == null || n < 1) return 'A valid number is required (at least 1)';
                return null;
              },
            ),
            const SizedBox(height: 20),
            _sectionLabel('Features'),
            const SizedBox(height: 6),
            _featureToggles(),
            const SizedBox(height: 20),
            _sectionLabel('Refer & Earn'),
            const SizedBox(height: 6),
            _referralSection(),
            const SizedBox(height: 32),
          ],
        ),
      ),
      bottomNavigationBar: _saveBar(),
    );
  }

  Widget _sectionLabel(String text) => Text(text, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15));

  // -------------------------------------------------------------------
  // Cover image picker
  // -------------------------------------------------------------------
  Widget _coverPicker() {
    return GestureDetector(
      onTap: _pickCover,
      child: Container(
        height: 160,
        width: double.infinity,
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(16),
          image: _pickedCover != null
              ? DecorationImage(image: FileImage(File(_pickedCover!.path)), fit: BoxFit.cover)
              : (_existingCoverUrl != null && _existingCoverUrl!.isNotEmpty)
                  ? DecorationImage(image: CachedNetworkImageProvider(_existingCoverUrl!), fit: BoxFit.cover)
                  : null,
        ),
        child: (_pickedCover == null && (_existingCoverUrl == null || _existingCoverUrl!.isEmpty))
            ? Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(Icons.add_photo_alternate_outlined, size: 36, color: Colors.grey.shade500),
                  const SizedBox(height: 8),
                  Text('Add Cover Image', style: TextStyle(color: Colors.grey.shade600, fontSize: 13)),
                ],
              )
            : Align(
                alignment: Alignment.bottomRight,
                child: Container(
                  margin: const EdgeInsets.all(10),
                  padding: const EdgeInsets.all(8),
                  decoration: const BoxDecoration(color: Colors.black45, shape: BoxShape.circle),
                  child: const Icon(Icons.edit_rounded, color: Colors.white, size: 18),
                ),
              ),
      ),
    );
  }

  // -------------------------------------------------------------------
  // Individual / Organisation toggle
  // -------------------------------------------------------------------
  Widget _typeToggle() {
    Widget chip(String value, String label, IconData icon) {
      final selected = _classroomType == value;
      return Expanded(
        child: GestureDetector(
          onTap: () => setState(() => _classroomType = value),
          child: Container(
            padding: const EdgeInsets.symmetric(vertical: 12),
            decoration: BoxDecoration(
              color: selected ? LiveClassColors.navy : Colors.white,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              children: [
                Icon(icon, size: 20, color: selected ? Colors.white : LiveClassColors.navy),
                const SizedBox(height: 4),
                Text(label,
                    style: TextStyle(
                        color: selected ? Colors.white : LiveClassColors.navy, fontWeight: FontWeight.w600, fontSize: 13)),
              ],
            ),
          ),
        ),
      );
    }

    return Row(
      children: [
        chip(ClassroomType.individual, 'Individual', Icons.person_outline_rounded),
        const SizedBox(width: 10),
        chip(ClassroomType.organisation, 'Organisation', Icons.apartment_rounded),
      ],
    );
  }

  // -------------------------------------------------------------------
  // Feature toggles
  // -------------------------------------------------------------------
  Widget _featureToggles() {
    Widget tile(String label, IconData icon, bool value, ValueChanged<bool> onChanged) {
      return Container(
        margin: const EdgeInsets.only(bottom: 8),
        decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12)),
        child: SwitchListTile(
          value: value,
          onChanged: onChanged,
          activeThumbColor: LiveClassColors.navy,
          secondary: Icon(icon, color: LiveClassColors.navy),
          title: Text(label, style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w500)),
        ),
      );
    }

    return Column(
      children: [
        tile('Whiteboard', Icons.draw_outlined, _whiteboardEnabled, (v) => setState(() => _whiteboardEnabled = v)),
        tile('Screen Share', Icons.screen_share_outlined, _screenShareEnabled, (v) => setState(() => _screenShareEnabled = v)),
        tile('Chat', Icons.chat_bubble_outline_rounded, _chatEnabled, (v) => setState(() => _chatEnabled = v)),
        tile('Recording', Icons.fiber_manual_record_outlined, _recordingEnabled, (v) => setState(() => _recordingEnabled = v)),
      ],
    );
  }

  // -------------------------------------------------------------------
  // Refer & Earn (FIX — backend cross-check: ClassroomSerializer's
  // referral_enabled / referral_commission_percent are teacher-writable
  // but had no UI here before).
  // -------------------------------------------------------------------
  Widget _referralSection() {
    return Container(
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12)),
      child: Column(
        children: [
          SwitchListTile(
            value: _referralEnabled,
            onChanged: (v) => setState(() => _referralEnabled = v),
            activeThumbColor: LiveClassColors.navy,
            secondary: const Icon(Icons.campaign_outlined, color: LiveClassColors.navy),
            title: const Text('Let students refer this classroom',
                style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w500)),
            subtitle: const Text('Referrers earn a percentage of each resulting purchase',
                style: TextStyle(fontSize: 11.5)),
          ),
          if (_referralEnabled) ...[
            const Divider(height: 1, indent: 16, endIndent: 16),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 14),
              child: TextFormField(
                controller: _referralCommissionCtrl,
                decoration: _decoration('Commission %', hint: '0-100'),
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                validator: (v) {
                  if (!_referralEnabled) return null;
                  final n = double.tryParse((v ?? '').trim());
                  if (n == null || n < 0 || n > 100) return 'Enter a value between 0 and 100';
                  return null;
                },
              ),
            ),
          ],
        ],
      ),
    );
  }

  // -------------------------------------------------------------------
  // Bottom save bar
  // -------------------------------------------------------------------
  Widget _saveBar() {
    return SafeArea(
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
        decoration: BoxDecoration(color: Colors.white, boxShadow: [
          BoxShadow(color: Colors.black.withValues(alpha: 0.08), blurRadius: 10, offset: const Offset(0, -2)),
        ]),
        child: SizedBox(
          width: double.infinity,
          height: 48,
          child: DecoratedBox(
            decoration: BoxDecoration(gradient: LiveClassColors.gradient, borderRadius: BorderRadius.circular(12)),
            child: Material(
              color: Colors.transparent,
              child: InkWell(
                borderRadius: BorderRadius.circular(12),
                onTap: _busy ? null : _save,
                child: Center(
                  child: _saving
                      ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))
                      : Text(_isEdit ? 'Save Changes' : 'Create Classroom',
                          style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15)),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}