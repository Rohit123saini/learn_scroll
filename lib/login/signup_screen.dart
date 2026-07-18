import 'package:flutter/material.dart';
import '../services/api_service.dart';

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

  static const Color brandColor = Color(0xFF6366F1); 
  static const Color backgroundColor = Colors.white; 
  static const Color textColor = Color(0xFF0F172A); 

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

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(res.message ?? "Signup Successful!"),
          backgroundColor: Colors.green,
          behavior: SnackBarBehavior.floating,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        ),
      );

      // Dono layers close karega (Popup sheet + Signup Page)
      Navigator.pop(context); // Bottom sheet bnd
      Navigator.pop(context); // Signup screen bnd
    } catch (e) {
      if (!mounted) return;

      setState(() {
        _isLoading = false;
        String errorMsg = e.toString().replaceAll("Exception:", "").trim();
        print("SIGNUP_ERROR: $errorMsg");

        if (errorMsg.toLowerCase().contains("username") || errorMsg.toLowerCase().contains("user already exist")) {
          _usernameBackendError = "User already exists with this username";
        } 
        else if (errorMsg.toLowerCase().contains("email")) {
          _emailBackendError = "Account already exists with this email";
        } 
        else {
          _globalError = errorMsg;
        }
      });

      // Bottom sheet band karke main screen pe aa jao errors dikhane ke liye
      Navigator.pop(context);
      _formKey.currentState!.validate();
    }
  }

  // --- OTP Verification Pop-up Interface ---
  void _showOtpBottomSheet() {
    _otpController.clear();
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: backgroundColor,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (context) {
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
                      color: textColor.withOpacity(0.2),
                      borderRadius: BorderRadius.circular(10),
                    ),
                  ),
                  const SizedBox(height: 20),
                  const Text(
                    "Verify Email Address",
                    style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: textColor),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    "We have sent a verification code to \n${_emailController.text.trim()}",
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 14, color: textColor.withOpacity(0.6)),
                  ),
                  const SizedBox(height: 24),
                  
                  // OTP Entry Box
                  TextField(
                    controller: _otpController,
                    keyboardType: TextInputType.number,
                    maxLength: 6,
                    textAlign: TextAlign.center,
                    style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold, letterSpacing: 8),
                    decoration: InputDecoration(
                      hintText: "000000",
                      hintStyle: TextStyle(color: textColor.withOpacity(0.3), letterSpacing: 8),
                      counterText: "",
                      prefixIcon: const Icon(Icons.lock_clock_outlined, color: brandColor),
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide: const BorderSide(color: brandColor, width: 2),
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
                        backgroundColor: brandColor,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                        elevation: 0,
                      ),
                      onPressed: _isLoading 
                          ? null 
                          : () async {
                              final otp = _otpController.text.trim();
                              if (otp.isEmpty || otp.length < 4) {
                                ScaffoldMessenger.of(context).showSnackBar(
                                  const SnackBar(content: Text("Please enter a valid OTP")),
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
                          ? const CircularProgressIndicator(color: Colors.white)
                          : const Text(
                              "Verify & Create Account",
                              style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white),
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
    return Scaffold(
      backgroundColor: backgroundColor,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        leading: const BackButton(color: textColor),
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
                  const Text(
                    "Create Account",
                    style: TextStyle(
                      fontSize: 32,
                      fontWeight: FontWeight.w800,
                      color: textColor,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    "Sign up to get started with LearnScroll",
                    style: TextStyle(
                      fontSize: 15,
                      color: textColor.withOpacity(0.5),
                    ),
                  ),
                  const SizedBox(height: 35),

                  // Username Field
                  _buildInputField(
                    controller: _usernameController,
                    label: "Username",
                    icon: Icons.person_outline_rounded,
                    backendError: _usernameBackendError,
                    onChanged: (_) {
                      if (_usernameBackendError != null) {
                        setState(() => _usernameBackendError = null);
                      }
                    },
                    validator: (val) => val == null || val.trim().isEmpty ? "Username is required" : null,
                  ),
                  const SizedBox(height: 18),
                  
                  // Email Field
                  _buildInputField(
                    controller: _emailController,
                    label: "Email Address",
                    icon: Icons.mail_outline_rounded,
                    type: TextInputType.emailAddress,
                    backendError: _emailBackendError,
                    onChanged: (_) {
                      if (_emailBackendError != null) {
                        setState(() => _emailBackendError = null);
                      }
                    },
                    validator: (val) {
                      if (val == null || val.trim().isEmpty) return "Email is required";
                      final emailRegex = RegExp(r'^[\w\-.]+@([\w-]+\.)+[\w-]{2,4}$');
                      if (!emailRegex.hasMatch(val.trim())) return "Enter a valid email address";
                      return null;
                    },
                  ),
                  const SizedBox(height: 18),

                  // Contact Number Input Field
                  _buildInputField(
                    controller: _phoneController,
                    label: "Contact Number",
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
                          style: const TextStyle(fontSize: 15, color: textColor, fontWeight: FontWeight.w600),
                          icon: const Icon(Icons.arrow_drop_down, color: brandColor, size: 20),
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
                              child: Text(country["label"]!),
                            );
                          }).toList(),
                        ),
                      ),
                    ),
                    validator: (val) {
                      if (val == null || val.trim().isEmpty) return "Contact number is required";
                      if (val.trim().length < 10) return "Enter a valid mobile number";
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
                          label: "First Name",
                          icon: Icons.badge_outlined,
                          validator: (val) => val == null || val.trim().isEmpty ? "Required" : null,
                        ),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: _buildInputField(
                          controller: _lastNameController,
                          label: "Last Name",
                          icon: Icons.badge_outlined,
                          validator: (val) => val == null || val.trim().isEmpty ? "Required" : null,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 18),

                  // Password Field
                  _buildInputField(
                    controller: _passwordController,
                    label: "Password",
                    icon: Icons.lock_outline_rounded,
                    isPassword: true,
                    hideText: _hidePassword,
                    onToggleVisibility: () => setState(() => _hidePassword = !_hidePassword),
                    validator: (val) {
                      if (val == null || val.isEmpty) return "Password is required";
                      if (val.length < 8) return "Password must be at least 8 characters";
                      return null;
                    },
                  ),
                  const SizedBox(height: 18),

                  // Confirm Password Field
                  _buildInputField(
                    controller: _confirmPasswordController,
                    label: "Confirm Password",
                    icon: Icons.lock_outline_rounded,
                    isPassword: true,
                    hideText: _hideConfirmPassword,
                    onToggleVisibility: () => setState(() => _hideConfirmPassword = !_hideConfirmPassword),
                    validator: (val) {
                      if (val == null || val.isEmpty) return "Confirm password is required";
                      if (val != _passwordController.text) return "Passwords do not match";
                      return null;
                    },
                  ),
                  
                  if (_globalError != null) ...[
                    const SizedBox(height: 14),
                    Text(
                      _globalError!,
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: Colors.redAccent, fontSize: 14, fontWeight: FontWeight.w500),
                    ),
                  ],

                  const SizedBox(height: 30),

                  // main submit button
                  SizedBox(
                    width: double.infinity,
                    height: 54,
                    child: ElevatedButton(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: brandColor,
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                        elevation: 0,
                      ),
                      onPressed: _isLoading ? null : _initiateSignupFlow,
                      child: _isLoading
                          ? const CircularProgressIndicator(color: Colors.white)
                          : const Text(
                              "Sign Up",
                              style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: Colors.white),
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
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: textColor),
        ),
        const SizedBox(height: 8),
        TextFormField(
          controller: controller,
          keyboardType: type,
          obscureText: isPassword && hideText,
          onChanged: onChanged,
          style: const TextStyle(fontSize: 15, color: textColor),
          decoration: InputDecoration(
            isDense: true,
            contentPadding: const EdgeInsets.symmetric(vertical: 16, horizontal: 16),
            errorText: backendError,
            prefixIcon: prefixWidget ?? Icon(icon, color: brandColor.withOpacity(0.7), size: 22),
            suffixIcon: isPassword
                ? IconButton(
                    icon: Icon(hideText ? Icons.visibility_off_outlined : Icons.visibility_outlined, color: textColor.withOpacity(0.4), size: 20),
                    onPressed: onToggleVisibility,
                  )
                : null,
            filled: true,
            fillColor: Colors.grey[50],
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: BorderSide(color: Colors.grey[200]!, width: 1),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: brandColor, width: 2),
            ),
            errorBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: Colors.redAccent, width: 1),
            ),
            focusedErrorBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: Colors.redAccent, width: 2),
            ),
          ),
          validator: validator,
        ),
      ],
    );
  }
}