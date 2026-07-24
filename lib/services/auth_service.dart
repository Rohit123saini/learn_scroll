

import 'package:shared_preferences/shared_preferences.dart';

class AuthService {
  // backend ko har jagah ye token chahiye
  static Future<String?> getToken() async {
    final prefs = await SharedPreferences.getInstance();
    // tera login "access" naam se save karta hai, wahi token hai
    String? token = prefs.getString("access");
    if(token == null) token = prefs.getString("access_token"); // fallback
    return token;
  }

  static Future<String?> getUserId() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString("user_id");
  }

  static Future<void> saveToken(String token) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString("access", token); // backend wala token
    await prefs.setString("access_token", token); // double save taaki kahin miss na ho
  }

  static Future<void> saveUserId(String id) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString("user_id", id);
  }

  static Future<void> logout() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.clear();
  }
}