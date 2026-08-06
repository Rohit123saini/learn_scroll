import 'package:flutter/material.dart';
import 'package:google_sign_in/google_sign_in.dart';
import '../services/api_service.dart';
import '../services/auth_service.dart';
import '../login/signup_screen.dart';
import '../home.dart';
import 'forgot_password_screen.dart';
import 'complete_profile_screen.dart'; // ✅ Google signup ke baad phone lene ke liye

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _formKey = GlobalKey<FormState>();

  final _userController = TextEditingController();
  final _passController = TextEditingController();

  bool _isLoading = false;
  bool _isGoogleLoading = false;
  bool _hidePassword = true;

  final _apiService = ApiService();

  // ⚠️ REPLACE this with your real Web Client ID (Google Cloud Console ->
  // APIs & Services -> Credentials -> OAuth 2.0 Client IDs -> the one with
  // "Application type: Web application"). Keep it identical to Django's
  // settings.GOOGLE_CLIENT_ID / .env value, and identical to the same
  // constant in signup_screen.dart — both must match.
  static const String _googleWebClientId =
      "384486121301-ls1m94qdskoh3d3jig6fso9mk9q3v9ll.apps.googleusercontent.com";
      

  // ✅ google_sign_in v7.x me GoogleSignIn ab singleton hai — direct
  // constructor (GoogleSignIn(...)) v7.0.0 se hata diya gaya hai.
  final GoogleSignIn _googleSignIn = GoogleSignIn.instance;

  // v7.x me ek explicit initialize() call zaroori hai, authenticate() se
  // pehle — exactly ek baar. Ye Future initState() me start hota hai aur
  // _loginWithGoogle() usko await karta hai taaki race-condition na ho.
  late final Future<void> _googleSignInInit;

  // Premium UI Theme Colors
  static const Color brandColor = Color(0xFF6366F1); // Elegant Indigo
  static const Color backgroundColor = Colors.white;
  static const Color textColor = Color(0xFF0F172A); // Dark Slate

  @override
  void initState() {
    super.initState();
    // serverClientId = Web Client ID (Android isse hi kaam kar jaata hai;
    // iOS ke liye alag se clientId bhi dena padega jab iOS setup karoge —
    // wahi value GoogleService-Info.plist ke "CLIENT_ID" me milegi).
    _googleSignInInit = _googleSignIn.initialize(
      serverClientId: _googleWebClientId,
    );
  }

  @override
  void dispose() {
    _userController.dispose();
    _passController.dispose();
    super.dispose();
  }

  Future<void> _login() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() {
      _isLoading = true;
    });

    try {
      final res = await _apiService.login(
        _userController.text.trim(),
        _passController.text,
      );

      if (!mounted) return;

      setState(() {
        _isLoading = false;
      });

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(res.message ?? "Login Successful!"),
          behavior: SnackBarBehavior.floating,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        ),
      );

      if (res.access != null) {
        await AuthService.saveToken(res.access!);
      }

      if (!mounted) return;

      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (context) => const HomeScreen()),
      );
    } catch (e) {
      if (!mounted) return;

      setState(() {
        _isLoading = false;
      });

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(e.toString().replaceAll("Exception:", "").trim()),
          behavior: SnackBarBehavior.floating,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        ),
      );
    }
  }

  // ✅ Google Sign-In / Sign-Up flow (one button handles both)
  Future<void> _loginWithGoogle() async {
    setState(() => _isGoogleLoading = true);

    try {
      // v7.x me initialize() authenticate() se pehle complete hona zaroori hai.
      await _googleSignInInit;

      // Clear any cached account so the picker always shows up fresh.
      await _googleSignIn.signOut();

      // ✅ v7.x: signIn() hata diya gaya, authenticate() use karo.
      // Cancel karne pe ye GoogleSignInException throw karta hai
      // (return null nahi karta jaisa pehle .signIn() karta tha).
      final googleUser = await _googleSignIn.authenticate();

      // ✅ v7.x: .authentication ab synchronous getter hai (Future nahi).
      final idToken = googleUser.authentication.idToken;

      if (idToken == null) {
        throw Exception("Could not get Google credentials. Please try again.");
      }

      final res = await _apiService.loginWithGoogle(idToken);

      if (!mounted) return;

      setState(() => _isGoogleLoading = false);

      if (res.access != null) {
        await AuthService.saveToken(res.access!);
      }

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(res.message ?? "Signed in with Google"),
          behavior: SnackBarBehavior.floating,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        ),
      );

      if (!mounted) return;

      // If phone is missing (fresh Google signup), collect it before Home.
      if (res.phoneMissing == true) {
        Navigator.pushReplacement(
          context,
          MaterialPageRoute(builder: (context) => const CompleteProfileScreen()),
        );
      } else {
        Navigator.pushReplacement(
          context,
          MaterialPageRoute(builder: (context) => const HomeScreen()),
        );
      }
    } on GoogleSignInException catch (e) {
      // ✅ v7.x: user cancel karega to authenticate() ab null return nahi
      // karta — GoogleSignInException throw karta hai. Ise silently handle
      // karo (error snackbar mat dikhao), baaki sab errors dikhao.
      if (!mounted) return;
      setState(() => _isGoogleLoading = false);

      if (e.code != GoogleSignInExceptionCode.canceled) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(e.description ?? "Google sign-in failed. Please try again."),
            behavior: SnackBarBehavior.floating,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
          ),
        );
      }
    } catch (e) {
      if (!mounted) return;

      setState(() => _isGoogleLoading = false);

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(e.toString().replaceAll("Exception:", "").trim()),
          behavior: SnackBarBehavior.floating,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final bool anyLoading = _isLoading || _isGoogleLoading;

    return Scaffold(
      backgroundColor: backgroundColor,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            physics: const BouncingScrollPhysics(),
            padding: const EdgeInsets.symmetric(horizontal: 28.0),
            child: Form(
              key: _formKey,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.center,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const SizedBox(height: 10),

                  Container(
                    height: 130,
                    width: 200,
                    alignment: Alignment.center,
                    child: Image.asset(
                      'assets/blogo.png',
                      fit: BoxFit.contain,
                      errorBuilder: (context, error, stackTrace) {
                        return const Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(Icons.menu_book_rounded, size: 50, color: brandColor),
                            Spacer(),
                            Text("LearnScroll", style: TextStyle(color: brandColor, fontWeight: FontWeight.bold)),
                          ],
                        );
                      },
                    ),
                  ),
                  const SizedBox(height: 20),

                  const Text(
                    "Welcome Back",
                    style: TextStyle(
                      fontSize: 30,
                      fontWeight: FontWeight.w800,
                      color: textColor,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    "Sign in to continue your journey",
                    style: TextStyle(
                      fontSize: 15,
                      color: textColor.withOpacity(0.5),
                    ),
                  ),
                  const SizedBox(height: 35),

                  _buildInputField(
                    controller: _userController,
                    label: "Username or Email",
                    icon: Icons.person_outline_rounded,
                    validator: (val) => val == null || val.trim().isEmpty ? "Username or Email is required" : null,
                  ),
                  const SizedBox(height: 20),

                  _buildInputField(
                    controller: _passController,
                    label: "Password",
                    icon: Icons.lock_outline_rounded,
                    isPassword: true,
                    hideText: _hidePassword,
                    onToggleVisibility: () => setState(() => _hidePassword = !_hidePassword),
                    validator: (val) => val == null || val.isEmpty ? "Password is required" : null,
                  ),
                  const SizedBox(height: 10),

                  Align(
                    alignment: Alignment.centerRight,
                    child: TextButton(
                      onPressed: () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(builder: (_) => const ForgotPasswordScreen()),
                        );
                      },
                      style: TextButton.styleFrom(padding: EdgeInsets.zero),
                      child: Text(
                        "Forgot Password?",
                        style: TextStyle(
                          color: brandColor.withOpacity(0.8),
                          fontWeight: FontWeight.w600,
                          fontSize: 14,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 25),

                  SizedBox(
                    width: double.infinity,
                    height: 56,
                    child: ElevatedButton(
                      onPressed: anyLoading ? null : _login,
                      style: ElevatedButton.styleFrom(
                        backgroundColor: brandColor,
                        foregroundColor: Colors.white,
                        elevation: 1.5,
                        shadowColor: brandColor.withOpacity(0.4),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(16),
                        ),
                      ),
                      child: _isLoading
                          ? const SizedBox(
                              height: 24,
                              width: 24,
                              child: CircularProgressIndicator(
                                strokeWidth: 2.5,
                                color: Colors.white,
                              ),
                            )
                          : const Text(
                              "Sign In",
                              style: TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                    ),
                  ),

                  const SizedBox(height: 22),

                  // ---------- OR divider ----------
                  Row(
                    children: [
                      Expanded(child: Divider(color: Colors.grey.withOpacity(0.3))),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 10),
                        child: Text(
                          "OR",
                          style: TextStyle(color: textColor.withOpacity(0.4), fontSize: 12, fontWeight: FontWeight.w600),
                        ),
                      ),
                      Expanded(child: Divider(color: Colors.grey.withOpacity(0.3))),
                    ],
                  ),
                  const SizedBox(height: 22),

                  // ---------- Continue with Google ----------
                  SizedBox(
                    width: double.infinity,
                    height: 56,
                    child: OutlinedButton(
                      onPressed: anyLoading ? null : _loginWithGoogle,
                      style: OutlinedButton.styleFrom(
                        backgroundColor: Colors.white,
                        side: BorderSide(color: Colors.grey.withOpacity(0.25), width: 1),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                      ),
                      child: _isGoogleLoading
                          ? const SizedBox(
                              height: 22,
                              width: 22,
                              child: CircularProgressIndicator(strokeWidth: 2.5, color: brandColor),
                            )
                          : Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                Image.asset(
                                  'assets/google_logo.png',
                                  height: 20,
                                  errorBuilder: (context, error, stackTrace) =>
                                      const Icon(Icons.g_mobiledata_rounded, size: 26, color: brandColor),
                                ),
                                const SizedBox(width: 10),
                                Text(
                                  "Continue with Google",
                                  style: TextStyle(
                                    fontSize: 15,
                                    fontWeight: FontWeight.w600,
                                    color: textColor.withOpacity(0.8),
                                  ),
                                ),
                              ],
                            ),
                    ),
                  ),

                  const SizedBox(height: 28),

                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text(
                        "Don't have an account? ",
                        style: TextStyle(color: textColor.withOpacity(0.6)),
                      ),
                      GestureDetector(
                        onTap: () {
                          Navigator.push(
                            context,
                            MaterialPageRoute(
                              builder: (_) => const SignupScreen(),
                            ),
                          );
                        },
                        child: const Text(
                          "Sign Up",
                          style: TextStyle(
                            color: brandColor,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 20),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildInputField({
    required TextEditingController controller,
    required String label,
    required IconData icon,
    bool isPassword = false,
    bool hideText = false,
    VoidCallback? onToggleVisibility,
    required String? Function(String?) validator,
  }) {
    return TextFormField(
      controller: controller,
      obscureText: isPassword ? hideText : false,
      validator: validator,
      style: const TextStyle(fontSize: 15, color: textColor, fontWeight: FontWeight.w500),
      autovalidateMode: AutovalidateMode.onUserInteraction,
      decoration: InputDecoration(
        labelText: label,
        labelStyle: TextStyle(color: textColor.withOpacity(0.4), fontSize: 14),
        prefixIcon: Icon(icon, color: brandColor.withOpacity(0.6), size: 22),
        suffixIcon: isPassword
            ? IconButton(
                icon: Icon(
                  hideText ? Icons.visibility_off_outlined : Icons.visibility_outlined,
                  color: textColor.withOpacity(0.4),
                  size: 20,
                ),
                onPressed: onToggleVisibility,
              )
            : null,
        filled: true,
        fillColor: Colors.white,
        contentPadding: const EdgeInsets.symmetric(vertical: 18, horizontal: 16),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(color: Colors.grey.withOpacity(0.15), width: 1),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: brandColor, width: 1.5),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(color: Colors.red.shade300, width: 1),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: Colors.red, width: 1.5),
        ),
      ),
    );
  }
}