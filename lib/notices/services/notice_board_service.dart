// lib/notices/services/notice_board_service.dart
//
// Backend endpoint (`core` app, Task 12):
//   GET core/notice-board/?limit=&offset=  -> {count, results:[...]}
//
// Same `Api.baseUrl` + `AuthService.getValidToken()` convention every
// other service in this app already uses (see
// `notifications/services/notification_service.dart`,
// `campus/services/campus_service.dart`) — no new/duplicate pattern.

import 'dart:convert';
import 'package:http/http.dart' as http;

import '../../services/auth_service.dart';
import '../../utils/api.dart';
import '../models/notice_board_models.dart';

class NoticeBoardApiException implements Exception {
  final String message;
  final int? statusCode;
  NoticeBoardApiException(this.message, {this.statusCode});

  @override
  String toString() => message;
}

class NoticeBoardService {
  NoticeBoardService._();

  static const Duration _timeout = Duration(seconds: 15);

  static Future<Map<String, String>> _headers() async {
    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) {
      throw NoticeBoardApiException('NOT_AUTHENTICATED');
    }
    return {
      'Authorization': 'Bearer $token',
      'Content-Type': 'application/json',
    };
  }

  static Future<NoticeBoardPage> fetch({int limit = 30, int offset = 0}) async {
    final uri = Uri.parse('${Api.baseUrl}/core/notice-board/').replace(
      queryParameters: {'limit': '$limit', 'offset': '$offset'},
    );
    final r = await http.get(uri, headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) {
      String message = 'Request failed (${r.statusCode})';
      try {
        final decoded = jsonDecode(utf8.decode(r.bodyBytes));
        if (decoded is Map && decoded['detail'] != null) {
          message = decoded['detail'].toString();
        }
      } catch (_) {
        // body JSON nahi tha — default message hi theek hai
      }
      throw NoticeBoardApiException(message, statusCode: r.statusCode);
    }
    return NoticeBoardPage.fromJson(
      Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map),
    );
  }
}
