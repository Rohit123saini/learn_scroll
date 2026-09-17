import 'package:flutter/material.dart';
import 'package:google_sign_in/google_sign_in.dart';
import '../services/api_service.dart';
import '../services/auth_service.dart';
import '../login/signup_screen.dart';
import '../home.dart';
import 'forgot_password_screen.dart';
import 'complete_profile_screen.dart'; // ✅ Google signup ke baad phone lene ke liye
// 🔥 NAYA — dark mode + i18n. `theme_service.dart`/`language_service.dart`
// jaisa hi pattern jo home.dart use karta hai (dekho ARCHITECTURE doc §3).
import '../l10n/app_localizations.dart';
import 'auth_widgets.dart';

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

  // 🔥 REMOVED — hardcoded `brandColor`/`backgroundColor`/`textColor`
  // (Color(0xFF6366F1) / Colors.white / Color(0xFF0F172A)) poori tarah
  // hata diye gaye hain. Ab har jagah `Theme.of(context).colorScheme` se
  // aata hai (`cs.primary` == AppColors.violet in both themes — same
  // brand color jo theme_service.dart me hai), taaki dark mode me screen
  // khud-ba-khud sahi dikhe — koi alag "dark login screen" nahi banani
  // padi.

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

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(msg),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    );
  }

  Future<void> _login() async {
    if (!_formKey.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context)!;

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

      _snack(res.message ?? l10n.loginSuccessful);

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

      _snack(e.toString().replaceAll("Exception:", "").trim());
    }
  }

  // ✅ Google Sign-In / Sign-Up flow (one button handles both)
  Future<void> _loginWithGoogle() async {
    final l10n = AppLocalizations.of(context)!;
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
        throw Exception(l10n.authGoogleCredentialsFailed);
      }

      final res = await _apiService.loginWithGoogle(idToken);

      if (!mounted) return;

      setState(() => _isGoogleLoading = false);

      if (res.access != null) {
        await AuthService.saveToken(res.access!);
      }

      _snack(res.message ?? l10n.authGoogleSignedIn);

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
        _snack(e.description ?? l10n.authGoogleSignInFailed);
      }
    } catch (e) {
      if (!mounted) return;

      setState(() => _isGoogleLoading = false);

      _snack(e.toString().replaceAll("Exception:", "").trim());
    }
  }

  @override
  Widget build(BuildContext context) {
    final bool anyLoading = _isLoading || _isGoogleLoading;
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      // 🔥 backgroundColor hata diya — Scaffold apne aap
      // ThemeData.scaffoldBackgroundColor (AppTheme.light/dark) use karta
      // hai, jo dark mode me theek se badal jaata hai.
      body: SafeArea(
        child: Stack(
          children: [
            Center(
              child: SingleChildScrollView(
                physics: const BouncingScrollPhysics(),
                padding: const EdgeInsets.symmetric(horizontal: 28.0),
                child: Form(
                  key: _formKey,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.center,
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const SizedBox(height: 46), // language toggle ke liye jagah

                      Container(
                        height: 130,
                        width: 200,
                        alignment: Alignment.center,
                        child: Image.asset(
                          'assets/blogo.png',
                          fit: BoxFit.contain,
                          errorBuilder: (context, error, stackTrace) {
                            return Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                Icon(Icons.menu_book_rounded, size: 50, color: cs.primary),
                                const Spacer(),
                                Text("LearnScroll",
                                    style: TextStyle(color: cs.primary, fontWeight: FontWeight.bold)),
                              ],
                            );
                          },
                        ),
                      ),
                      const SizedBox(height: 20),

                      Text(
                        l10n.loginWelcomeBack,
                        style: TextStyle(
                          fontSize: 30,
                          fontWeight: FontWeight.w800,
                          color: cs.onSurface,
                          letterSpacing: -0.5,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        l10n.loginSubtitle,
                        style: TextStyle(fontSize: 15, color: cs.onSurfaceVariant),
                      ),
                      const SizedBox(height: 35),

                      _buildInputField(
                        controller: _userController,
                        label: l10n.loginUsernameOrEmail,
                        icon: Icons.person_outline_rounded,
                        validator: (val) =>
                            val == null || val.trim().isEmpty ? l10n.loginUsernameRequired : null,
                      ),
                      const SizedBox(height: 20),

                      _buildInputField(
                        controller: _passController,
                        label: l10n.loginPassword,
                        icon: Icons.lock_outline_rounded,
                        isPassword: true,
                        hideText: _hidePassword,
                        onToggleVisibility: () => setState(() => _hidePassword = !_hidePassword),
                        validator: (val) => val == null || val.isEmpty ? l10n.loginPasswordRequired : null,
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
                            l10n.loginForgotPassword,
                            style: TextStyle(
                              color: cs.primary,
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
                            backgroundColor: cs.primary,
                            foregroundColor: cs.onPrimary,
                            elevation: 1.5,
                            shadowColor: cs.primary.withOpacity(0.4),
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(16),
                            ),
                          ),
                          child: _isLoading
                              ? SizedBox(
                                  height: 24,
                                  width: 24,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2.5,
                                    color: cs.onPrimary,
                                  ),
                                )
                              : Text(
                                  l10n.loginSignIn,
                                  style: const TextStyle(
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
                          Expanded(child: Divider(color: cs.outlineVariant)),
                          Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 10),
                            child: Text(
                              l10n.authOr,
                              style: TextStyle(
                                  color: cs.onSurfaceVariant, fontSize: 12, fontWeight: FontWeight.w600),
                            ),
                          ),
                          Expanded(child: Divider(color: cs.outlineVariant)),
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
                            backgroundColor: cs.surface,
                            side: BorderSide(color: cs.outlineVariant, width: 1),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                          ),
                          child: _isGoogleLoading
                              ? SizedBox(
                                  height: 22,
                                  width: 22,
                                  child: CircularProgressIndicator(strokeWidth: 2.5, color: cs.primary),
                                )
                              : Row(
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    Image.asset(
                                      'assets/google_logo.png',
                                      height: 20,
                                      errorBuilder: (context, error, stackTrace) =>
                                          Icon(Icons.g_mobiledata_rounded, size: 26, color: cs.primary),
                                    ),
                                    const SizedBox(width: 10),
                                    Text(
                                      l10n.loginContinueWithGoogle,
                                      style: TextStyle(
                                        fontSize: 15,
                                        fontWeight: FontWeight.w600,
                                        color: cs.onSurface,
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
                            l10n.loginNoAccount,
                            style: TextStyle(color: cs.onSurfaceVariant),
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
                            child: Text(
                              l10n.loginSignUp,
                              style: TextStyle(
                                color: cs.primary,
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
            // 🔥 NAYA — home.dart jaisa hi EN/हिं toggle, top-right corner.
            const Positioned(top: 4, right: 4, child: AuthLanguageToggle()),
          ],
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
    final cs = Theme.of(context).colorScheme;
    return TextFormField(
      controller: controller,
      obscureText: isPassword ? hideText : false,
      validator: validator,
      style: TextStyle(fontSize: 15, color: cs.onSurface, fontWeight: FontWeight.w500),
      autovalidateMode: AutovalidateMode.onUserInteraction,
      decoration: InputDecoration(
        labelText: label,
        labelStyle: TextStyle(color: cs.onSurfaceVariant, fontSize: 14),
        prefixIcon: Icon(icon, color: cs.primary.withOpacity(0.7), size: 22),
        suffixIcon: isPassword
            ? IconButton(
                icon: Icon(
                  hideText ? Icons.visibility_off_outlined : Icons.visibility_outlined,
                  color: cs.onSurfaceVariant,
                  size: 20,
                ),
                onPressed: onToggleVisibility,
              )
            : null,
        filled: true,
        fillColor: cs.surfaceVariant,
        contentPadding: const EdgeInsets.symmetric(vertical: 18, horizontal: 16),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(color: cs.outlineVariant, width: 1),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(color: cs.primary, width: 1.5),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(color: cs.error, width: 1),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(color: cs.error, width: 1.5),
        ),
      ),
    );
  }
}