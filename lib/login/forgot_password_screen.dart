import 'dart:async';
import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../l10n/app_localizations.dart';
import 'auth_widgets.dart';
import 'login_screen.dart'; // ✅ Reset ke baad wapas login pe bhejne ke liye

// 🔧 CLEANUP — is file me pehle isi logic ka ek poora commented-out
// (~380 line) purana draft upar pada hua tha. Dead code hata diya gaya —
// neeche jo live class hai wahi actually compile/run hoti thi.
//
// 🔥 FIX (B-7 — backend ke login_app_reference.md §10.8 / views.py check
// karke) — yeh screen pehle generic send-otp/verify-otp/change-password
// use kar rahi thi. `verifyOtp()` VerifyOTPView hit karta hai, jiska
// "user_exists" branch actually OTP se user ko LOGIN kar deta hai (session
// bana deta hai) aur usका access token phir `change-password` (authenticated
// endpoint) ke liye reuse hota tha — kaam to ho jaata tha, par backend team
// ne jaan-boojh kar ek alag, dedicated `ForgotPasswordView`/`ResetPasswordView`
// pair banaya hai jo kabhi token issue nahi karta / kabhi login nahi karta
// (dekho views.py ka B-7 comment block). Ab yeh screen wahi do endpoints
// use karti hai:
//   Step 1 (identity) → forgotPassword()  → POST /auth/forgot-password/
//   Step 3 (new pass) → resetPassword()   → POST /auth/reset-password/  (otp
//                        yahi ek call check karta hai — koi alag "verify"
//                        step backend me hai hi nahi ab)
// Step 2 (OTP entry) isliye ab sirf local hai — khaali field check karta
// hai, koi API call nahi karta; asli OTP check step 3 ke submit pe hota
// hai. Isi wajah se purana "Skip" button bhi hata diya gaya — pehle woh
// isliye kaam karta tha kyunki step 2 ka VerifyOTPView call chupke se
// login kar deta tha; naya flow kabhi session banata hi nahi, isliye
// "skip password reset, seedha andar chale jao" ab possible hi nahi hai.

class ForgotPasswordScreen extends StatefulWidget {
  const ForgotPasswordScreen({super.key});

  @override
  State<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
}

class _ForgotPasswordScreenState extends State<ForgotPasswordScreen> {
  final _formKey = GlobalKey<FormState>();

  // Controllers
  final _identityController = TextEditingController();
  final _otpController = TextEditingController();
  final _newPasswordController = TextEditingController();
  final _confirmPasswordController = TextEditingController();

  final _apiService = ApiService();

  // Flow States
  bool _isLoading = false;
  int _currentStep = 1; // 1: Send OTP, 2: Verify OTP, 3: Reset Password
  bool _hidePassword = true;

  // 🔥 NAYA — resend cooldown. Backend `forgot_password` scope already
  // 5/min pe rate-limited hai (settings.py), par woh server-side backstop
  // hai — ek local cooldown bhi rakha taaki user ko turant pata chale ki
  // button abhi kaam nahi karega, uske bajaye seedha ek 429/throttled
  // error dikhe.
  static const int _resendCooldownSeconds = 30;
  int _resendSecondsLeft = 0;
  Timer? _resendTimer;

  // 🔥 REMOVED — hardcoded brandColor/textColor. Ab
  // `Theme.of(context).colorScheme` se aate hain (login_screen.dart jaisa
  // hi fix).

  @override
  void dispose() {
    _identityController.dispose();
    _otpController.dispose();
    _newPasswordController.dispose();
    _confirmPasswordController.dispose();
    _resendTimer?.cancel();
    super.dispose();
  }

  void _startResendCooldown() {
    _resendTimer?.cancel();
    setState(() => _resendSecondsLeft = _resendCooldownSeconds);
    _resendTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      if (_resendSecondsLeft <= 1) {
        timer.cancel();
        setState(() => _resendSecondsLeft = 0);
      } else {
        setState(() => _resendSecondsLeft -= 1);
      }
    });
  }

  Future<void> _handleResend() async {
    if (_resendSecondsLeft > 0 || _isLoading) return;
    try {
      await _apiService.forgotPassword(_identityController.text.trim());
      if (!mounted) return;
      _showSnackBar(AppLocalizations.of(context)!.forgotOtpSentSuccess, success: true);
      _startResendCooldown();
    } catch (e) {
      _showSnackBar(e.toString().replaceAll("Exception: ", ""), success: false);
    }
  }

  // STEP 1: Reset code request — POST /auth/forgot-password/
  Future<void> _handleSendOtp() async {
    if (!_formKey.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context)!;

    setState(() => _isLoading = true);

    try {
      await _apiService.forgotPassword(_identityController.text.trim());
      _showSnackBar(l10n.forgotOtpSentSuccess, success: true);
      _startResendCooldown();
      setState(() {
        _currentStep = 2;
      });
    } catch (e) {
      _showSnackBar(e.toString().replaceAll("Exception: ", ""), success: false);
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  // STEP 2: Sirf local check — is naye flow me OTP ka asli verify koi
  // alag endpoint hai hi nahi, wo step 3 ke reset-password call ke saath
  // hi hota hai. Yahan bas itna dekh lete hain ki field khaali na ho.
  void _handleContinueFromOtp() {
    final l10n = AppLocalizations.of(context)!;
    if (_otpController.text.trim().isEmpty) {
      _showSnackBar(l10n.forgotOtpRequired, success: false);
      return;
    }
    setState(() => _currentStep = 3);
  }

  // STEP 3: Live API — POST /auth/reset-password/ (otp + new password
  // ek hi call me check hote hain), phir wapas Login screen par.
  Future<void> _handleResetPassword() async {
    if (!_formKey.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context)!;

    setState(() => _isLoading = true);

    try {
      await _apiService.resetPassword(
        emailOrPhone: _identityController.text.trim(),
        otp: _otpController.text.trim(),
        newPassword: _newPasswordController.text,
        confirmPassword: _confirmPasswordController.text,
      );

      _showSnackBar(l10n.forgotPasswordUpdated, success: true);
      _navigateToLogin();
    } catch (e) {
      _showSnackBar(e.toString().replaceAll("Exception: ", ""), success: false);
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  // Reset kabhi session nahi banata (backend never issues a token yahan) —
  // isliye Home nahi, Login par bhejna hai, jahan user naye password se
  // khud sign in kare.
  void _navigateToLogin() {
    if (!mounted) return;
    Navigator.pushAndRemoveUntil(
      context,
      MaterialPageRoute(builder: (context) => const LoginScreen()),
      (route) => false,
    );
  }

  void _showSnackBar(String message, {required bool success}) {
    if (!mounted) return;
    final cs = Theme.of(context).colorScheme;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: success ? kAuthSuccessColor : cs.error,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      // 🔥 backgroundColor hata diya — theme se aata hai ab.
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        leading: IconButton(
          icon: Icon(Icons.arrow_back_ios_new_rounded, color: cs.onSurface, size: 20),
          onPressed: () => Navigator.pop(context),
        ),
        // 🔥 REMOVED — "Skip" button. Woh sirf isliye valid tha kyunki
        // purana step-2 (VerifyOTPView) chupke se ek session bana deta
        // tha, to "skip" karke seedha us session ke saath Home jaana
        // sambhav tha. Naya ForgotPasswordView/ResetPasswordView flow
        // kabhi login nahi karta, isliye ab koi session hai hi nahi jise
        // skip karke andar jaaya ja sake — password reset karna hi ek
        // raasta hai.
        actions: const [
          // 🔥 NAYA — language toggle top bar me.
          Padding(padding: EdgeInsets.only(right: 8, left: 4), child: AuthLanguageToggle()),
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
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    _currentStep == 1
                        ? l10n.forgotTitleStep1
                        : _currentStep == 2
                            ? l10n.forgotTitleStep2
                            : l10n.forgotTitleStep3,
                    style: TextStyle(
                      fontSize: 28,
                      fontWeight: FontWeight.w800,
                      color: cs.onSurface,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _currentStep == 1
                        ? l10n.forgotSubtitleStep1
                        : _currentStep == 2
                            ? l10n.forgotSubtitleStep2
                            : l10n.forgotSubtitleStep3,
                    style: TextStyle(
                      fontSize: 15,
                      color: cs.onSurfaceVariant,
                      height: 1.4,
                    ),
                  ),
                  const SizedBox(height: 35),

                  // STEP 1 UI: Identity Input
                  if (_currentStep == 1) ...[
                    _buildInputField(
                      controller: _identityController,
                      label: l10n.forgotIdentityLabel,
                      icon: Icons.alternate_email_rounded,
                      validator: (val) => val == null || val.trim().isEmpty ? l10n.forgotFieldRequired : null,
                    ),
                  ],

                  // STEP 2 UI: OTP Input
                  if (_currentStep == 2) ...[
                    _buildInputField(
                      controller: _otpController,
                      label: l10n.forgotOtpLabel,
                      icon: Icons.domain_verification_rounded,
                      keyboardType: TextInputType.number,
                      validator: (val) => val == null || val.trim().isEmpty ? l10n.forgotOtpRequired : null,
                    ),
                  ],

                  // Resend — step 2 (waiting on the code) aur step 3
                  // (password type karte-karte code expire ho sakta hai)
                  // dono me kaam ka hai. Local cooldown ke dauran disabled
                  // rehta hai (dekho `_startResendCooldown`).
                  if (_currentStep == 2 || _currentStep == 3) ...[
                    const SizedBox(height: 14),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton(
                        onPressed: _resendSecondsLeft > 0 || _isLoading ? null : _handleResend,
                        style: TextButton.styleFrom(padding: EdgeInsets.zero),
                        child: Text(
                          _resendSecondsLeft > 0 ? "${l10n.retry} ($_resendSecondsLeft)" : l10n.retry,
                          style: TextStyle(
                            color: _resendSecondsLeft > 0 ? cs.onSurfaceVariant : cs.primary,
                            fontWeight: FontWeight.w600,
                            fontSize: 13,
                          ),
                        ),
                      ),
                    ),
                  ],

                  // STEP 3 UI: Password Update Inputs
                  if (_currentStep == 3) ...[
                    _buildInputField(
                      controller: _newPasswordController,
                      label: l10n.forgotNewPassword,
                      icon: Icons.lock_outline_rounded,
                      isPassword: true,
                      hideText: _hidePassword,
                      onToggleVisibility: () => setState(() => _hidePassword = !_hidePassword),
                      validator: (val) => val == null || val.isEmpty ? l10n.forgotNewPasswordRequired : null,
                    ),
                    const SizedBox(height: 20),
                    _buildInputField(
                      controller: _confirmPasswordController,
                      label: l10n.forgotConfirmNewPassword,
                      icon: Icons.lock_reset_rounded,
                      isPassword: true,
                      hideText: _hidePassword,
                      onToggleVisibility: () => setState(() => _hidePassword = !_hidePassword),
                      validator: (val) {
                        if (val == null || val.isEmpty) return l10n.forgotConfirmPasswordRequired;
                        if (val != _newPasswordController.text) return l10n.forgotPasswordsNoMatch;
                        return null;
                      },
                    ),
                  ],

                  const SizedBox(height: 30),

                  // Main Interactive Action Button
                  SizedBox(
                    width: double.infinity,
                    height: 56,
                    child: ElevatedButton(
                      onPressed: _isLoading
                          ? null
                          : _currentStep == 1
                              ? _handleSendOtp
                              : _currentStep == 2
                                  ? _handleContinueFromOtp
                                  : _handleResetPassword,
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
                              _currentStep == 1
                                  ? l10n.forgotSendOtp
                                  : _currentStep == 2
                                      ? l10n.forgotVerifyOtp
                                      : l10n.forgotUpdatePassword,
                              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                            ),
                    ),
                  ),
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
    TextInputType keyboardType = TextInputType.text,
    VoidCallback? onToggleVisibility,
    required String? Function(String?) validator,
  }) {
    final cs = Theme.of(context).colorScheme;
    return TextFormField(
      controller: controller,
      obscureText: isPassword ? hideText : false,
      validator: validator,
      keyboardType: keyboardType,
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