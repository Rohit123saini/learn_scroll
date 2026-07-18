
import 'dart:convert';
import 'package:http/http.dart' as http;
import '../../utils/api.dart';
import '../../services/auth_service.dart';

class ApiService {
  // Search users
  static Future<List<dynamic>> searchUsers(String query) async {
    try {
      final token = await AuthService.getToken();
      
      final url = Uri.parse("${Api.baseUrl}/profile/search/?search=$query");

      final response = await http.get(
        url,
        headers: {
          'Content-Type': 'application/json',
          if (token!= null) 'Authorization': 'Bearer $token',
        },
      );

      if (response.statusCode == 200) {
        final Map<String, dynamic> responseData = json.decode(response.body);
        if (responseData['status'] == true) {
          return responseData['data'];
        }
      }
      return [];
    } catch (e) {
      print("Search Error: $e");
      return [];
    }
  }
}