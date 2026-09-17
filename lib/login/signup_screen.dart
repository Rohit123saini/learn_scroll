import 'package:flutter/material.dart';
import 'package:google_sign_in/google_sign_in.dart';
import '../services/api_service.dart';
import '../services/auth_service.dart';
import '../home.dart';
import 'complete_profile_screen.dart'; // ✅ Google signup ke baad phone lene ke liye
// 🔥 NAYA — dark mode + i18n (home.dart jaisa hi pattern).
import '../l10n/app_localizations.dart';
import 'auth_widgets.dart';

class SignupScreen extends StatefulWidget {
  const SignupScreen({super.key});

  @override
  State<SignupScreen> createState() => _SignupScreenState();
}

class _SignupScreenState extends State<SignupScreen> {
  final _formKey = GlobalKey<FormState>();

  final _usernameController = TextEditingController();
  final _emailController = TextEditingController();
  final _firstNameController = TextEditingController();
  final _lastNameController = TextEditingController();
  final _phoneController = TextEditingController();
  final _passwordController = TextEditingController();
  final _confirmPasswordController = TextEditingController();
  final _otpController = TextEditingController();

  bool _isLoading = false;
  bool _isGoogleLoading = false;
  bool _hidePassword = true;
  bool _hideConfirmPassword = true;

  // Country Code State Variable (Default: India +91)
  String _selectedCountryCode = "91";

  // Backend Inline Errors State
  String? _usernameBackendError;
  String? _emailBackendError;
  String? _phoneBackendError;
  String? _globalError;

  final _apiService = ApiService();

  // ⚠️ Same Web Client ID jo login_screen.dart me hai — dono jagah exact
  // same value honi chahiye (Google Cloud Console -> Web application type),
  // aur Django's settings.GOOGLE_CLIENT_ID / .env se bhi match honi chahiye.
  static const String _googleWebClientId =
      "384486121301-ls1m94qdskoh3d3jig6fso9mk9q3v9ll.apps.googleusercontent.com";

  // ✅ google_sign_in v7.x me GoogleSignIn ab singleton hai — direct
  // constructor v7.0.0 se hata diya gaya hai.
  final GoogleSignIn _googleSignIn = GoogleSignIn.instance;

  // v7.x me initialize() authenticate() se pehle ek baar complete hona
  // zaroori hai — initState() me start karke yahan store kar rahe hain.
  late final Future<void> _googleSignInInit;

  // 🔥 REMOVED — hardcoded brandColor/backgroundColor/textColor. Ab poori
  // tarah `Theme.of(context).colorScheme` se aate hain (login_screen.dart
  // jaisa hi fix).

  @override
  void initState() {
    super.initState();
    _googleSignInInit = _googleSignIn.initialize(
      serverClientId: _googleWebClientId,
    );
  }

  @override
  void dispose() {
    _usernameController.dispose();
    _emailController.dispose();
    _firstNameController.dispose();
    _lastNameController.dispose();
    _phoneController.dispose();
    _passwordController.dispose();
    _confirmPasswordController.dispose();
    _otpController.dispose();
    super.dispose();
  }

  void _snack(String msg, {bool success = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(msg),
        backgroundColor: success ? kAuthSuccessColor : null,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    );
  }

  // --- Step 1: Triggered when user clicks Sign Up ---
  Future<void> _initiateSignupFlow() async {
    setState(() {
      _usernameBackendError = null;
      _emailBackendError = null;
      _phoneBackendError = null;
      _globalError = null;
    });

    if (!_formKey.currentState!.validate()) return;

    setState(() {
      _isLoading = true;
    });

    final email = _emailController.text.trim();

    try {
      // Direct ApiService use karke OTP bhej rahe hain
      await _apiService.sendOtp(email);

      setState(() {
        _isLoading = false;
      });

      if (!mounted) return;
      // OTP send successfully, open popup sheet
      _showOtpBottomSheet();
    } catch (e) {
      setState(() {
        _isLoading = false;
        _globalError = e.toString().replaceAll("Exception:", "").trim();
      });
    }
  }

  // --- Step 2: Executes after correct OTP Verification ---
  Future<void> _completeFinalSignup() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() {
      _isLoading = true;
    });

    String fullPhoneNumber = "$_selectedCountryCode${_phoneController.text.trim()}";

    try {
      final res = await _apiService.signup(
        _usernameController.text.trim(),
        _emailController.text.trim(),
        _firstNameController.text.trim(),
        _lastNameController.text.trim(),
        _passwordController.text,
        _confirmPasswordController.text,
        fullPhoneNumber,
      );

      if (!mounted) return;

      setState(() {
        _isLoading = false;
      });

      _snack(res.message ?? l10n.signupSuccessful, success: true);

      // Dono layers close karega (Popup sheet + Signup Page)
      Navigator.pop(context); // Bottom sheet bnd
      Navigator.pop(context); // Signup screen bnd
    } catch (e) {
      if (!mounted) return;

      setState(() {
        _isLoading = false;
        String errorMsg = e.toString().replaceAll("Exception:", "").trim();
        debugPrint("SIGNUP_ERROR: $errorMsg");

        if (errorMsg.toLowerCase().contains("username") || errorMsg.toLowerCase().contains("user already exist")) {
          _usernameBackendError = l10n.signupUsernameExists;
        } else if (errorMsg.toLowerCase().contains("email")) {
          _emailBackendError = l10n.signupEmailExists;
        } else {
          _globalError = errorMsg;
        }
      });

      // Bottom sheet band karke main screen pe aa jao errors dikhane ke liye
      Navigator.pop(context);
      _formKey.currentState!.validate();
    }
  }

  // --- Google Sign-Up flow (same endpoint as login — backend decides signup vs login) ---
  Future<void> _signupWithGoogle() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() {
      _globalError = null;
      _isGoogleLoading = true;
    });

    try {
      // v7.x me initialize() authenticate() se pehle complete hona zaroori hai.
      await _googleSignInInit;

      await _googleSignIn.signOut();

      // ✅ v7.x: signIn() hata diya gaya, authenticate() use karo.
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

      _snack(res.message ?? l10n.signupGoogleSuccessful, success: true);

      if (!mounted) return;

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
      // ✅ v7.x: user cancel karne pe ab GoogleSignInException throw hoti
      // hai (pehle .signIn() null return karta tha) — cancel ko chup-chaap
      // handle karo, baaki errors dikhao.
      if (!mounted) return;
      setState(() => _isGoogleLoading = false);

      if (e.code != GoogleSignInExceptionCode.canceled) {
        setState(() {
          _globalError = e.description ?? l10n.authGoogleSignInFailed;
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isGoogleLoading = false;
        _globalError = e.toString().replaceAll("Exception:", "").trim();
      });
    }
  }

  // --- OTP Verification Pop-up Interface ---
  void _showOtpBottomSheet() {
    _otpController.clear();
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: cs.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (sheetContext) {
        return StatefulBuilder(
          builder: (BuildContext context, StateSetter setModalState) {
            return Padding(
              padding: EdgeInsets.only(
                bottom: MediaQuery.of(context).viewInsets.bottom + 24,
                left: 24,
                right: 24,
                top: 24,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: 40,
                    height: 5,
                    decoration: BoxDecoration(
                      color: cs.onSurfaceVariant.withOpacity(0.4),
                      borderRadius: BorderRadius.circular(10),
                    ),
                  ),
                  const SizedBox(height: 20),
                  Text(
                    l10n.signupVerifyEmail,
                    style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: cs.onSurface),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    "${l10n.signupOtpSentTo}\n${_emailController.text.trim()}",
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 14, color: cs.onSurfaceVariant),
                  ),
                  const SizedBox(height: 24),

                  // OTP Entry Box
                  TextField(
                    controller: _otpController,
                    keyboardType: TextInputType.number,
                    maxLength: 6,
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, letterSpacing: 8, color: cs.onSurface),
                    decoration: InputDecoration(
                      hintText: "000000",
                      hintStyle: TextStyle(color: cs.onSurfaceVariant.withOpacity(0.5), letterSpacing: 8),
                      counterText: "",
                      filled: true,
                      fillColor: cs.surfaceVariant,
                      prefixIcon: Icon(Icons.lock_clock_outlined, color: cs.primary),
                      border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: cs.outlineVariant)),
                      enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: cs.outlineVariant)),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide: BorderSide(color: cs.primary, width: 2),
                      ),
                    ),
                  ),
                  const SizedBox(height: 24),

                  // Button to Trigger Check OTP
                  SizedBox(
                    width: double.infinity,
                    height: 52,
                    child: ElevatedButton(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: cs.primary,
                        foregroundColor: cs.onPrimary,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                        elevation: 0,
                      ),
                      onPressed: _isLoading
                          ? null
                          : () async {
                              final otp = _otpController.text.trim();
                              if (otp.isEmpty || otp.length < 4) {
                                ScaffoldMessenger.of(context).showSnackBar(
                                  SnackBar(content: Text(l10n.signupOtpInvalid)),
                                );
                                return;
                              }

                              setModalState(() => _isLoading = true);

                              try {
                                // Step A: OTP Match Check Karo Django se
                                await _apiService.verifyOtp(_emailController.text.trim(), otp);

                                setModalState(() => _isLoading = false);

                                // Step B: Success hone pe final signup system hit karo
                                await _completeFinalSignup();
                              } catch (e) {
                                setModalState(() => _isLoading = false);
                                ScaffoldMessenger.of(context).showSnackBar(
                                  SnackBar(content: Text(e.toString().replaceAll("Exception:", "").trim())),
                                );
                              }
                            },
                      child: _isLoading
                          ? CircularProgressIndicator(color: cs.onPrimary)
                          : Text(
                              l10n.signupVerifyAndCreate,
                              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                            ),
                    ),
                  ),
                ],
              ),
            );
          },
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final bool anyLoading = _isLoading || _isGoogleLoading;
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      // 🔥 backgroundColor hata diya — theme se aata hai ab.
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        leading: BackButton(color: cs.onSurface),
        // 🔥 NAYA — language toggle top bar me.
        actions: const [
          Padding(padding: EdgeInsets.only(right: 8), child: AuthLanguageToggle()),
        ],
      ),
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
                  Text(
                    l10n.signupCreateAccount,
                    style: TextStyle(
                      fontSize: 32,
                      fontWeight: FontWeight.w800,
                      color: cs.onSurface,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    l10n.signupSubtitle,
                    style: TextStyle(fontSize: 15, color: cs.onSurfaceVariant),
                  ),
                  const SizedBox(height: 28),

                  // ---------- Continue with Google (fastest path) ----------
                  SizedBox(
                    width: double.infinity,
                    height: 56,
                    child: OutlinedButton(
                      onPressed: anyLoading ? null : _signupWithGoogle,
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
                                  l10n.signupWithGoogle,
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
                  const SizedBox(height: 22),

                  Row(
                    children: [
                      Expanded(child: Divider(color: cs.outlineVariant)),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 10),
                        child: Text(
                          l10n.signupOrEmail,
                          style: TextStyle(color: cs.onSurfaceVariant, fontSize: 11, fontWeight: FontWeight.w600),
                        ),
                      ),
                      Expanded(child: Divider(color: cs.outlineVariant)),
                    ],
                  ),
                  const SizedBox(height: 22),

                  // Username Field
                  _buildInputField(
                    controller: _usernameController,
                    label: l10n.signupUsername,
                    icon: Icons.person_outline_rounded,
                    backendError: _usernameBackendError,
                    onChanged: (_) {
                      if (_usernameBackendError != null) {
                        setState(() => _usernameBackendError = null);
                      }
                    },
                    validator: (val) => val == null || val.trim().isEmpty ? l10n.signupUsernameRequired : null,
                  ),
                  const SizedBox(height: 18),

                  // Email Field
                  _buildInputField(
                    controller: _emailController,
                    label: l10n.signupEmail,
                    icon: Icons.mail_outline_rounded,
                    type: TextInputType.emailAddress,
                    backendError: _emailBackendError,
                    onChanged: (_) {
                      if (_emailBackendError != null) {
                        setState(() => _emailBackendError = null);
                      }
                    },
                    validator: (val) {
                      if (val == null || val.trim().isEmpty) return l10n.signupEmailRequired;
                      final emailRegex = RegExp(r'^[\w\-.]+@([\w-]+\.)+[\w-]{2,4}$');
                      if (!emailRegex.hasMatch(val.trim())) return l10n.signupEmailInvalid;
                      return null;
                    },
                  ),
                  const SizedBox(height: 18),

                  // Contact Number Input Field
                  _buildInputField(
                    controller: _phoneController,
                    label: l10n.signupContact,
                    icon: Icons.phone_android_rounded,
                    type: TextInputType.phone,
                    backendError: _phoneBackendError,
                    onChanged: (_) {
                      if (_phoneBackendError != null) {
                        setState(() => _phoneBackendError = null);
                      }
                    },
                    prefixWidget: Padding(
                      padding: const EdgeInsets.only(left: 12.0, right: 4.0),
                      child: DropdownButtonHideUnderline(
                        child: DropdownButton<String>(
                          value: _selectedCountryCode,
                          style: TextStyle(fontSize: 15, color: cs.onSurface, fontWeight: FontWeight.w600),
                          dropdownColor: cs.surface,
                          icon: Icon(Icons.arrow_drop_down, color: cs.primary, size: 20),
                          onChanged: (String? newValue) {
                            if (newValue != null) {
                              setState(() {
                                _selectedCountryCode = newValue;
                              });
                            }
                          },
                          items: <Map<String, String>>[
                            {"code": "91", "label": "+91 (IN)"},
                            {"code": "1", "label": "+1 (US)"},
                            {"code": "44", "label": "+44 (UK)"},
                            {"code": "971", "label": "+971 (UAE)"},
                          ].map<DropdownMenuItem<String>>((Map<String, String> country) {
                            return DropdownMenuItem<String>(
                              value: country["code"],
                              child: Text(country["label"]!, style: TextStyle(color: cs.onSurface)),
                            );
                          }).toList(),
                        ),
                      ),
                    ),
                    validator: (val) {
                      if (val == null || val.trim().isEmpty) return l10n.signupContactRequired;
                      if (val.trim().length < 10) return l10n.signupContactInvalid;
                      return null;
                    },
                  ),
                  const SizedBox(height: 18),

                  // First Name & Last Name Row
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: _buildInputField(
                          controller: _firstNameController,
                          label: l10n.signupFirstName,
                          icon: Icons.badge_outlined,
                          validator: (val) => val == null || val.trim().isEmpty ? l10n.signupFieldRequired : null,
                        ),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: _buildInputField(
                          controller: _lastNameController,
                          label: l10n.signupLastName,
                          icon: Icons.badge_outlined,
                          validator: (val) => val == null || val.trim().isEmpty ? l10n.signupFieldRequired : null,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 18),

                  // Password Field
                  _buildInputField(
                    controller: _passwordController,
                    label: l10n.signupPassword,
                    icon: Icons.lock_outline_rounded,
                    isPassword: true,
                    hideText: _hidePassword,
                    onToggleVisibility: () => setState(() => _hidePassword = !_hidePassword),
                    validator: (val) {
                      if (val == null || val.isEmpty) return l10n.signupPasswordRequired;
                      if (val.length < 8) return l10n.signupPasswordMinLength;
                      return null;
                    },
                  ),
                  const SizedBox(height: 18),

                  // Confirm Password Field
                  _buildInputField(
                    controller: _confirmPasswordController,
                    label: l10n.signupConfirmPassword,
                    icon: Icons.lock_outline_rounded,
                    isPassword: true,
                    hideText: _hideConfirmPassword,
                    onToggleVisibility: () => setState(() => _hideConfirmPassword = !_hideConfirmPassword),
                    validator: (val) {
                      if (val == null || val.isEmpty) return l10n.signupConfirmPasswordRequired;
                      if (val != _passwordController.text) return l10n.signupPasswordsNoMatch;
                      return null;
                    },
                  ),

                  if (_globalError != null) ...[
                    const SizedBox(height: 14),
                    Text(
                      _globalError!,
                      textAlign: TextAlign.center,
                      style: TextStyle(color: cs.error, fontSize: 14, fontWeight: FontWeight.w500),
                    ),
                  ],

                  const SizedBox(height: 30),

                  // main submit button
                  SizedBox(
                    width: double.infinity,
                    height: 54,
                    child: ElevatedButton(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: cs.primary,
                        foregroundColor: cs.onPrimary,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                        elevation: 0,
                      ),
                      onPressed: anyLoading ? null : _initiateSignupFlow,
                      child: _isLoading
                          ? CircularProgressIndicator(color: cs.onPrimary)
                          : Text(
                              l10n.signupButton,
                              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
                            ),
                    ),
                  ),
                  const SizedBox(height: 24),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  // --- Input UI Helper ---
  Widget _buildInputField({
    required TextEditingController controller,
    required String label,
    required IconData icon,
    TextInputType type = TextInputType.text,
    bool isPassword = false,
    bool hideText = false,
    VoidCallback? onToggleVisibility,
    String? backendError,
    ValueChanged<String>? onChanged,
    Widget? prefixWidget,
    required FormFieldValidator<String> validator,
  }) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: cs.onSurface),
        ),
        const SizedBox(height: 8),
        TextFormField(
          controller: controller,
          keyboardType: type,
          obscureText: isPassword && hideText,
          onChanged: onChanged,
          style: TextStyle(fontSize: 15, color: cs.onSurface),
          decoration: InputDecoration(
            isDense: true,
            contentPadding: const EdgeInsets.symmetric(vertical: 16, horizontal: 16),
            errorText: backendError,
            prefixIcon: prefixWidget ?? Icon(icon, color: cs.primary.withOpacity(0.7), size: 22),
            suffixIcon: isPassword
                ? IconButton(
                    icon: Icon(hideText ? Icons.visibility_off_outlined : Icons.visibility_outlined,
                        color: cs.onSurfaceVariant, size: 20),
                    onPressed: onToggleVisibility,
                  )
                : null,
            filled: true,
            fillColor: cs.surfaceVariant,
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: BorderSide(color: cs.outlineVariant, width: 1),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: BorderSide(color: cs.primary, width: 2),
            ),
            errorBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: BorderSide(color: cs.error, width: 1),
            ),
            focusedErrorBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: BorderSide(color: cs.error, width: 2),
            ),
          ),
          validator: validator,
        ),
      ],
    );
  }
}