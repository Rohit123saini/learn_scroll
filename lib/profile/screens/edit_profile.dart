import 'dart:io';
import 'package:flutter/foundation.dart' show listEquals;
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../api_service.dart';
import '../model.dart';
import '../profile_link.dart'; // P7-FE
import '../../utils/api.dart';
import '../../widgets/ls_ui.dart';
import '../../l10n/app_localizations.dart';
import '../../post/screens/ratio_crop_screen.dart'; // 1.3-FE

// ============================================================
// EDIT PROFILE — reskinned onto the same ls_ui.dart/ColorScheme system as
// profile.dart and home.dart. Also fixed two real bugs while in here:
//   1. RAW BACKEND ERRORS — a duplicate-username save used to show the
//      literal DRF JSON body in a SnackBar (`Error: Exception: Update
//      failed: 400 - {"username": ["A user with that username already
//      exists."]}`). `ApiService.updateProfile` now parses that into a
//      clean message; this screen recognizes the username case specifically
//      and reuses the app's own existing `signupUsernameExists` copy from
//      the signup flow, so the two places that can produce this exact
//      error now say the same thing.
//   2. NO VALIDATION / NO UNSAVED-CHANGES GUARD — username could be
//      submitted empty (backend would 400 on it with no client-side hint
//      first), and backing out mid-edit silently discarded typed changes
//      with no confirmation. Both added.
//
// ⚠️ i18n — reused existing keys where the app already had them
// (`save`, `signupUsername`, `signupUsernameRequired`, `signupUsernameExists`,
// `signupFirstName`, `signupLastName`, `editProfileButton` — this last one
// added by the profile.dart pass, reused here as the AppBar title since
// it's the same text). Genuinely new, added to app_hi.arb by this pass /
// still need app_en.arb:
//   bioLabel                    "Bio"
//   profileUpdatedSuccess       "Profile updated successfully"
//   discardChangesTitle         "Discard changes?"
//   discardChangesMessage       "You have unsaved changes. Are you sure you want to go back?"
//   discard                     "Discard"
//   keepEditing                 "Keep Editing"
//   changePhotoTooltip          "Change photo"
// ============================================================

// P7-FE — one editable row of the links editor.
class _LinkRow {
  final TextEditingController title;
  final TextEditingController url;
  _LinkRow({String title = '', String url = ''})
      : title = TextEditingController(text: title),
        url = TextEditingController(text: url);
  void dispose() {
    title.dispose();
    url.dispose();
  }
}

class EditProfileScreen extends StatefulWidget {
  final ProfileModel user;
  const EditProfileScreen({super.key, required this.user});

  @override
  State<EditProfileScreen> createState() => _EditProfileScreenState();
}

class _EditProfileScreenState extends State<EditProfileScreen> {
  final _formKey = GlobalKey<FormState>();
  late TextEditingController _usernameController;
  late TextEditingController _firstNameController;
  late TextEditingController _lastNameController;
  late TextEditingController _bioController;
  // P7-FE — pronouns (<=30), category label (<=40), up to 3 links. Strings are plain
  // English for now (no new .arb keys needed); move to l10n with the rest when convenient.
  late TextEditingController _pronounsController;
  late TextEditingController _categoryController;
  final List<_LinkRow> _linkRows = [];

  File? _selectedImage;
  bool _isSaving = false;

  bool get _isDirty =>
      _selectedImage != null ||
      _usernameController.text.trim() != widget.user.username ||
      _firstNameController.text.trim() != widget.user.firstName ||
      _lastNameController.text.trim() != widget.user.lastName ||
      _bioController.text.trim() != widget.user.bio ||
      _pronounsController.text.trim() != widget.user.pronouns ||
      _categoryController.text.trim() != widget.user.categoryLabel ||
      !listEquals(_currentLinks(), widget.user.links);

  /// Rows with anything typed, normalised the same way they'll be sent
  /// (bare "example.com" -> "https://example.com"). Fully empty rows are dropped.
  List<ProfileLink> _currentLinks() => [
        for (final r in _linkRows)
          if (r.title.text.trim().isNotEmpty || r.url.text.trim().isNotEmpty)
            ProfileLink(title: r.title.text.trim(), url: normalizeLinkUrl(r.url.text)),
      ];

  @override
  void initState() {
    super.initState();
    _usernameController = TextEditingController(text: widget.user.username);
    _firstNameController = TextEditingController(text: widget.user.firstName);
    _lastNameController = TextEditingController(text: widget.user.lastName);
    _bioController = TextEditingController(text: widget.user.bio);
    _pronounsController = TextEditingController(text: widget.user.pronouns);
    _categoryController = TextEditingController(text: widget.user.categoryLabel);
    for (final l in widget.user.links.take(kMaxProfileLinks)) {
      _linkRows.add(_LinkRow(title: l.title, url: l.url));
    }
  }

  @override
  void dispose() {
    _usernameController.dispose();
    _firstNameController.dispose();
    _lastNameController.dispose();
    _bioController.dispose();
    _pronounsController.dispose();
    _categoryController.dispose();
    for (final r in _linkRows) {
      r.dispose();
    }
    super.dispose();
  }

  Future<void> _pickImage() async {
    final picker = ImagePicker();
    final pickedFile = await picker.pickImage(source: ImageSource.gallery, imageQuality: 80);
    if (pickedFile == null || !mounted) return;
    // 1.3-FE — avatars are always 1:1: crop with a circle guide before it is
    // set (cancel = keep the current photo). Output is a 720px square JPEG.
    final cropped = await showRatioCrop(
      context,
      File(pickedFile.path),
      aspect: 1.0,
      title: AppLocalizations.of(context)!.avatarCropTitle,
      circleGuide: true,
      outputWidth: 720,
    );
    if (cropped == null || !mounted) return;
    setState(() => _selectedImage = cropped);
  }

  Future<bool> _confirmDiscard(AppLocalizations l10n) async {
    if (!_isDirty) return true;
    final cs = Theme.of(context).colorScheme;
    final result = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l10n.discardChangesTitle),
        content: Text(l10n.discardChangesMessage),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: Text(l10n.keepEditing)),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(l10n.discard, style: TextStyle(color: cs.error)),
          ),
        ],
      ),
    );
    return result ?? false;
  }

  Future<void> _updateProfile(AppLocalizations l10n) async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _isSaving = true);
    try {
      await ApiService.updateProfile(
        username: _usernameController.text.trim(),
        firstName: _firstNameController.text.trim(),
        lastName: _lastNameController.text.trim(),
        bio: _bioController.text.trim(),
        pronouns: _pronounsController.text.trim(), // P7-FE
        categoryLabel: _categoryController.text.trim(), // P7-FE
        links: _currentLinks(), // P7-FE — [] clears them
        profilePhoto: _selectedImage,
      );
      if (mounted) {
        lsSnack(context, l10n.profileUpdatedSuccess);
        Navigator.pop(context, true);
      }
    } catch (e) {
      if (!mounted) return;
      final raw = e.toString().replaceFirst('Exception: ', '');
      // See the `username:` sentinel note in ApiService._extractUpdateError.
      final message = raw.startsWith('username:') ? l10n.signupUsernameExists : raw;
      lsSnack(context, message, error: true);
    } finally {
      if (mounted) setState(() => _isSaving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, result) async {
        if (didPop) return;
        if (await _confirmDiscard(l10n) && mounted) {
          Navigator.pop(context);
        }
      },
      child: Scaffold(
        backgroundColor: lsBg(context),
        appBar: AppBar(
          title: Text(l10n.editProfileButton),
          actions: [
            TextButton(
              onPressed: _isSaving ? null : () => _updateProfile(l10n),
              child: _isSaving
                  ? SizedBox(
                      height: 18,
                      width: 18,
                      child: CircularProgressIndicator(strokeWidth: 2, color: cs.primary),
                    )
                  : Text(l10n.save, style: TextStyle(color: cs.primary, fontWeight: FontWeight.w700)),
            ),
          ],
        ),
        body: Form(
          key: _formKey,
          child: ListView(
            padding: const EdgeInsets.all(kLsPad),
            children: [
              const SizedBox(height: 12),
              Center(child: _avatarPicker(cs, l10n)),
              const SizedBox(height: 32),
              TextFormField(
                controller: _usernameController,
                decoration: InputDecoration(
                  labelText: l10n.signupUsername,
                  prefixIcon: const Icon(Icons.alternate_email_rounded),
                ),
                validator: (v) => (v == null || v.trim().isEmpty) ? l10n.signupUsernameRequired : null,
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: _firstNameController,
                decoration: InputDecoration(
                  labelText: l10n.signupFirstName,
                  prefixIcon: const Icon(Icons.person_outline_rounded),
                ),
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: _lastNameController,
                decoration: InputDecoration(
                  labelText: l10n.signupLastName,
                  prefixIcon: const Icon(Icons.person_outline_rounded),
                ),
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: _bioController,
                maxLines: 4,
                maxLength: 150,
                decoration: InputDecoration(
                  labelText: l10n.bioLabel,
                  alignLabelWithHint: true,
                  prefixIcon: const Padding(
                    padding: EdgeInsets.only(bottom: 60),
                    child: Icon(Icons.info_outline_rounded),
                  ),
                ),
              ),
              // ---- P7-FE ----
              const SizedBox(height: 14),
              TextFormField(
                controller: _pronounsController,
                maxLength: 30,
                decoration: const InputDecoration(
                  labelText: 'Pronouns',
                  hintText: 'e.g. she/her',
                  counterText: '',
                  prefixIcon: Icon(Icons.badge_outlined),
                ),
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: _categoryController,
                maxLength: 40,
                decoration: const InputDecoration(
                  labelText: 'Category',
                  hintText: 'e.g. Teacher, JEE Aspirant',
                  counterText: '',
                  prefixIcon: Icon(Icons.local_offer_outlined),
                ),
              ),
              const SizedBox(height: 22),
              _linksEditor(cs),
              const SizedBox(height: 24),
            ],
          ),
        ),
      ),
    );
  }

  // ---------- links editor (P7-FE) ----------
  void _addLink() {
    if (_linkRows.length >= kMaxProfileLinks) return;
    setState(() => _linkRows.add(_LinkRow()));
  }

  void _removeLink(int i) {
    final row = _linkRows[i];
    setState(() => _linkRows.removeAt(i));
    // dispose AFTER the frame that stops using the controllers
    WidgetsBinding.instance.addPostFrameCallback((_) => row.dispose());
  }

  Widget _linksEditor(ColorScheme cs) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(children: [
          const Text('Links', style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700)),
          const SizedBox(width: 8),
          Text('${_linkRows.length}/$kMaxProfileLinks', style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
        ]),
        const SizedBox(height: 10),
        for (int i = 0; i < _linkRows.length; i++)
          Padding(
            key: ObjectKey(_linkRows[i]),
            padding: const EdgeInsets.only(bottom: 12),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(children: [
                    TextFormField(
                      controller: _linkRows[i].title,
                      maxLength: kProfileLinkTitleMax,
                      decoration: const InputDecoration(labelText: 'Title', hintText: 'Website', counterText: ''),
                      validator: (v) => ((v ?? '').trim().isEmpty && _linkRows[i].url.text.trim().isNotEmpty)
                          ? 'Add a title'
                          : null,
                    ),
                    const SizedBox(height: 8),
                    TextFormField(
                      controller: _linkRows[i].url,
                      keyboardType: TextInputType.url,
                      autocorrect: false,
                      decoration: const InputDecoration(labelText: 'URL', hintText: 'https://example.com'),
                      validator: (v) {
                        final empty = (v ?? '').trim().isEmpty;
                        if (empty) return _linkRows[i].title.text.trim().isNotEmpty ? 'Add a URL' : null;
                        return validateLinkUrl(v);
                      },
                    ),
                  ]),
                ),
                IconButton(
                  tooltip: 'Remove link',
                  onPressed: () => _removeLink(i),
                  icon: Icon(Icons.close_rounded, color: cs.onSurfaceVariant),
                ),
              ],
            ),
          ),
        if (_linkRows.length < kMaxProfileLinks)
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: _addLink,
              icon: const Icon(Icons.add_link_rounded, size: 18),
              label: const Text('Add link'),
            ),
          ),
      ],
    );
  }

  Widget _avatarPicker(ColorScheme cs, AppLocalizations l10n) {
    final photo = widget.user.profilePhoto;
    final url = photo.isEmpty ? '' : (photo.startsWith('http') ? photo : '${Api.baseUrl}$photo');
    return Stack(children: [
      Container(
        width: 108,
        height: 108,
        padding: const EdgeInsets.all(2),
        decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: cs.outlineVariant, width: 2)),
        child: ClipOval(
          child: _selectedImage != null
              ? Image.file(_selectedImage!, width: 104, height: 104, fit: BoxFit.cover)
              : url.isEmpty
                  ? Container(color: cs.surfaceVariant, child: Icon(Icons.person_rounded, size: 52, color: cs.onSurfaceVariant))
                  : CachedNetworkImage(
                      imageUrl: url,
                      width: 104,
                      height: 104,
                      fit: BoxFit.cover,
                      placeholder: (c, u) => Container(color: cs.surfaceVariant),
                      errorWidget: (c, u, e) =>
                          Container(color: cs.surfaceVariant, child: Icon(Icons.person_rounded, size: 52, color: cs.onSurfaceVariant)),
                    ),
        ),
      ),
      Positioned(
        bottom: 0,
        right: 0,
        child: Semantics(
          button: true,
          label: l10n.changePhotoTooltip,
          child: Tooltip(
            message: l10n.changePhotoTooltip,
            child: InkWell(
              onTap: _pickImage,
              customBorder: const CircleBorder(),
              child: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(color: cs.primary, shape: BoxShape.circle, border: Border.all(color: cs.surface, width: 2)),
                child: Icon(Icons.camera_alt_rounded, color: cs.onPrimary, size: 18),
              ),
            ),
          ),
        ),
      ),
    ]);
  }
}
