import 'package:flutter/material.dart';

// 🔥 NAYA — shapes aur text tool add karne ke liye enum extend kiya.
enum ToolType { marker, paint, eraser, highlighter, rectangle, circle, line, arrowLine, text }

/// Shape tools ko freehand tools se alag treat karne ke liye helper.
bool isShapeTool(ToolType t) =>
    t == ToolType.rectangle || t == ToolType.circle || t == ToolType.line || t == ToolType.arrowLine;

class DrawingPoint {
  final Offset offset;
  final Paint paint;
  final ToolType toolType;

  DrawingPoint({
    required this.offset,
    required this.paint,
    required this.toolType,
  });

  Map<String, dynamic> toJson() => {
    'dx': offset.dx,
    'dy': offset.dy,
    'color': paint.color.value,
    'strokeWidth': paint.strokeWidth,
    'tool': toolType.name,
  };

  factory DrawingPoint.fromJson(Map<String, dynamic> json) {
    return DrawingPoint(
      offset: Offset((json['dx'] as num).toDouble(), (json['dy'] as num).toDouble()),
      paint: Paint()
        ..color = Color(json['color'])
        ..strokeWidth = (json['strokeWidth'] as num).toDouble()
        ..strokeCap = StrokeCap.round,
      toolType: ToolType.values.firstWhere(
        (e) => e.name == json['tool'],
        orElse: () => ToolType.marker,
      ),
    );
  }
}

/// 🔥 NAYA — Rectangle/Circle/Line/Arrow ke liye. Freehand stroke se alag
/// isliye rakha hai kyunki isme sirf 2 points (start/end) store hote hain
/// — bahut lightweight, aur drag ke dauraan preview dikhana bhi aasan.
class ShapeElement {
  final String id;
  final String userId;
  Offset start;
  Offset end;
  final ToolType tool;
  final Color color;
  final double strokeWidth;

  ShapeElement({
    required this.id,
    required this.userId,
    required this.start,
    required this.end,
    required this.tool,
    required this.color,
    required this.strokeWidth,
  });

  Map<String, dynamic> toJson() => {
    'id': id,
    'userId': userId,
    'startDx': start.dx,
    'startDy': start.dy,
    'endDx': end.dx,
    'endDy': end.dy,
    'tool': tool.name,
    'color': color.value,
    'strokeWidth': strokeWidth,
  };

  factory ShapeElement.fromJson(Map<String, dynamic> json) {
    return ShapeElement(
      id: json['id']?.toString() ?? '',
      userId: json['userId']?.toString() ?? '',
      start: Offset((json['startDx'] as num).toDouble(), (json['startDy'] as num).toDouble()),
      end: Offset((json['endDx'] as num).toDouble(), (json['endDy'] as num).toDouble()),
      tool: ToolType.values.firstWhere(
        (e) => e.name == json['tool'],
        orElse: () => ToolType.rectangle,
      ),
      color: Color(json['color']),
      strokeWidth: (json['strokeWidth'] as num).toDouble(),
    );
  }
}

/// 🔥 NAYA — Text tool: canvas pe kahin bhi tap karke type kiya gaya text.
/// Sticky note se alag hai — koi background box nahi, bas plain floating
/// text jo drag/edit ho sakta hai.
class TextElement {
  final String id;
  final String userId;
  String text;
  Offset position;
  Color color;
  double fontSize;

  TextElement({
    required this.id,
    required this.userId,
    required this.text,
    required this.position,
    this.color = Colors.black,
    this.fontSize = 16,
  });

  Map<String, dynamic> toJson() => {
    'id': id,
    'userId': userId,
    'text': text,
    'dx': position.dx,
    'dy': position.dy,
    'color': color.value,
    'fontSize': fontSize,
  };

  factory TextElement.fromJson(Map<String, dynamic> json) {
    return TextElement(
      id: json['id']?.toString() ?? '',
      userId: json['userId']?.toString() ?? '',
      text: json['text']?.toString() ?? '',
      position: Offset((json['dx'] as num).toDouble(), (json['dy'] as num).toDouble()),
      color: Color(json['color'] ?? Colors.black.value),
      fontSize: (json['fontSize'] as num?)?.toDouble() ?? 16,
    );
  }
}

class StickyNoteModel {
  final String id;
  final String userId;
  String text;
  Offset position;
  Color color;

  StickyNoteModel({
    required this.id,
    required this.userId,
    required this.text,
    required this.position,
    required this.color,
  });

  Map<String, dynamic> toJson() => {
    'id': id,
    'userId': userId,
    'text': text,
    'dx': position.dx,
    'dy': position.dy,
    'color': color.value,
  };

  factory StickyNoteModel.fromJson(Map<String, dynamic> json) {
    return StickyNoteModel(
      id: json['id'].toString(),
      userId: json['userId'].toString(),
      text: json['text']?.toString() ?? '',
      position: Offset((json['dx'] as num).toDouble(), (json['dy'] as num).toDouble()),
      color: Color(json['color']),
    );
  }
}

class UserProfileWindowModel {
  final String userId;
  final String displayName;
  final String? avatarUrl;
  Offset position;
  Size size;
  int zIndex;

  UserProfileWindowModel({
    required this.userId,
    required this.displayName,
    this.avatarUrl,
    required this.position,
    required this.size,
    required this.zIndex,
  });

  Map<String, dynamic> toJson() => {
    'userId': userId,
    'displayName': displayName,
    'avatarUrl': avatarUrl,
    'dx': position.dx,
    'dy': position.dy,
    'width': size.width,
    'height': size.height,
    'zIndex': zIndex,
  };

  factory UserProfileWindowModel.fromJson(Map<String, dynamic> json) {
    return UserProfileWindowModel(
      userId: json['userId'],
      displayName: json['displayName'],
      avatarUrl: json['avatarUrl'],
      position: Offset((json['dx'] as num).toDouble(), (json['dy'] as num).toDouble()),
      size: Size((json['width'] as num).toDouble(), (json['height'] as num).toDouble()),
      zIndex: json['zIndex'] ?? 0,
    );
  }
}

/// 🔥 NAYA — MULTI-PAGE WHITEBOARD
/// Har page apna khud ka strokes/shapes/texts/sticky-notes rakhta hai,
/// jaise PDF/slides ke beech switch karte ho waise hi whiteboard pages ke
/// beech switch hota hai. `userStrokeIndices` sirf local undo-bookkeeping
/// ke liye hai — backend save/restore me include NAHI hota (isliye
/// toJson/fromJson isko touch nahi karte).
class WhiteboardPage {
  final String id;
  List<List<DrawingPoint>> strokes;
  List<ShapeElement> shapes;
  List<TextElement> texts;
  List<StickyNoteModel> stickyNotes;
  Map<String, List<int>> userStrokeIndices = {};

  // 🔥 NAYA — is page pe agar koi PDF/image load kiya gaya hai to uska
  // backend URL yahan store hota hai (local file path nahi — wo sirf
  // isi device tak valid hota hai, doosre participant ke phone pe kaam
  // nahi karega). Isi field ki wajah se:
  //   1. `toJson()`/`fromJson()` ke through ye `saveStudyRoomState`/
  //      `getStudyRoomState` me persist hota hai, isliye baad me join
  //      karne wala ya app reopen karne wala user bhi wahi POORI file
  //      (sab pages) dekh sakta hai, sirf ek screenshot nahi.
  //   2. Realtime me already-connected participants ko `load_page_file`
  //      socket event se turant pata chal jaata hai, jo wo apne device
  //      pe download karke poora render kar lete hain.
  String? fileUrl;
  String? fileType; // 'pdf' | 'image'

  WhiteboardPage({
    required this.id,
    List<List<DrawingPoint>>? strokes,
    List<ShapeElement>? shapes,
    List<TextElement>? texts,
    List<StickyNoteModel>? stickyNotes,
    this.fileUrl,
    this.fileType,
  })  : strokes = strokes ?? [],
        shapes = shapes ?? [],
        texts = texts ?? [],
        stickyNotes = stickyNotes ?? [];

  Map<String, dynamic> toJson() => {
    'id': id,
    'strokes': strokes.map((stroke) => stroke.map((p) => p.toJson()).toList()).toList(),
    'shapes': shapes.map((s) => s.toJson()).toList(),
    'texts': texts.map((t) => t.toJson()).toList(),
    'stickyNotes': stickyNotes.map((n) => n.toJson()).toList(),
    if (fileUrl != null) 'fileUrl': fileUrl,
    if (fileType != null) 'fileType': fileType,
  };

  factory WhiteboardPage.fromJson(Map<String, dynamic> json) {
    return WhiteboardPage(
      id: json['id']?.toString() ?? DateTime.now().millisecondsSinceEpoch.toString(),
      strokes: ((json['strokes'] as List?) ?? [])
          .map<List<DrawingPoint>>(
            (stroke) => (stroke as List).map((p) => DrawingPoint.fromJson(p)).toList(),
          )
          .toList(),
      shapes: ((json['shapes'] as List?) ?? []).map((s) => ShapeElement.fromJson(s)).toList(),
      texts: ((json['texts'] as List?) ?? []).map((t) => TextElement.fromJson(t)).toList(),
      stickyNotes:
          ((json['stickyNotes'] as List?) ?? []).map((n) => StickyNoteModel.fromJson(n)).toList(),
      fileUrl: json['fileUrl']?.toString(),
      fileType: json['fileType']?.toString(),
    );
  }
}

/// 🔥 NAYA — Class transcript segment (Feature 3: timestamped searchable
/// recap). Ek segment = kisi ek participant ke ek local-mic-recording
/// chunk ka transcript, session-relative offset (seconds, session start
/// se) ke saath — NOT wall-clock time, taaki "jump to timestamp" ek hi
/// consistent timeline pe kaam kare chahe kisi ka bhi phone clock skewed
/// ho. `audioFileUrl` sirf isi chunk ka hai (poori class ki continuous
/// recording nahi — us backend infra ke bina ye hi buildable tha).
class TranscriptSegmentModel {
  final String id;
  final String speakerName;
  final double startOffsetSeconds;
  final double endOffsetSeconds;
  final String text;
  final String audioFileUrl;

  TranscriptSegmentModel({
    required this.id,
    required this.speakerName,
    required this.startOffsetSeconds,
    required this.endOffsetSeconds,
    required this.text,
    required this.audioFileUrl,
  });

  /// "3:45" jaisa display format — UI me baar-baar likhna na pade.
  String get timeLabel {
    final total = startOffsetSeconds.round();
    final m = total ~/ 60;
    final s = total % 60;
    return "$m:${s.toString().padLeft(2, '0')}";
  }

  factory TranscriptSegmentModel.fromJson(Map<String, dynamic> json) {
    return TranscriptSegmentModel(
      id: json['id']?.toString() ?? '',
      speakerName: json['speaker_name']?.toString() ?? 'Unknown',
      startOffsetSeconds: (json['start_offset_seconds'] as num?)?.toDouble() ?? 0,
      endOffsetSeconds: (json['end_offset_seconds'] as num?)?.toDouble() ?? 0,
      text: json['text']?.toString() ?? '',
      audioFileUrl: json['audio_file_url']?.toString() ?? '',
    );
  }
}

/// 🔥 NAYA — Feature 5: Revision Deck. Ek flashcard = ek chhota
/// question/term (front) + uska answer/definition (back), exam-revision
/// ke liye. `flip` state yahan model me nahi rakha — purely UI-side
/// (screen apna khud ka `isFlipped` bool rakhega per-card), taaki model
/// sirf data ho, presentation state na ho.
class FlashcardModel {
  final String front;
  final String back;

  FlashcardModel({required this.front, required this.back});

  factory FlashcardModel.fromJson(Map<String, dynamic> json) {
    return FlashcardModel(
      front: json['front']?.toString() ?? '',
      back: json['back']?.toString() ?? '',
    );
  }
}

/// 🔥 NAYA — Feature 5: Revision Deck. Poore class (chat + whiteboard +
/// transcript, server-side combine hota hai) se banaya gaya self-revision
/// pack — flashcards + ek MCQ quiz, dono. `AiStudyService.
/// generateRevisionDeck()`/`getSavedRevisionDeck()` isi model ko return
/// karte hain. `quiz` ka raw `Map` format wahi hai jo `_QuizQuestionCard`
/// (study_room_screen.dart) already expect karta hai — isliye yahan alag
/// se koi QuizQuestionModel nahi banaya, taaki widget reuse ho sake.
class RevisionDeckModel {
  final List<FlashcardModel> flashcards;
  final List<Map<String, dynamic>> quiz;
  final DateTime? createdAt;

  RevisionDeckModel({
    required this.flashcards,
    required this.quiz,
    this.createdAt,
  });

  bool get isEmpty => flashcards.isEmpty && quiz.isEmpty;

  factory RevisionDeckModel.fromJson(Map<String, dynamic> json) {
    return RevisionDeckModel(
      flashcards: ((json['flashcards'] as List?) ?? [])
          .map((f) => FlashcardModel.fromJson((f as Map).cast<String, dynamic>()))
          .toList(),
      quiz: ((json['quiz'] as List?) ?? [])
          .map((q) => (q as Map).cast<String, dynamic>())
          .toList(),
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'].toString())
          : null,
    );
  }
}

/// 🔥 NAYA — Feature 6: attendance/consistency streak.
/// `CallApiService.getStudyRoomStreak()` isi model ko return karta hai.
class StudyStreakModel {
  final int currentStreak;
  final int longestStreak;
  final int totalClassesAttended;
  final DateTime? lastAttended;

  StudyStreakModel({
    required this.currentStreak,
    required this.longestStreak,
    required this.totalClassesAttended,
    this.lastAttended,
  });

  factory StudyStreakModel.fromJson(Map<String, dynamic> json) {
    return StudyStreakModel(
      currentStreak: (json['current_streak'] as num?)?.toInt() ?? 0,
      longestStreak: (json['longest_streak'] as num?)?.toInt() ?? 0,
      totalClassesAttended: (json['total_classes_attended'] as num?)?.toInt() ?? 0,
      lastAttended: json['last_attended'] != null
          ? DateTime.tryParse(json['last_attended'].toString())
          : null,
    );
  }
}

/// 🔥 NAYA — Collaborative Study Timer (Pomodoro-style). Sab participants
/// ke beech `timer_update` room event se sync hota hai — countdown khud
/// calculate karne ke bajaye `endAt` timestamp bhejte hain, taaki har
/// device apna hi accurate countdown nikaal sake (network lag se drift
/// na ho).
class StudyTimerState {
  bool isRunning;
  bool isBreak;
  int focusMinutes;
  int breakMinutes;
  DateTime? endAt;

  StudyTimerState({
    this.isRunning = false,
    this.isBreak = false,
    this.focusMinutes = 25,
    this.breakMinutes = 5,
    this.endAt,
  });

  Duration get totalDuration => Duration(minutes: isBreak ? breakMinutes : focusMinutes);

  Map<String, dynamic> toJson() => {
    'isRunning': isRunning,
    'isBreak': isBreak,
    'focusMinutes': focusMinutes,
    'breakMinutes': breakMinutes,
    'endAt': endAt?.toIso8601String(),
  };

  factory StudyTimerState.fromJson(Map<String, dynamic> json) {
    return StudyTimerState(
      isRunning: json['isRunning'] ?? false,
      isBreak: json['isBreak'] ?? false,
      focusMinutes: json['focusMinutes'] ?? 25,
      breakMinutes: json['breakMinutes'] ?? 5,
      endAt: json['endAt'] != null ? DateTime.tryParse(json['endAt'].toString()) : null,
    );
  }
}