// message/screens/parent_code_entry_screen.dart
//
// Feature 8 — entry point for "Parent Mode". No student login needed:
// a parent types in the code their child generated and shared with them.
// Add a text button to this from LoginScreen, e.g.:
//
//   TextButton(
//     onPressed: () => Navigator.push(context,
//       MaterialPageRoute(builder: (_) => const ParentCodeEntryScreen())),
//     child: const Text("Parent/Guardian? View your child's progress"),
//   )

import 'package:flutter/material.dart';
import '../services/parent_service.dart';
import 'parent_dashboard_screen.dart';

class ParentCodeEntryScreen extends StatefulWidget {
  const ParentCodeEntryScreen({super.key});

  @override
  State<ParentCodeEntryScreen> createState() => _ParentCodeEntryScreenState();
}

class _ParentCodeEntryScreenState extends State<ParentCodeEntryScreen> {
  final _codeController = TextEditingController();
  bool _loading = false;
  String? _error;

  @override
  void dispose() {
    _codeController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final code = _codeController.text.trim();
    if (code.isEmpty) {
      setState(() => _error = 'Code daalo');
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      await ParentService.instance.verifyCode(code);
      if (!mounted) return;
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (_) => const ParentDashboardScreen()),
      );
    } on ParentModeException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Kuch galat ho gaya. Dobara try karo.');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F0F11),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0F0F11),
        title: const Text('Parent Mode'),
      ),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              "Apne bachche ka progress dekhein",
              style: TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            const Text(
              "Sirf attendance aur assignment status dikhega — chat message nahi. "
              "Aapke bachche ne jo code diya hai wo neeche daalein.",
              style: TextStyle(color: Colors.white70, fontSize: 14),
            ),
            const SizedBox(height: 32),
            TextField(
              controller: _codeController,
              textCapitalization: TextCapitalization.characters,
              style: const TextStyle(color: Colors.white, fontSize: 20, letterSpacing: 4),
              decoration: InputDecoration(
                hintText: 'e.g. 7F3K9QRT',
                hintStyle: const TextStyle(color: Colors.white38),
                filled: true,
                fillColor: Colors.white.withOpacity(0.06),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                errorText: _error,
              ),
            ),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: _loading ? null : _submit,
                child: _loading
                    ? const SizedBox(
                        height: 20, width: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('View Progress'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}