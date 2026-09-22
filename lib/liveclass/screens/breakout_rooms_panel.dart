// ============================================================
// LIVECLASS — BREAKOUT ROOMS PANEL (host/co-teacher/moderator)
//
// Backend surface used: GET/POST /sessions/{id}/breakout/, POST
// /sessions/{id}/breakout/assign/, POST /sessions/{id}/breakout/close/.
// Opened as a bottom sheet from `live_session_screen.dart`'s host
// toolbar. A breakout layout is create-once: POST 400s if one is
// already running, so this panel always closes the existing layout
// before creating a new room count — mirrors the backend's own
// "close it first" rule from the endpoint doc.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../api/liveclass_api.dart';
import '../models/liveclass_models.dart';

class BreakoutRoomsPanel extends StatefulWidget {
  final LiveClassApi api;
  final int sessionId;
  const BreakoutRoomsPanel({super.key, required this.api, required this.sessionId});

  @override
  State<BreakoutRoomsPanel> createState() => _BreakoutRoomsPanelState();
}

class _BreakoutRoomsPanelState extends State<BreakoutRoomsPanel> {
  List<dynamic> _rooms = const [];
  List<SessionParticipant> _participants = const [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    final results = await Future.wait([
      widget.api.breakoutRooms(widget.sessionId),
      widget.api.participants(widget.sessionId),
    ]);
    setState(() {
      _rooms = results[0] as List;
      _participants = (results[1] as List).map((e) => SessionParticipant.fromJson(e as Map<String, dynamic>)).toList();
      _loading = false;
    });
  }

  Future<void> _createRooms(int count) async {
    if (_rooms.isNotEmpty) {
      await widget.api.closeBreakout(widget.sessionId);
    }
    await widget.api.createBreakoutRooms(widget.sessionId, count);
    _load();
  }

  Future<void> _assign(int participantId, int? room) async {
    await widget.api.assignBreakout(widget.sessionId, participantId, room);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(kLsPad),
        child: _loading
            ? const SizedBox(height: 120, child: Center(child: CircularProgressIndicator()))
            : Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(t.breakoutRoomsTitle, style: LsType.head(context, size: 16)),
                const SizedBox(height: 12),
                if (_rooms.isEmpty)
                  Wrap(spacing: 8, children: [2, 3, 4, 5].map((n) => OutlinedButton(
                        onPressed: () => _createRooms(n),
                        child: Text(t.roomsCountLabel(n)),
                      )).toList())
                else ...[
                  Row(children: [
                    Expanded(child: Text(t.roomsActiveLabel(_rooms.length), style: TextStyle(fontSize: 13, color: cs.onSurface))),
                    TextButton(
                      onPressed: () async {
                        await widget.api.closeBreakout(widget.sessionId);
                        _load();
                      },
                      child: Text(t.closeBreakoutCta),
                    ),
                  ]),
                  const SizedBox(height: 8),
                  SizedBox(
                    height: 260,
                    child: ListView(children: _participants.map((p) => LsCard(
                          margin: const EdgeInsets.only(bottom: 8),
                          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                          child: Row(children: [
                            Expanded(child: Text(p.userName, style: TextStyle(fontSize: 12.5, color: cs.onSurface))),
                            DropdownButton<int?>(
                              value: null,
                              hint: Text(t.assignRoomHint, style: const TextStyle(fontSize: 11.5)),
                              items: [
                                DropdownMenuItem(value: null, child: Text(t.mainRoomLabel)),
                                for (var i = 1; i <= _rooms.length; i++) DropdownMenuItem(value: i, child: Text(t.roomNumberLabel(i))),
                              ],
                              onChanged: (v) => _assign(p.id, v),
                            ),
                          ]),
                        )).toList()),
                  ),
                ],
              ]),
      ),
    );
  }
}
