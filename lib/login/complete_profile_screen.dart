import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../home.dart';

class CompleteProfileScreen extends StatefulWidget {
  const CompleteProfileScreen({super.key});

  @override
  State<CompleteProfileScreen> createState() => _CompleteProfileScreenState();
}

class _CompleteProfileScreenState extends State<CompleteProfileScreen> {
  final _formKey = GlobalKey<FormState>();
  final _phoneController = TextEditingController();
  final _apiService = ApiService();

  bool _isLoading = false;
  String? _phoneError;

  String _selectedCountryCode = "91";

  static const Color brandColor = Color(0xFF6366F1);
  static const Color backgroundColor = Colors.white;
  static const Color textColor = Color(0xFF0F172A);

  @override
  void dispose() {
    _phoneController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() => _phoneError = null);

    if (!_formKey.currentState!.validate()) return;

    setState(() => _isLoading = true);

    final fullPhone = "$_selectedCountryCode${_phoneController.text.trim()}";

    try {
      await _apiService.completeProfile(fullPhone);

      if (!mounted) return;

      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (context) => const HomeScreen()),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isLoading = false;
        _phoneError = e.toString().replaceAll("Exception:", "").trim();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
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
                  const Icon(Icons.phone_iphone_rounded, size: 60, color: brandColor),
                  const SizedBox(height: 20),
                  const Text(
                    "One Last Step",
                    style: TextStyle(
                      fontSize: 28,
                      fontWeight: FontWeight.w800,
                      color: textColor,
                      letterSpacing: -0.5,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    "Please add your phone number to finish setting up your account",
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 15, color: textColor.withOpacity(0.5)),
                  ),
                  const SizedBox(height: 35),

                  TextFormField(
                    controller: _phoneController,
                    keyboardType: TextInputType.phone,
                    style: const TextStyle(fontSize: 15, color: textColor, fontWeight: FontWeight.w500),
                    autovalidateMode: AutovalidateMode.onUserInteraction,
                    decoration: InputDecoration(
                      labelText: "Phone Number",
                      labelStyle: TextStyle(color: textColor.withOpacity(0.4), fontSize: 14),
                      errorText: _phoneError,
                      filled: true,
                      fillColor: Colors.grey[50],
                      contentPadding: const EdgeInsets.symmetric(vertical: 18, horizontal: 16),
                      prefixIcon: Padding(
                        padding: const EdgeInsets.only(left: 12.0, right: 4.0),
                        child: DropdownButtonHideUnderline(
                          child: DropdownButton<String>(
                            value: _selectedCountryCode,
                            style: const TextStyle(fontSize: 15, color: textColor, fontWeight: FontWeight.w600),
                            icon: const Icon(Icons.arrow_drop_down, color: brandColor, size: 20),
                            onChanged: (String? newValue) {
                              if (newValue != null) {
                                setState(() => _selectedCountryCode = newValue);
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
                    ),
                    validator: (val) {
                      if (val == null || val.trim().isEmpty) return "Phone number is required";
                      if (val.trim().length < 10) return "Enter a valid mobile number";
                      return null;
                    },
                  ),
                  const SizedBox(height: 28),

                  SizedBox(
                    width: double.infinity,
                    height: 56,
                    child: ElevatedButton(
                      onPressed: _isLoading ? null : _submit,
                      style: ElevatedButton.styleFrom(
                        backgroundColor: brandColor,
                        foregroundColor: Colors.white,
                        elevation: 1.5,
                        shadowColor: brandColor.withOpacity(0.4),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                      ),
                      child: _isLoading
                          ? const SizedBox(
                              height: 24,
                              width: 24,
                              child: CircularProgressIndicator(strokeWidth: 2.5, color: Colors.white),
                            )
                          : const Text(
                              "Continue",
                              style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                            ),
                    ),
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
}