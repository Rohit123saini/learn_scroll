import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'auth_service.dart';
import '../model/login_model.dart';
import '../model/signup_model.dart';
import '../utils/api.dart';

class ApiService {
  // ================= LOGIN (username/password) =================
  Future<LoginResponse> login(String username, String password) async {
    final response = await http.post(
      Uri.parse("${Api.baseUrl}/login/"),
      headers: {"Content-Type": "application/json"},
      body: jsonEncode({"username": username, "password": password}),
    );
    final data = jsonDecode(response.body);
    if (response.statusCode == 200) {
      await _saveSession(data);
      return LoginResponse.fromJson(data);
    }
    throw Exception(data["message"]);
  }

  // ================= GOOGLE LOGIN / SIGNUP =================
  // One endpoint handles both: new email -> account created (signup),
  // existing email -> logged in. Backend tells us which one happened.
  Future<LoginResponse> loginWithGoogle(String idToken) async {
    final response = await http.post(
      Uri.parse("${Api.baseUrl}/login/auth/google/"),
      headers: {"Content-Type": "application/json"},
      body: jsonEncode({"id_token": idToken}),
    );

    final data = jsonDecode(response.body);

    if (response.statusCode == 200) {
      await _saveSession(data);
      return LoginResponse.fromJson(data);
    }

    throw Exception(data["message"] ?? "Google authentication failed");
  }

  // ================= COMPLETE PROFILE (phone number after Google auth) =================
  Future<void> completeProfile(String phone) async {
    final token = await AuthService.getToken();
    if (token == null) {
      throw Exception("Session expired. Please sign in again.");
    }

    final response = await http.post(
      Uri.parse("${Api.baseUrl}/login/auth/complete-profile/"),
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer $token",
      },
      body: jsonEncode({"phone": phone}),
    );

    final data = jsonDecode(response.body);

    if (response.statusCode != 200) {
      throw Exception(
        data["phone"]?[0] ??
            data["message"] ??
            data["detail"] ??
            "Failed to save phone number",
      );
    }
  }

  // ================= SEND OTP =================
  Future<void> sendOtp(String emailOrPhone) async {
    final response = await http.post(
      Uri.parse("${Api.baseUrl}/login/auth/send-otp/"),
      headers: {"Content-Type": "application/json"},
      body: jsonEncode({"email_or_phone": emailOrPhone}),
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
      headers: {"Content-Type": "application/json"},
      body: jsonEncode({"email_or_phone": emailOrPhone, "otp": otp}),
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
      headers: {"Content-Type": "application/json"},
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

  // ================= FORGOT / RESET PASSWORD (B-7 dedicated flow) =================
  // 🔧 login_app_reference.md §10.8 / views.py ForgotPasswordView &
  // ResetPasswordView — deliberately NOT sendOtp()/verifyOtp()/
  // changePassword() above. Those exist for a different job (signup OTP,
  // and passwordless OTP-login which silently starts a session). This
  // pair never issues a token and never logs the caller in — after a
  // successful reset the user is expected to sign in normally with the
  // new password.

  // Step 1 — request a reset code for a known account. Same generic
  // response whether or not the account exists (account-enumeration
  // safe), so there's nothing account-specific to branch on here.
  Future<void> forgotPassword(String emailOrPhone) async {
    final response = await http.post(
      Uri.parse("${Api.baseUrl}/login/auth/forgot-password/"),
      headers: {"Content-Type": "application/json"},
      body: jsonEncode({"email_or_phone": emailOrPhone}),
    );

    if (response.statusCode != 200) {
      throw Exception(_firstError(_safeDecode(response)) ?? "Failed to send reset code");
    }
  }

  // Step 2 — spend the code + set a new password in one call. The OTP
  // itself is only actually checked here (not in a separate "verify"
  // call) — backend's ResetPasswordSerializer validates otp + new
  // password together.
  Future<void> resetPassword({
    required String emailOrPhone,
    required String otp,
    required String newPassword,
    required String confirmPassword,
  }) async {
    final response = await http.post(
      Uri.parse("${Api.baseUrl}/login/auth/reset-password/"),
      headers: {"Content-Type": "application/json"},
      body: jsonEncode({
        "email_or_phone": emailOrPhone,
        "otp": otp,
        "new_password": newPassword,
        "confirm_password": confirmPassword,
      }),
    );

    if (response.statusCode != 200) {
      throw Exception(_firstError(_safeDecode(response)) ?? "Failed to reset password");
    }
  }

  // 🔥 HARDENING — a plain `jsonDecode(response.body)` throws
  // FormatException on any non-JSON body (a proxy timeout page, a raw
  // 502/503 HTML error page, an empty body on some 5xx responses). That
  // exception isn't wrong, exactly, but it surfaces to the user as a
  // useless "FormatException: Unexpected character" snackbar instead of
  // a normal error message. Decode defensively here and fall through to
  // the generic fallback message in forgotPassword/resetPassword above
  // when the body isn't parseable JSON.
  Map<String, dynamic> _safeDecode(http.Response response) {
    try {
      final decoded = jsonDecode(response.body);
      if (decoded is Map<String, dynamic>) return decoded;
      return {};
    } catch (_) {
      return {};
    }
  }

  // Shared error-message picker for the two calls above: view-level
  // failures (expired/locked/wrong code) come back as {"message": "..."},
  // but plain serializer validation failures (weak password, mismatch,
  // blank otp) come back as DRF's default {"field": ["..."]} shape with
  // no "message" key at all — same ambiguity testseries_service.dart's
  // `_fail()` already handles for that app's errors.
  String? _firstError(Map<String, dynamic> data) {
    if (data.isEmpty) return null;
    final direct = data["message"] ?? data["detail"];
    if (direct != null) return direct.toString();
    final first = data.values.first;
    return first is List && first.isNotEmpty ? first.first.toString() : first.toString();
  }

  // ================= internal helper =================
  Future<void> _saveSession(Map<String, dynamic> data) async {
    SharedPreferences pref = await SharedPreferences.getInstance();
    await pref.setString("access", data["token"]["access"]);
    await pref.setString("refresh", data["token"]["refresh"]);
    await pref.setString("access_token", data["token"]["access"]); // double save

    try {
      String uid = "";
      if (data["user"] != null && data["user"]["id"] != null) {
        uid = data["user"]["id"].toString();
      } else if (data["user"] != null && data["user"]["user_id"] != null) {
        uid = data["user"]["user_id"].toString();
      } else if (data["id"] != null) {
        uid = data["id"].toString();
      }
      if (uid.isNotEmpty) {
        await pref.setString("user_id", uid);
      }
    } catch (e) {
      print("user_id save error: $e");
    }
  }
}