import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'auth_service.dart';
import '../model/login_model.dart';
import '../model/signup_model.dart';
// import '../model/signup_model.dart';
import '../utils/api.dart';

class ApiService {

  // ================= LOGIN =================
  Future<LoginResponse> login(
    String username,
    String password,
  ) async {
    final response = await http.post(
      Uri.parse("${Api.baseUrl}/login/"),
      headers: {
        "Content-Type": "application/json",
      },
      body: jsonEncode({
        "username": username,
        "password": password,
      }),
    );

    final data = jsonDecode(response.body);

    if (response.statusCode == 200) {
      SharedPreferences pref = await SharedPreferences.getInstance();
      await pref.setString(
        "access",
        data["token"]["access"],
      );
      await pref.setString(
        "refresh",
        data["token"]["refresh"],
      );
      return LoginResponse.fromJson(data);
    }
    throw Exception(data["message"]);
  }

  // ================= SEND OTP =================
  Future<void> sendOtp(String emailOrPhone) async {
    final response = await http.post(
      Uri.parse("${Api.baseUrl}/login/auth/send-otp/"),
      headers: {
        "Content-Type": "application/json",
      },
      body: jsonEncode({
        "email_or_phone": emailOrPhone,
      }),
    );

    final data = jsonDecode(response.body);

    if (response.statusCode != 200 && response.statusCode != 201) {
      throw Exception(data["message"] ?? "Failed to send OTP");
    }
  }

  // ================= VERIFY OTP =================
  Future<String?> verifyOtp(String emailOrPhone, String otp) async {
    final response = await http.post(
      Uri.parse("${Api.baseUrl}/login/auth/verify-otp/"),
      headers: {
        "Content-Type": "application/json",
      },
      body: jsonEncode({
        "email_or_phone": emailOrPhone,
        "otp": otp,
      }),
    );

    final data = jsonDecode(response.body);

    if (response.statusCode == 200 || response.statusCode == 201) {
      return data["token"]?["access"] ?? data["access"]; 
    }
    
    throw Exception(data["message"] ?? "Invalid OTP code");
  }

  // ================= SIGNUP =================
  Future<SignupResponse> signup(
    String username,
    String email,
    String firstName,
    String lastName,
    String password,
    String confirmPassword,
    String phone,
  ) async {
    final response = await http.post(
      Uri.parse("${Api.baseUrl}/login/signup/"),
      headers: {
        "Content-Type": "application/json",
      },
      body: jsonEncode({
        "username": username,
        "email": email,
        "first_name": firstName,
        "last_name": lastName,
        "password": password,
        "confirm_password": confirmPassword,
        "phone": phone,
      }),
    );

    final data = jsonDecode(response.body);

    if (response.statusCode == 201 || response.statusCode == 200) {
      return SignupResponse.fromJson(data);
    }

    throw Exception(data.toString());
  }

  // ================= CHANGE PASSWORD =================
  // ✅ AB YEH METHOD CLASS KE ANDAR AA GAYA HAI!
  Future<void> changePassword(
    String newPassword,
    String confirmPassword,
  ) async {
    final token = await AuthService.getToken();

    if (token == null) {
      throw Exception("Access token not found. Please verify OTP again.");
    }

    final response = await http.post(
      Uri.parse("${Api.baseUrl}/login/auth/change-password/"),
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer $token",
      },
      body: jsonEncode({
        "new_password": newPassword,
        "confirm_password": confirmPassword,
      }),
    );

    final data = jsonDecode(response.body);

    if (response.statusCode != 200) {
      throw Exception(
        data["message"] ??
        data["detail"] ??
        data["non_field_errors"]?[0] ??
        "Failed to change password",
      );
    }
  }
}