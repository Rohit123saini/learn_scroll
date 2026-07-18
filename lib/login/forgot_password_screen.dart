// import 'package:flutter/material.dart';
// import '../services/api_service.dart';
// import '../services/auth_service.dart'; // ✅ AuthService Import kiya
// import '../home.dart'; // ✅ HomeScreen Import kiya

// class ForgotPasswordScreen extends StatefulWidget {
//   const ForgotPasswordScreen({super.key});

//   @override
//   State<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
// }

// class _ForgotPasswordScreenState extends State<ForgotPasswordScreen> {
//   final _formKey = GlobalKey<FormState>();
  
//   // Controllers
//   final _identityController = TextEditingController();
//   final _otpController = TextEditingController();
//   final _newPasswordController = TextEditingController();
//   final _confirmPasswordController = TextEditingController();

//   final _apiService = ApiService();

//   // Flow States
//   bool _isLoading = false;
//   int _currentStep = 1; // 1: Send OTP, 2: Verify OTP, 3: Reset Password Option
//   bool _hidePassword = true;

//   // Premium UI Theme Colors
//   static const Color brandColor = Color(0xFF6366F1);
//   static const Color textColor = Color(0xFF0F172A);

//   @override
//   void dispose() {
//     _identityController.dispose();
//     _otpController.dispose();
//     _newPasswordController.dispose();
//     _confirmPasswordController.dispose();
//     super.dispose();
//   }

//   // STEP 1: Send OTP
//   Future<void> _handleSendOtp() async {
//     if (!_formKey.currentState!.validate()) return;

//     setState(() => _isLoading = true);

//     try {
//       await _apiService.sendOtp(_identityController.text.trim());
//       _showSnackBar("OTP sent successfully!", Colors.black87);
//       setState(() {
//         _currentStep = 2;
//       });
//     } catch (e) {
//       _showSnackBar(e.toString().replaceAll("Exception: ", ""), Colors.redAccent);
//     } finally {
//       if (mounted) setState(() => _isLoading = false);
//     }
//   }

//   // STEP 2: Verify OTP aur Token Save Karna
//   Future<void> _handleVerifyOtp() async {
//     if (_otpController.text.trim().isEmpty) {
//       _showSnackBar("Please enter the OTP", Colors.redAccent);
//       return;
//     }

//     setState(() => _isLoading = true);

//     try {
//       // API verify call se token receive kiya
//       final token = await _apiService.verifyOtp(
//         _identityController.text.trim(),
//         _otpController.text.trim(),
//       );
      
//       // ✅ Token ko AuthService me save kiya
//       if (token != null) {
//         await AuthService.saveToken(token);
//       }

//       _showSnackBar("OTP Verified Successfully!", Colors.black87);
      
//       setState(() {
//         _currentStep = 3; // Password change ya Skip screen par le gaye
//       });
//     } catch (e) {
//       _showSnackBar(e.toString().replaceAll("Exception: ", ""), Colors.redAccent);
//     } finally {
//       if (mounted) setState(() => _isLoading = false);
//     }
//   }

//   // STEP 3 (Option A): Password Change Karke Home Pe Jana
//   Future<void> _handleResetPassword() async {
//     if (!_formKey.currentState!.validate()) return;

//     setState(() => _isLoading = true);

//     try {
//       // TODO: Agar aapki koi password update API hai to yahan call karein.
//       await Future.delayed(const Duration(seconds: 1)); // Mock delay

//       _showSnackBar("Password updated successfully!", Colors.black87);
//       _navigateToHome();
//     } catch (e) {
//       _showSnackBar(e.toString(), Colors.redAccent);
//     } finally {
//       if (mounted) setState(() => _isLoading = false);
//     }
//   }

//   // STEP 3 (Option B): Skip Karke Seedha Home Pe Jana
//   void _handleSkipPassword() {
//     _showSnackBar("Logged in successfully!", Colors.black87);
//     _navigateToHome();
//   }

//   // Common function home par bhejne ke liye aur stack clear karne ke liye
//   void _navigateToHome() {
//     if (!mounted) return;
//     Navigator.pushAndRemoveUntil(
//       context,
//       MaterialPageRoute(builder: (context) => const HomeScreen()),
//       (route) => false, // Isse saare pichle screens back-stack se hat jayenge
//     );
//   }

//   void _showSnackBar(String message, Color bgColor) {
//     if (!mounted) return;
//     ScaffoldMessenger.of(context).showSnackBar(
//       SnackBar(
//         content: Text(message),
//         behavior: SnackBarBehavior.floating,
//         shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
//       ),
//     );
//   }

//   @override
//   Widget build(BuildContext context) {
//     return Scaffold(
//       backgroundColor: Colors.white,
//       appBar: AppBar(
//         backgroundColor: Colors.white,
//         elevation: 0,
//         leading: IconButton(
//           icon: const Icon(Icons.arrow_back_ios_new_rounded, color: textColor, size: 20),
//           onPressed: () => Navigator.pop(context),
//         ),
//         actions: [
//           // ✅ Jab user Step 3 par ho, tabhi "Skip" button top bar me dikhega
//           if (_currentStep == 3)
//             TextButton(
//               onPressed: _isLoading ? null : _handleSkipPassword,
//               child: const Text(
//                 "Skip",
//                 style: TextStyle(
//                   color: brandColor,
//                   fontWeight: FontWeight.bold,
//                   fontSize: 16,
//                 ),
//               ),
//             ),
//         ],
//       ),
//       body: SafeArea(
//         child: Center(
//           child: SingleChildScrollView(
//             physics: const BouncingScrollPhysics(),
//             padding: const EdgeInsets.symmetric(horizontal: 28.0),
//             child: Form(
//               key: _formKey,
//               child: Column(
//                 crossAxisAlignment: CrossAxisAlignment.start,
//                 children: [
//                   Text(
//                     _currentStep == 1 
//                         ? "Forgot Password?" 
//                         : _currentStep == 2 
//                             ? "Verify OTP" 
//                             : "Reset Password",
//                     style: const TextStyle(
//                       fontSize: 28,
//                       fontWeight: FontWeight.w800,
//                       color: textColor,
//                       letterSpacing: -0.5,
//                     ),
//                   ),
//                   const SizedBox(height: 8),
//                   Text(
//                     _currentStep == 1
//                         ? "Enter your registered email or phone number to receive a verification OTP."
//                         : _currentStep == 2
//                             ? "Enter the 6-digit verification code sent to your dynamic contact point."
//                             : "You can update your password now, or click 'Skip' to proceed to Home.",
//                     style: TextStyle(
//                       fontSize: 15,
//                       color: textColor.withOpacity(0.6),
//                       height: 1.4,
//                     ),
//                   ),
//                   const SizedBox(height: 35),

//                   // STEP 1 UI
//                   if (_currentStep == 1) ...[
//                     _buildInputField(
//                       controller: _identityController,
//                       label: "Email or Phone Number",
//                       icon: Icons.alternate_email_rounded,
//                       validator: (val) => val == null || val.trim().isEmpty ? "This field is required" : null,
//                     ),
//                   ],

//                   // STEP 2 UI
//                   if (_currentStep == 2) ...[
//                     _buildInputField(
//                       controller: _otpController,
//                       label: "Enter OTP Code",
//                       icon: Icons.domain_verification_rounded,
//                       keyboardType: TextInputType.number,
//                       validator: (val) => val == null || val.trim().isEmpty ? "OTP cannot be empty" : null,
//                     ),
//                   ],

//                   // STEP 3 UI
//                   if (_currentStep == 3) ...[
//                     _buildInputField(
//                       controller: _newPasswordController,
//                       label: "New Password",
//                       icon: Icons.lock_outline_rounded,
//                       isPassword: true,
//                       hideText: _hidePassword,
//                       onToggleVisibility: () => setState(() => _hidePassword = !_hidePassword),
//                       validator: (val) => val == null || val.isEmpty ? "New password is required" : null,
//                     ),
//                     const SizedBox(height: 20),
//                     _buildInputField(
//                       controller: _confirmPasswordController,
//                       label: "Confirm New Password",
//                       icon: Icons.lock_reset_rounded,
//                       isPassword: true,
//                       hideText: _hidePassword,
//                       onToggleVisibility: () => setState(() => _hidePassword = !_hidePassword),
//                       validator: (val) {
//                         if (val == null || val.isEmpty) return "Confirm password is required";
//                         if (val != _newPasswordController.text) return "Passwords do not match";
//                         return null;
//                       },
//                     ),
//                   ],

//                   const SizedBox(height: 30),

//                   // Main Button
//                   SizedBox(
//                     width: double.infinity,
//                     height: 56,
//                     child: ElevatedButton(
//                       onPressed: _isLoading 
//                           ? null 
//                           : _currentStep == 1 
//                               ? _handleSendOtp 
//                               : _currentStep == 2 
//                                   ? _handleVerifyOtp 
//                                   : _handleResetPassword,
//                       style: ElevatedButton.styleFrom(
//                         backgroundColor: brandColor,
//                         foregroundColor: Colors.white,
//                         elevation: 1.5,
//                         shadowColor: brandColor.withOpacity(0.4),
//                         shape: RoundedRectangleBorder(
//                           borderRadius: BorderRadius.circular(16),
//                         ),
//                       ),
//                       child: _isLoading
//                           ? const SizedBox(
//                               height: 24,
//                               width: 24,
//                               child: CircularProgressIndicator(
//                                 strokeWidth: 2.5,
//                                 color: Colors.white,
//                               ),
//                             )
//                           : Text(
//                               _currentStep == 1 
//                                   ? "Send OTP" 
//                                   : _currentStep == 2 
//                                       ? "Verify OTP" 
//                                       : "Update Password",
//                               style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
//                             ),
//                     ),
//                   ),
//                 ],
//               ),
//             ),
//           ),
//         ),
//       ),
//     );
//   }

//   Widget _buildInputField({
//     required TextEditingController controller,
//     required String label,
//     required IconData icon,
//     bool isPassword = false,
//     bool hideText = false,
//     TextInputType keyboardType = TextInputType.text,
//     VoidCallback? onToggleVisibility,
//     required String? Function(String?) validator,
//   }) {
//     return TextFormField(
//       controller: controller,
//       obscureText: isPassword ? hideText : false,
//       validator: validator,
//       keyboardType: keyboardType,
//       style: const TextStyle(fontSize: 15, color: textColor, fontWeight: FontWeight.w500),
//       autovalidateMode: AutovalidateMode.onUserInteraction,
//       decoration: InputDecoration(
//         labelText: label,
//         labelStyle: TextStyle(color: textColor.withOpacity(0.4), fontSize: 14),
//         prefixIcon: Icon(icon, color: brandColor.withOpacity(0.6), size: 22),
//         suffixIcon: isPassword
//             ? IconButton(
//                 icon: Icon(
//                   hideText ? Icons.visibility_off_outlined : Icons.visibility_outlined,
//                   color: textColor.withOpacity(0.4),
//                   size: 20,
//                 ),
//                 onPressed: onToggleVisibility,
//               )
//             : null,
//         filled: true,
//         fillColor: Colors.white,
//         contentPadding: const EdgeInsets.symmetric(vertical: 18, horizontal: 16),
//         enabledBorder: OutlineInputBorder(
//           borderRadius: BorderRadius.circular(16),
//           borderSide: BorderSide(color: Colors.grey.withOpacity(0.15), width: 1),
//         ),
//         focusedBorder: OutlineInputBorder(
//           borderRadius: BorderRadius.circular(16),
//           borderSide: const BorderSide(color: brandColor, width: 1.5),
//         ),
//         errorBorder: OutlineInputBorder(
//           borderRadius: BorderRadius.circular(16),
//           borderSide: BorderSide(color: Colors.red.shade300, width: 1),
//         ),
//         focusedErrorBorder: OutlineInputBorder(
//           borderRadius: BorderRadius.circular(16),
//           borderSide: const BorderSide(color: Colors.red, width: 1.5),
//         ),
//       ),
//     );
//   }
// }





























import 'dart:convert';
import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../services/auth_service.dart'; // ✅ Token verification aur saving ke liye
import '../home.dart'; // ✅ Password change ke baad redirection ke liye

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

  // UI Theme Colors
  static const Color brandColor = Color(0xFF6366F1);
  static const Color textColor = Color(0xFF0F172A);

  @override
  void dispose() {
    _identityController.dispose();
    _otpController.dispose();
    _newPasswordController.dispose();
    _confirmPasswordController.dispose();
    super.dispose();
  }

  // STEP 1: Send OTP
  Future<void> _handleSendOtp() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() => _isLoading = true);

    try {
      await _apiService.sendOtp(_identityController.text.trim());
      _showSnackBar("OTP sent successfully!", Colors.black87);
      setState(() {
        _currentStep = 2;
      });
    } catch (e) {
      _showSnackBar(e.toString().replaceAll("Exception: ", ""), Colors.redAccent);
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  // STEP 2: Verify OTP aur Token Save Karna
  Future<void> _handleVerifyOtp() async {
    if (_otpController.text.trim().isEmpty) {
      _showSnackBar("Please enter the OTP", Colors.redAccent);
      return;
    }

    setState(() => _isLoading = true);

    try {
      // API verify call se token receive kiya
      final token = await _apiService.verifyOtp(
        _identityController.text.trim(),
        _otpController.text.trim(),
      );
      
      // Token ko AuthService me save kiya taaki pass update API use kar sake
      if (token != null) {
        await AuthService.saveToken(token);
        _showSnackBar("OTP Verified Successfully!", Colors.black87);
        setState(() {
          _currentStep = 3; // Password change screen par le gaye
        });
      } else {
        throw Exception("Token not received. Please try again.");
      }
    } catch (e) {
      _showSnackBar(e.toString().replaceAll("Exception: ", ""), Colors.redAccent);
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  // STEP 3: Live API Se Password Change Karke Home Pe Jana
  Future<void> _handleResetPassword() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() => _isLoading = true);

    try {
      // Live API trigger hui jisme background me saved token jaa raha hai
      await _apiService.changePassword(
        _newPasswordController.text,
        _confirmPasswordController.text,
      );

      _showSnackBar("Password updated successfully!", Colors.black87);
      _navigateToHome();
    } catch (e) {
      _showSnackBar(e.toString().replaceAll("Exception: ", ""), Colors.redAccent);
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  // STEP 3 (Option B): Skip Karke Seedha Home Pe Jana
  void _handleSkipPassword() {
    _showSnackBar("Logged in successfully!", Colors.black87);
    _navigateToHome();
  }

  // Stack clear karke home par redirect karne ke liye
  void _navigateToHome() {
    if (!mounted) return;
    Navigator.pushAndRemoveUntil(
      context,
      MaterialPageRoute(builder: (context) => const HomeScreen()),
      (route) => false,
    );
  }

  void _showSnackBar(String message, Color bgColor) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: bgColor,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_ios_new_rounded, color: textColor, size: 20),
          onPressed: () => Navigator.pop(context),
        ),
        actions: [
          // Jab user Step 3 par ho, tabhi "Skip" button top bar me dikhega
          if (_currentStep == 3)
            TextButton(
              onPressed: _isLoading ? null : _handleSkipPassword,
              child: const Text(
                "Skip",
                style: TextStyle(
                  color: brandColor,
                  fontWeight: FontWeight.bold,
                  fontSize: 16,
                ),
              ),
            ),
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
                        ? "Forgot Password?" 
                        : _currentStep == 2 
                            ? "Verify OTP" 
                            : "Reset Password",
                    style: const TextStyle(
                      fontSize: 28,
                      fontWeight: FontWeight.w800,
                      color: textColor,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _currentStep == 1
                        ? "Enter your registered email or phone number to receive a verification OTP."
                        : _currentStep == 2
                            ? "Enter the 6-digit verification code sent to your dynamic contact point."
                            : "You can update your password now, or click 'Skip' to proceed to Home.",
                    style: TextStyle(
                      fontSize: 15,
                      color: textColor.withOpacity(0.6),
                      height: 1.4,
                    ),
                  ),
                  const SizedBox(height: 35),

                  // STEP 1 UI: Identity Input
                  if (_currentStep == 1) ...[
                    _buildInputField(
                      controller: _identityController,
                      label: "Email or Phone Number",
                      icon: Icons.alternate_email_rounded,
                      validator: (val) => val == null || val.trim().isEmpty ? "This field is required" : null,
                    ),
                  ],

                  // STEP 2 UI: OTP Input
                  if (_currentStep == 2) ...[
                    _buildInputField(
                      controller: _otpController,
                      label: "Enter OTP Code",
                      icon: Icons.domain_verification_rounded,
                      keyboardType: TextInputType.number,
                      validator: (val) => val == null || val.trim().isEmpty ? "OTP cannot be empty" : null,
                    ),
                  ],

                  // STEP 3 UI: Password Update Inputs
                  if (_currentStep == 3) ...[
                    _buildInputField(
                      controller: _newPasswordController,
                      label: "New Password",
                      icon: Icons.lock_outline_rounded,
                      isPassword: true,
                      hideText: _hidePassword,
                      onToggleVisibility: () => setState(() => _hidePassword = !_hidePassword),
                      validator: (val) => val == null || val.isEmpty ? "New password is required" : null,
                    ),
                    const SizedBox(height: 20),
                    _buildInputField(
                      controller: _confirmPasswordController,
                      label: "Confirm New Password",
                      icon: Icons.lock_reset_rounded,
                      isPassword: true,
                      hideText: _hidePassword,
                      onToggleVisibility: () => setState(() => _hidePassword = !_hidePassword),
                      validator: (val) {
                        if (val == null || val.isEmpty) return "Confirm password is required";
                        if (val != _newPasswordController.text) return "Passwords do not match";
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
                                  ? _handleVerifyOtp 
                                  : _handleResetPassword,
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
                          : Text(
                              _currentStep == 1 
                                  ? "Send OTP" 
                                  : _currentStep == 2 
                                      ? "Verify OTP" 
                                      : "Update Password",
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
    return TextFormField(
      controller: controller,
      obscureText: isPassword ? hideText : false,
      validator: validator,
      keyboardType: keyboardType,
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