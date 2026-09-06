// message/screens/focus_mode_screen.dart
//
// 🔥 NAYA (Feature 12) — "Smart do-not-disturb during focus/exam windows"
// Student yahan duration + exception rule set karta hai. Screen khud
// start/stop dono API calls handle karti hai aur Navigator.pop se
// updated `FocusSessionStatus?` wapas bhejti hai (caller — abhi
// `conversations_screen.dart` — sirf local state refresh karta hai).

import 'dart:async';
import 'package:flutter/material.dart';
import '../services/message_api_service.dart';
// 🔧 GAP FIX — FocusSessionStatus ab yahan se nahi, `message_models.dart`
// se aata hai. Pehle ye class isi file me define thi, lekin
// message_api_service.dart ke naye getFocusStatus()/startFocusSession()
// methods ko bhi FocusSessionStatus return type chahiye tha, aur
// message_api_service.dart <-> focus_mode_screen.dart ek dusre ko import
// nahi kar sakte (circular import). Isliye DTO ko shared
// `message_models.dart` me move kiya — baaki saare DTOs (ConversationModel
// etc.) bhi wahin hain, so ye pattern-consistent bhi hai.
import '../models/message_models.dart';

const _kNavy = Color(0xFF030F27);
const _kAnnouncement = Color(0xFFFF8F00);

class FocusModeScreen extends StatefulWidget {
  final FocusSessionStatus? current;
  const FocusModeScreen({super.key, this.current});

  @override
  State<FocusModeScreen> createState() => _FocusModeScreenState();
}

class _FocusModeScreenState extends State<FocusModeScreen> {
  // Preset durations — coaching context me ye sabse common windows hain
  // (ek period, do period, poora exam-block).
  static const _presets = [30, 60, 120, 180];
  int _selectedMinutes = 60;
  String _exceptionRule = 'teachers_only';
  bool _isSaving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    if (widget.current != null) {
      _exceptionRule = widget.current!.exceptionRule;
    }
  }

  Future<void> _start() async {
    setState(() { _isSaving = true; _error = null; });
    try {
      final status = await MessageApiService.startFocusSession(
        durationMinutes: _selectedMinutes,
        exceptionRule: _exceptionRule,
      );
      if (!mounted) return;
      Navigator.pop(context, status);
    } catch (e) {
      if (!mounted) return;
      setState(() { _isSaving = false; _error = e.toString(); });
    }
  }

  Future<void> _stop() async {
    setState(() { _isSaving = true; _error = null; });
    try {
      await MessageApiService.cancelFocusSession();
      if (!mounted) return;
      Navigator.pop(context, null);
    } catch (e) {
      if (!mounted) return;
      setState(() { _isSaving = false; _error = e.toString(); });
    }
  }

  @override
  Widget build(BuildContext context) {
    final isActive = widget.current?.active ?? false;

    return Scaffold(
      backgroundColor: const Color(0xFFF6F7FB),
      appBar: AppBar(
        backgroundColor: _kNavy,
        title: const Text("Focus mode", style: TextStyle(color: Colors.white, fontWeight: FontWeight.w700)),
        iconTheme: const IconThemeData(color: Colors.white),
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          if (isActive) _activeBanner(widget.current!),
          if (isActive) const SizedBox(height: 24),

          Text(
            isActive ? "Change duration" : "How long?",
            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: _kNavy),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: _presets.map((mins) {
              final selected = _selectedMinutes == mins;
              final label = mins < 60 ? '${mins}m' : '${mins ~/ 60}h${mins % 60 == 0 ? '' : ' ${mins % 60}m'}';
              return ChoiceChip(
                label: Text(label),
                selected: selected,
                selectedColor: _kAnnouncement.withOpacity(0.2),
                labelStyle: TextStyle(
                  color: selected ? _kAnnouncement : Colors.black87,
                  fontWeight: selected ? FontWeight.w700 : FontWeight.normal,
                ),
                onSelected: (_) => setState(() => _selectedMinutes = mins),
              );
            }).toList(),
          ),
          const SizedBox(height: 8),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton(
              onPressed: () => _showCustomDurationPicker(context),
              child: const Text("Custom duration"),
            ),
          ),

          const SizedBox(height: 28),
          const Text(
            "Who can still reach you?",
            style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: _kNavy),
          ),
          const SizedBox(height: 8),
          _ruleTile(
            value: 'teachers_only',
            title: 'Only teachers & staff',
            subtitle: 'Group admin/moderator ke messages aur calls aayenge, baaki sab silent',
            icon: Icons.school_rounded,
          ),
          _ruleTile(
            value: 'nobody',
            title: 'Nobody — full silence',
            subtitle: 'Exam ke waqt ke liye — koi bhi push nahi aayega, teacher bhi nahi',
            icon: Icons.do_not_disturb_on_rounded,
          ),

          if (_error != null) ...[
            const SizedBox(height: 16),
            Text(_error!, style: const TextStyle(color: Colors.red, fontSize: 13)),
          ],

          const SizedBox(height: 32),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: _isSaving ? null : _start,
              style: ElevatedButton.styleFrom(
                backgroundColor: _kAnnouncement,
                padding: const EdgeInsets.symmetric(vertical: 14),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
              ),
              child: _isSaving
                  ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : Text(
                      isActive ? "Update focus mode" : "Start focus mode",
                      style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 15),
                    ),
            ),
          ),
          if (isActive) ...[
            const SizedBox(height: 10),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                onPressed: _isSaving ? null : _stop,
                style: OutlinedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  side: const BorderSide(color: Colors.red),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                ),
                child: const Text("End focus mode now", style: TextStyle(color: Colors.red, fontWeight: FontWeight.w700)),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _activeBanner(FocusSessionStatus status) {
    final remaining = status.endsAt.difference(DateTime.now());
    final h = remaining.inHours;
    final m = remaining.inMinutes.remainder(60);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: _kAnnouncement.withOpacity(0.12),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(children: [
        const Icon(Icons.bolt_rounded, color: _kAnnouncement),
        const SizedBox(width: 10),
        Expanded(
          child: Text(
            "Focus mode is active — ${h > 0 ? '${h}h ${m}m' : '${m}m'} left",
            style: const TextStyle(fontWeight: FontWeight.w600, color: Color(0xFF8A5300)),
          ),
        ),
      ]),
    );
  }

  Widget _ruleTile({required String value, required String title, required String subtitle, required IconData icon}) {
    final selected = _exceptionRule == value;
    return InkWell(
      onTap: () => setState(() => _exceptionRule = value),
      borderRadius: BorderRadius.circular(10),
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: selected ? _kAnnouncement : Colors.grey[300]!, width: selected ? 1.6 : 1),
        ),
        child: Row(children: [
          Icon(icon, color: selected ? _kAnnouncement : Colors.grey[500]),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: TextStyle(fontWeight: FontWeight.w700, color: selected ? _kAnnouncement : Colors.black87)),
              const SizedBox(height: 2),
              Text(subtitle, style: TextStyle(fontSize: 12, color: Colors.grey[600])),
            ]),
          ),
          Radio<String>(
            value: value,
            groupValue: _exceptionRule,
            activeColor: _kAnnouncement,
            onChanged: (v) => setState(() => _exceptionRule = v!),
          ),
        ]),
      ),
    );
  }

  Future<void> _showCustomDurationPicker(BuildContext context) async {
    int hours = _selectedMinutes ~/ 60;
    int minutes = _selectedMinutes % 60;
    final picked = await showModalBottomSheet<int>(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          return Padding(
            padding: const EdgeInsets.all(20),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Text("Custom duration", style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
              const SizedBox(height: 16),
              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                _stepper(label: 'Hours', value: hours, min: 0, max: 8, onChanged: (v) => setSheetState(() => hours = v)),
                const SizedBox(width: 24),
                _stepper(label: 'Minutes', value: minutes, min: 0, max: 45, step: 15, onChanged: (v) => setSheetState(() => minutes = v)),
              ]),
              const SizedBox(height: 20),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(backgroundColor: _kAnnouncement),
                  onPressed: (hours * 60 + minutes) < 5
                      ? null
                      : () => Navigator.pop(ctx, hours * 60 + minutes),
                  child: const Text("Set", style: TextStyle(color: Colors.white)),
                ),
              ),
            ]),
          );
        });
      },
    );
    if (picked != null) setState(() => _selectedMinutes = picked.clamp(5, 480));
  }

  Widget _stepper({required String label, required int value, required int min, required int max, int step = 1, required void Function(int) onChanged}) {
    return Column(children: [
      Text(label, style: TextStyle(fontSize: 12, color: Colors.grey[600])),
      const SizedBox(height: 6),
      Row(children: [
        IconButton(icon: const Icon(Icons.remove_circle_outline), onPressed: value > min ? () => onChanged(value - step) : null),
        SizedBox(width: 32, child: Text('$value', textAlign: TextAlign.center, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700))),
        IconButton(icon: const Icon(Icons.add_circle_outline), onPressed: value < max ? () => onChanged(value + step) : null),
      ]),
    ]);
  }
}