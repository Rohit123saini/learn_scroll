// message/widgets/mention_suggestions_overlay.dart
//
// 🔥 NAYA (Phase 3, §2.2 — FRONTEND_INTEGRATION_ARCHITECTURE.md) —
// @Mentions autocomplete overlay + supporting pure functions.
//
// `chat_screen.dart` isko already import karta hai aur teen cheezein
// use karta hai (dono naam yahi match karne chahiye, kahin aur define
// nahi hain):
//   - `extractMentionQuery(text, cursorPosition)` — `_onTypingChanged` me
//   - `insertMention(value, user)`                — `_onMentionSelected` me
//   - `MentionSuggestionsOverlay` widget            — compose box ke upar
//
// Backend sirf group ke **active members** ka username regex match karta
// hai (frontend doc §2.2) — isliye suggestion list hamesha caller-supplied
// `members` (`_groupMembers`, `getGroupActiveMembers()` se load hui) se
// hi banti hai, koi random/global user search nahi.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../models/message_models.dart';

const Color _kAccent = Color(0xFF3D7EFF);

/// Compose-box text + cursor position dekh kar batata hai ki abhi user
/// ek active "@query" type kar raha hai ya nahi.
///
/// - Return `null` -> koi active mention nahi, overlay hide.
/// - Return `""`   -> user ne abhi-abhi sirf "@" type kiya hai, sab
///   members dikhao.
/// - Return `"ali"` -> "@ali" type ho raha hai, "ali" se filter karo.
///
/// Rules:
///  - Cursor se peeche sabse najdeeki `@` dhoondo.
///  - `@` aur cursor ke beech koi space/newline nahi hona chahiye (warna
///    wo purana/khatam ho chuka mention hai, ab active nahi).
///  - `@` khud text ke start pe ho, ya usse pehle wala character
///    whitespace/newline ho (warna "email@domain" jaisi cheez false-positive
///    trigger kar degi).
String? extractMentionQuery(String text, int cursorPosition) {
  if (text.isEmpty) return null;
  final pos = cursorPosition.clamp(0, text.length);
  final before = text.substring(0, pos);
  final atIndex = before.lastIndexOf('@');
  if (atIndex == -1) return null;

  final segment = before.substring(atIndex + 1);
  if (segment.contains(' ') || segment.contains('\n')) return null;

  if (atIndex > 0) {
    final prevChar = before[atIndex - 1];
    if (prevChar != ' ' && prevChar != '\n') return null;
  }

  return segment;
}

/// Suggestion list se ek member select karne par, active "@query" ko
/// `@username ` se replace karta hai (trailing space taaki turant aage
/// type karte hi naya word shuru ho, mention ke saath jud na jaaye) aur
/// cursor ko usi ke turant baad rakhta hai.
///
/// Agar koi active `@` nahi milta (edge-case — text kahin aur se change
/// ho gaya ho), safe fallback ke taur pe original `value` hi wapas kar
/// deta hai, kuch break nahi hota.
TextEditingValue insertMention(TextEditingValue value, UserMini user) {
  final text = value.text;
  final rawCursor = value.selection.baseOffset;
  final cursor = (rawCursor < 0 ? text.length : rawCursor).clamp(0, text.length);
  final before = text.substring(0, cursor);
  final atIndex = before.lastIndexOf('@');
  if (atIndex == -1) return value;

  final mentionName = user.username.isNotEmpty ? user.username : user.displayName;
  final mentionText = '@$mentionName ';
  final newText = text.replaceRange(atIndex, cursor, mentionText);
  final newCursor = atIndex + mentionText.length;

  return TextEditingValue(
    text: newText,
    selection: TextSelection.collapsed(offset: newCursor),
  );
}

/// Compose box ke bilkul upar dikhne wala floating suggestion card —
/// WhatsApp/Slack jaisa "@" autocomplete. `chat_screen.dart` isko sirf
/// group chats me, `_mentionQuery != null` hote hi render karta hai.
class MentionSuggestionsOverlay extends StatelessWidget {
  final List<UserMini> members;
  final String query;
  final void Function(UserMini user) onSelected;

  const MentionSuggestionsOverlay({
    super.key,
    required this.members,
    required this.query,
    required this.onSelected,
  });

  List<UserMini> get _filtered {
    final q = query.trim().toLowerCase();
    final matches = members.where((m) {
      if (q.isEmpty) return true;
      return m.username.toLowerCase().contains(q) ||
          m.displayName.toLowerCase().contains(q);
    }).toList();

    // Username-prefix matches ("@ali" -> "aliya" username) sabse upar,
    // baaki alphabetical.
    matches.sort((a, b) {
      final aPrefix = a.username.toLowerCase().startsWith(q) ? 0 : 1;
      final bPrefix = b.username.toLowerCase().startsWith(q) ? 0 : 1;
      if (aPrefix != bPrefix) return aPrefix.compareTo(bPrefix);
      return a.displayName.toLowerCase().compareTo(b.displayName.toLowerCase());
    });
    return matches;
  }

  @override
  Widget build(BuildContext context) {
    final results = _filtered;
    if (results.isEmpty) return const SizedBox.shrink();

    return Container(
      constraints: const BoxConstraints(maxHeight: 210),
      margin: const EdgeInsets.symmetric(horizontal: 10),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.14),
            blurRadius: 12,
            offset: const Offset(0, -3),
          ),
        ],
      ),
      child: ListView.separated(
        shrinkWrap: true,
        padding: const EdgeInsets.symmetric(vertical: 4),
        itemCount: results.length,
        separatorBuilder: (_, __) => Divider(height: 1, color: Colors.grey[200]),
        itemBuilder: (context, i) {
          final m = results[i];
          final initial = m.displayName.isNotEmpty ? m.displayName[0].toUpperCase() : '?';
          return ListTile(
            dense: true,
            visualDensity: VisualDensity.compact,
            leading: CircleAvatar(
              radius: 16,
              backgroundColor: _kAccent,
              backgroundImage: (m.profilePhoto != null && m.profilePhoto!.isNotEmpty)
                  ? CachedNetworkImageProvider(m.profilePhoto!)
                  : null,
              child: (m.profilePhoto == null || m.profilePhoto!.isEmpty)
                  ? Text(initial, style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600))
                  : null,
            ),
            title: Text(
              m.displayName,
              style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, color: Colors.black87),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            subtitle: m.username.isNotEmpty
                ? Text('@${m.username}', style: TextStyle(fontSize: 11.5, color: Colors.grey[600]))
                : null,
            onTap: () => onSelected(m),
          );
        },
      ),
    );
  }
}