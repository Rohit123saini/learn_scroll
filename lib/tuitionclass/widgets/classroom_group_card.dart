// tuitionclass/widgets/classroom_group_card.dart
// T3 — class ka chat group: "Open group" (participants), status + retry +
// on/off toggle (teacher/manager). Group server par class create hote hi
// auto-ban jaata hai aur participants ke saath sync rehta hai.
import 'package:flutter/material.dart';

import '../../message/screens/chat_screen.dart';
import '../../message/services/message_api_service.dart';
import '../services/tuitionclass_api_service.dart';

class ClassroomGroupCard extends StatefulWidget {
  final int classroomId;
  final bool canManage; // teacher / co-teacher / moderator
  final bool isTeacher; // only the owner may switch the group off/on
  const ClassroomGroupCard({super.key, required this.classroomId, required this.canManage, required this.isTeacher});

  @override
  State<ClassroomGroupCard> createState() => _ClassroomGroupCardState();
}

class _ClassroomGroupCardState extends State<ClassroomGroupCard> {
  Map<String, dynamic>? _state;
  bool _busy = false;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final api = TuitionClassApi.classrooms;
      final s = widget.canManage ? await api.groupStatus(widget.classroomId) : await api.groupOpen(widget.classroomId);
      if (mounted) setState(() { _state = s; _loading = false; });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  bool get _enabled => _state?['chat_group_enabled'] == true;
  bool get _ready => (_state?['group_ready'] ?? (_state?['linked_conversation_id'] != null)) == true;

  void _snack(String m) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));

  Future<void> _open() async {
    setState(() => _busy = true);
    try {
      String? id = _state?['linked_conversation_id']?.toString();
      if (id == null) {
        final s = await TuitionClassApi.classrooms.groupOpen(widget.classroomId);
        id = s['linked_conversation_id']?.toString();
      }
      if (id == null) {
        _snack('Group abhi ready nahi hai.');
        return;
      }
      final convo = await MessageApiService.getConversation(id);
      if (!mounted) return;
      await Navigator.push(context, MaterialPageRoute(builder: (_) => ChatScreen(conversation: convo)));
    } on TuitionClassApiException catch (e) {
      _snack(e.message);
    } catch (_) {
      _snack('Group nahi khul paya, dobara try karo.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _retry() async {
    setState(() => _busy = true);
    try {
      final s = await TuitionClassApi.classrooms.groupRetry(widget.classroomId);
      if (mounted) setState(() => _state = s);
      _snack('Group ban gaya.');
    } on TuitionClassApiException catch (e) {
      _snack(e.message);
    } catch (_) {
      _snack('Group abhi nahi ban paya, thodi der baad try karo.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _toggle(bool value) async {
    setState(() => _busy = true);
    try {
      final s = await TuitionClassApi.classrooms.groupToggle(widget.classroomId, value);
      if (mounted) setState(() => _state = s);
      _snack(value ? 'Group on ho gaya.' : 'Group band (archive) ho gaya.');
    } on TuitionClassApiException catch (e) {
      _snack(e.message);
    } catch (_) {
      _snack('Change nahi ho paya.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const SizedBox.shrink();
    if (_state == null) return const SizedBox.shrink();
    // A non-manager only sees the card when there is something to open.
    if (!widget.canManage && !_ready) return const SizedBox.shrink();

    final members = _state?['member_count'];
    final expected = _state?['expected_count'];
    final inSync = _state?['in_sync'] == true;

    return Card(
      margin: const EdgeInsets.fromLTRB(16, 8, 16, 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.forum_outlined),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                widget.canManage
                    ? (!_enabled ? 'Class group band hai' : _ready ? 'Group ban gaya' : 'Group abhi bana nahi')
                    : 'Class group',
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ),
            if (widget.canManage && widget.isTeacher)
              Switch(value: _enabled, onChanged: _busy ? null : _toggle),
          ]),
          if (widget.canManage && _ready && members != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                inSync ? '$members members (participants ke saath sync)' : '$members members / $expected participants — sync ho raha hai',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
          const SizedBox(height: 8),
          Row(children: [
            if (_ready)
              FilledButton.icon(
                onPressed: _busy ? null : _open,
                icon: const Icon(Icons.chat_bubble_outline),
                label: const Text('Open group'),
              ),
            if (widget.canManage && _enabled && (!_ready || !inSync)) ...[
              if (_ready) const SizedBox(width: 8),
              OutlinedButton.icon(
                onPressed: _busy ? null : _retry,
                icon: const Icon(Icons.refresh),
                label: Text(_ready ? 'Sync' : 'Retry'),
              ),
            ],
          ]),
        ]),
      ),
    );
  }
}
