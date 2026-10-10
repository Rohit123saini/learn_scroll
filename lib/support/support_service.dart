// lib/support/support_service.dart
//
// Help & feedback API client. Talks to the Django `support` app:
//   GET/POST  /support/tickets/                      support chat
//   GET       /support/tickets/<id>/
//   POST      /support/tickets/<id>/messages/
//   POST      /support/tickets/<id>/close/
//   POST      /support/bug-reports/                  (multipart, optional screenshot)
//   GET/POST  /support/feature-requests/             ("I want this" board)
//   POST      /support/feature-requests/<id>/vote/   (toggle)
//
// Every method throws `SupportException` with a message that is already fit
// to show the user (server `detail` / first field error), so screens only
// need `catch (e) { lsSnack(context, e.toString()) }`.

import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../services/auth_service.dart';
import '../utils/api.dart';

class SupportException implements Exception {
  final String message;
  SupportException(this.message);
  @override
  String toString() => message;
}

// ------------------------------------------------------------------ models
class SupportMessage {
  final String id;
  final String body;
  final bool isStaff;
  final String senderLabel;
  final DateTime createdAt;

  SupportMessage({
    required this.id,
    required this.body,
    required this.isStaff,
    required this.senderLabel,
    required this.createdAt,
  });

  factory SupportMessage.fromJson(Map<String, dynamic> j) => SupportMessage(
        id: '${j['id']}',
        body: (j['body'] as String?) ?? '',
        isStaff: j['is_staff'] == true,
        senderLabel: (j['sender_label'] as String?) ?? '',
        createdAt: DateTime.tryParse('${j['created_at']}')?.toLocal() ?? DateTime.now(),
      );
}

class SupportTicket {
  final String id;
  final String subject;
  final String category;
  final String status; // open | answered | resolved | closed
  final bool hasUnreadReply;
  final DateTime lastMessageAt;
  final String preview;
  final List<SupportMessage> messages; // empty in list responses

  SupportTicket({
    required this.id,
    required this.subject,
    required this.category,
    required this.status,
    required this.hasUnreadReply,
    required this.lastMessageAt,
    required this.preview,
    this.messages = const [],
  });

  bool get isClosed => status == 'closed';

  factory SupportTicket.fromJson(Map<String, dynamic> j) => SupportTicket(
        id: '${j['id']}',
        subject: (j['subject'] as String?) ?? '',
        category: (j['category'] as String?) ?? 'other',
        status: (j['status'] as String?) ?? 'open',
        hasUnreadReply: j['has_unread_reply'] == true,
        lastMessageAt: DateTime.tryParse('${j['last_message_at']}')?.toLocal() ?? DateTime.now(),
        preview: (j['last_message_preview'] as String?) ?? '',
        messages: (j['messages'] is List)
            ? (j['messages'] as List).whereType<Map<String, dynamic>>().map(SupportMessage.fromJson).toList()
            : const [],
      );
}

class FeatureRequest {
  final String id;
  final String title;
  final String description;
  final String status; // open | planned | in_progress | shipped | declined
  final int votes;
  final bool hasVoted;
  final bool isMine;
  final String authorName;

  FeatureRequest({
    required this.id,
    required this.title,
    required this.description,
    required this.status,
    required this.votes,
    required this.hasVoted,
    required this.isMine,
    required this.authorName,
  });

  bool get votingClosed => status == 'shipped' || status == 'declined';

  FeatureRequest copyWith({int? votes, bool? hasVoted}) => FeatureRequest(
        id: id,
        title: title,
        description: description,
        status: status,
        votes: votes ?? this.votes,
        hasVoted: hasVoted ?? this.hasVoted,
        isMine: isMine,
        authorName: authorName,
      );

  factory FeatureRequest.fromJson(Map<String, dynamic> j) => FeatureRequest(
        id: '${j['id']}',
        title: (j['title'] as String?) ?? '',
        description: (j['description'] as String?) ?? '',
        status: (j['status'] as String?) ?? 'open',
        votes: (j['votes_count'] as num?)?.toInt() ?? 0,
        hasVoted: j['has_voted'] == true,
        isMine: j['is_mine'] == true,
        authorName: (j['author_name'] as String?) ?? '',
      );
}

// ----------------------------------------------------------------- service
class SupportService {
  SupportService._();

  static const Duration _timeout = Duration(seconds: 20);

  static Future<Map<String, String>> _headers({bool json = true}) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw SupportException('Please log in again.');
    return {
      'Authorization': 'Bearer $token',
      if (json) 'Content-Type': 'application/json',
    };
  }

  static Uri _uri(String path, [Map<String, String>? query]) =>
      Uri.parse('${Api.baseUrl}/support/$path').replace(queryParameters: query);

  /// Turns any non-2xx response into a SupportException with a readable message.
  static dynamic _decode(http.Response res) {
    dynamic body;
    try {
      body = res.body.isEmpty ? null : jsonDecode(utf8.decode(res.bodyBytes));
    } catch (_) {
      body = null;
    }
    if (res.statusCode >= 200 && res.statusCode < 300) return body;
    throw SupportException(_message(res.statusCode, body));
  }

  static String _message(int status, dynamic body) {
    if (body is Map) {
      final d = body['detail'];
      if (d is String && d.isNotEmpty) return d;
      for (final v in body.values) {
        if (v is List && v.isNotEmpty) return '${v.first}';
        if (v is String && v.isNotEmpty) return v;
      }
    }
    if (status == 429) return 'Too many requests. Please try again a little later.';
    if (status == 413) return 'That file is too large.';
    return 'Something went wrong (error $status). Please try again.';
  }

  static Future<T> _guard<T>(Future<T> Function() run) async {
    try {
      return await run();
    } on SupportException {
      rethrow;
    } on SocketException {
      throw SupportException('No internet connection.');
    } on http.ClientException {
      throw SupportException('No internet connection.');
    } on Exception catch (e) {
      if (e.toString().contains('TimeoutException')) {
        throw SupportException('The server is taking too long. Please try again.');
      }
      throw SupportException('Something went wrong. Please try again.');
    }
  }

  static List<Map<String, dynamic>> _results(dynamic body) {
    final raw = body is Map ? body['results'] : body;
    return raw is List ? raw.whereType<Map<String, dynamic>>().toList() : <Map<String, dynamic>>[];
  }

  // ---- tickets
  static Future<List<SupportTicket>> tickets() => _guard(() async {
        final res = await http.get(_uri('tickets/'), headers: await _headers()).timeout(_timeout);
        return _results(_decode(res)).map(SupportTicket.fromJson).toList();
      });

  static Future<SupportTicket> ticket(String id) => _guard(() async {
        final res = await http.get(_uri('tickets/$id/'), headers: await _headers()).timeout(_timeout);
        return SupportTicket.fromJson(_decode(res) as Map<String, dynamic>);
      });

  static Future<SupportTicket> createTicket({
    required String subject,
    required String category,
    required String message,
  }) =>
      _guard(() async {
        final res = await http
            .post(_uri('tickets/'),
                headers: await _headers(),
                body: jsonEncode({'subject': subject, 'category': category, 'message': message}))
            .timeout(_timeout);
        return SupportTicket.fromJson(_decode(res) as Map<String, dynamic>);
      });

  static Future<SupportMessage> reply(String ticketId, String body) => _guard(() async {
        final res = await http
            .post(_uri('tickets/$ticketId/messages/'), headers: await _headers(), body: jsonEncode({'body': body}))
            .timeout(_timeout);
        return SupportMessage.fromJson(_decode(res) as Map<String, dynamic>);
      });

  static Future<void> closeTicket(String id) => _guard(() async {
        final res = await http.post(_uri('tickets/$id/close/'), headers: await _headers()).timeout(_timeout);
        _decode(res);
      });

  // ---- bug reports
  static Future<void> reportBug({
    required String title,
    required String description,
    String screen = '',
    String appVersion = '',
    String platform = '',
    String deviceInfo = '',
    File? screenshot,
  }) =>
      _guard(() async {
        final req = http.MultipartRequest('POST', _uri('bug-reports/'));
        req.headers.addAll(await _headers(json: false));
        req.fields.addAll({
          'title': title,
          'description': description,
          'screen': screen,
          'app_version': appVersion,
          'platform': platform,
          'device_info': deviceInfo,
        });
        if (screenshot != null) {
          req.files.add(await http.MultipartFile.fromPath('screenshot', screenshot.path));
        }
        final streamed = await req.send().timeout(const Duration(seconds: 60));
        _decode(await http.Response.fromStream(streamed));
      });

  // ---- feature board
  static Future<List<FeatureRequest>> featureRequests({String sort = 'top', String query = ''}) =>
      _guard(() async {
        final res = await http
            .get(_uri('feature-requests/', {'sort': sort, if (query.isNotEmpty) 'q': query}),
                headers: await _headers())
            .timeout(_timeout);
        return _results(_decode(res)).map(FeatureRequest.fromJson).toList();
      });

  static Future<FeatureRequest> createFeatureRequest({required String title, String description = ''}) =>
      _guard(() async {
        final res = await http
            .post(_uri('feature-requests/'),
                headers: await _headers(), body: jsonEncode({'title': title, 'description': description}))
            .timeout(_timeout);
        return FeatureRequest.fromJson(_decode(res) as Map<String, dynamic>);
      });

  /// Toggles my vote. Returns (voted, votesCount) as the server now has them.
  static Future<({bool voted, int votes})> toggleVote(String id) => _guard(() async {
        final res = await http.post(_uri('feature-requests/$id/vote/'), headers: await _headers()).timeout(_timeout);
        final b = _decode(res) as Map<String, dynamic>;
        return (voted: b['voted'] == true, votes: (b['votes_count'] as num?)?.toInt() ?? 0);
      });
}
