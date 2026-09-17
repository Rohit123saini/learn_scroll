import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../home.dart';
// 🔥 NAYA — dark mode + i18n (home.dart jaisa hi pattern).
import '../l10n/app_localizations.dart';
import 'auth_widgets.dart';

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

  // 🔥 REMOVED — hardcoded brandColor/backgroundColor/textColor. Ab
  // `Theme.of(context).colorScheme` se aate hain (login_screen.dart jaisa
  // hi fix).

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
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      // 🔥 backgroundColor hata diya — theme se aata hai ab.
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
                      Icon(Icons.phone_iphone_rounded, size: 60, color: cs.primary),
                      const SizedBox(height: 20),
                      Text(
                        l10n.completeProfileTitle,
                        style: TextStyle(
                          fontSize: 28,
                          fontWeight: FontWeight.w800,
                          color: cs.onSurface,
                          letterSpacing: -0.5,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        l10n.completeProfileSubtitle,
                        textAlign: TextAlign.center,
                        style: TextStyle(fontSize: 15, color: cs.onSurfaceVariant),
                      ),
                      const SizedBox(height: 35),

                      TextFormField(
                        controller: _phoneController,
                        keyboardType: TextInputType.phone,
                        style: TextStyle(fontSize: 15, color: cs.onSurface, fontWeight: FontWeight.w500),
                        autovalidateMode: AutovalidateMode.onUserInteraction,
                        decoration: InputDecoration(
                          labelText: l10n.completeProfilePhone,
                          labelStyle: TextStyle(color: cs.onSurfaceVariant, fontSize: 14),
                          errorText: _phoneError,
                          filled: true,
                          fillColor: cs.surfaceVariant,
                          contentPadding: const EdgeInsets.symmetric(vertical: 18, horizontal: 16),
                          prefixIcon: Padding(
                            padding: const EdgeInsets.only(left: 12.0, right: 4.0),
                            child: DropdownButtonHideUnderline(
                              child: DropdownButton<String>(
                                value: _selectedCountryCode,
                                style: TextStyle(fontSize: 15, color: cs.onSurface, fontWeight: FontWeight.w600),
                                dropdownColor: cs.surface,
                                icon: Icon(Icons.arrow_drop_down, color: cs.primary, size: 20),
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
                                    child: Text(country["label"]!, style: TextStyle(color: cs.onSurface)),
                                  );
                                }).toList(),
                              ),
                            ),
                          ),
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
                        ),
                        validator: (val) {
                          if (val == null || val.trim().isEmpty) return l10n.completeProfilePhoneRequired;
                          if (val.trim().length < 10) return l10n.completeProfilePhoneInvalid;
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
                            backgroundColor: cs.primary,
                            foregroundColor: cs.onPrimary,
                            elevation: 1.5,
                            shadowColor: cs.primary.withOpacity(0.4),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                          ),
                          child: _isLoading
                              ? SizedBox(
                                  height: 24,
                                  width: 24,
                                  child: CircularProgressIndicator(strokeWidth: 2.5, color: cs.onPrimary),
                                )
                              : Text(
                                  l10n.completeProfileContinue,
                                  style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                                ),
                        ),
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
}