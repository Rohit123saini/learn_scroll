// message/screens/class_transcript_screen.dart
//
// 🔥 NAYA (Feature 3) — Class transcript + timestamped searchable recap.
//
// IMPORTANT design note: LiveKit server-side room recording/egress abhi
// is stack me wired nahi hai (sirf client SDK hai) — isliye "poori class
// ki EK continuous recording, jisme click karte hi timestamp pe seek ho
// jaaye" abhi possible NAHI hai. Jo buildable tha existing packages se:
// har participant apna mic locally chunk-record karta hai
// (StudyRoomCallManager, ~45s chunks), backend har chunk transcribe karke
// session-relative timestamp ke saath store karta hai. Ye screen un
// segments ko ek combined, searchable timeline ki tarah dikhata hai —
// "jump to timestamp" ka matlab yahan hai "us waqt ka audio chunk play
// karo" (poori-recording-seek nahi, per-segment playback).
//
// Backend GET /message/study-room/<conversationId>/transcript/?q=...
// (views_ai.ClassTranscriptSearchView) — session_id na diya jaaye to
// backend khud sabse recent session dikhata hai.

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:audioplayers/audioplayers.dart';

import '../models/study_room_models.dart';
import '../services/ai_study_service.dart';

const Color _kNavy = Color(0xFF030F27);
const Color _kAccent = Color(0xFF3D7EFF);

class ClassTranscriptScreen extends StatefulWidget {
  final String conversationId;
  final String? sessionId; // null = backend apni marzi se latest session dikhaye

  const ClassTranscriptScreen({super.key, required this.conversationId, this.sessionId});

  @override
  State<ClassTranscriptScreen> createState() => _ClassTranscriptScreenState();
}

class _ClassTranscriptScreenState extends State<ClassTranscriptScreen> {
  final TextEditingController _queryController = TextEditingController();
  Timer? _debounce;

  bool _loading = true;
  String? _error;
  List<TranscriptSegmentModel> _segments = [];

  final AudioPlayer _player = AudioPlayer();
  String? _playingSegmentId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _queryController.dispose();
    _player.dispose();
    super.dispose();
  }

  Future<void> _load({String? query}) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final segments = await AiStudyService.searchTranscript(
        conversationId: widget.conversationId,
        sessionId: widget.sessionId,
        query: query,
      );
      if (!mounted) return;
      setState(() {
        _segments = segments;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Transcript load nahi ho paya: $e';
        _loading = false;
      });
    }
  }

  void _onQueryChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), () {
      _load(query: value.trim().isEmpty ? null : value.trim());
    });
  }

  Future<void> _togglePlay(TranscriptSegmentModel segment) async {
    if (_playingSegmentId == segment.id) {
      await _player.stop();
      setState(() => _playingSegmentId = null);
      return;
    }
    try {
      await _player.stop();
      await _player.play(UrlSource(segment.audioFileUrl));
      setState(() => _playingSegmentId = segment.id);
      _player.onPlayerComplete.first.then((_) {
        if (mounted && _playingSegmentId == segment.id) {
          setState(() => _playingSegmentId = null);
        }
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Audio play nahi ho paya: $e')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kNavy,
      appBar: AppBar(
        backgroundColor: _kNavy,
        elevation: 0,
        title: const Text('Class Transcript', style: TextStyle(color: Colors.white)),
        iconTheme: const IconThemeData(color: Colors.white),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
            child: TextField(
              controller: _queryController,
              onChanged: _onQueryChanged,
              style: const TextStyle(color: Colors.white),
              decoration: InputDecoration(
                hintText: 'Search: "jump to where teacher explained..."',
                hintStyle: const TextStyle(color: Colors.white38),
                prefixIcon: const Icon(Icons.search, color: Colors.white54),
                filled: true,
                fillColor: Colors.white10,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
              ),
            ),
          ),
          Expanded(child: _buildBody()),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator(color: _kAccent));
    }
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(_error!, style: const TextStyle(color: Colors.white54), textAlign: TextAlign.center),
        ),
      );
    }
    if (_segments.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(24),
          child: Text(
            'Abhi is class ka transcript ready nahi hai — recording chalne ke\n'
            'thodi der baad (background me transcribe hota hai) yahan dikhega.',
            style: TextStyle(color: Colors.white38),
            textAlign: TextAlign.center,
          ),
        ),
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.symmetric(vertical: 8),
      itemCount: _segments.length,
      separatorBuilder: (_, __) => const Divider(color: Colors.white12, height: 1),
      itemBuilder: (_, i) {
        final s = _segments[i];
        final isPlaying = _playingSegmentId == s.id;
        return ListTile(
          leading: CircleAvatar(
            backgroundColor: _kAccent.withOpacity(0.2),
            child: Text(s.timeLabel, style: const TextStyle(color: _kAccent, fontSize: 11, fontWeight: FontWeight.bold)),
          ),
          title: Text(s.text, style: const TextStyle(color: Colors.white)),
          subtitle: Text(s.speakerName, style: const TextStyle(color: Colors.white38, fontSize: 12)),
          trailing: IconButton(
            icon: Icon(isPlaying ? Icons.stop_circle : Icons.play_circle_outline, color: _kAccent),
            onPressed: () => _togglePlay(s),
          ),
          onTap: () => _togglePlay(s),
        );
      },
    );
  }
}