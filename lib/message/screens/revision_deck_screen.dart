import 'package:flutter/material.dart';
import '../models/study_room_models.dart';
import '../services/ai_study_service.dart';

/// 🔥 NAYA — Feature 5: Revision Deck screen.
///
/// `boardContentBuilder` — study_room_screen.dart apna `_collectBoardTextContent`
/// method yahan pass karta hai (function reference, string nahi — taaki jab
/// bhi user "Regenerate" tap kare, us waqt ka LATEST board content collect
/// ho, screen open hote waqt ka stale content nahi).
///
/// Flow:
///   1. Open hote hi saved deck load karne ki koshish (`getSavedRevisionDeck`)
///      — agar pehle se bana hua hai to Gemini dobara call kiye bina turant
///      dikh jaata hai.
///   2. Kuch na mile (ya user "Regenerate" tap kare) to `generateRevisionDeck`
///      call hota hai, jo naya deck bana ke server pe save bhi kar deta hai.
class RevisionDeckScreen extends StatefulWidget {
  final String conversationId;
  final String Function() boardContentBuilder;

  const RevisionDeckScreen({
    super.key,
    required this.conversationId,
    required this.boardContentBuilder,
  });

  @override
  State<RevisionDeckScreen> createState() => _RevisionDeckScreenState();
}

class _RevisionDeckScreenState extends State<RevisionDeckScreen> {
  RevisionDeckModel? _deck;
  bool _loading = true;
  bool _generating = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadSavedDeck();
  }

  Future<void> _loadSavedDeck() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final deck = await AiStudyService.getSavedRevisionDeck(widget.conversationId);
      if (!mounted) return;
      setState(() {
        _deck = deck;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = null; // load-failure ke liye error nahi dikhate — sirf empty state se "Generate" flow chalta hai
      });
    }
  }

  Future<void> _generate() async {
    setState(() {
      _generating = true;
      _error = null;
    });
    try {
      final deck = await AiStudyService.generateRevisionDeck(
        conversationId: widget.conversationId,
        boardContent: widget.boardContentBuilder(),
      );
      if (!mounted) return;
      setState(() {
        _deck = deck;
        _generating = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _generating = false;
        _error = e.toString().replaceFirst('Exception: ', '');
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final hasDeck = _deck != null && !_deck!.isEmpty;

    return DefaultTabController(
      length: 2,
      child: Scaffold(
        backgroundColor: const Color(0xFF14141F),
        appBar: AppBar(
          backgroundColor: const Color(0xFF1E1E2C),
          title: const Text('Revision Deck', style: TextStyle(color: Colors.white)),
          iconTheme: const IconThemeData(color: Colors.white),
          bottom: hasDeck
              ? const TabBar(
                  indicatorColor: Colors.tealAccent,
                  labelColor: Colors.tealAccent,
                  unselectedLabelColor: Colors.white54,
                  tabs: [
                    Tab(icon: Icon(Icons.style_outlined), text: 'Flashcards'),
                    Tab(icon: Icon(Icons.quiz_outlined), text: 'Quiz'),
                  ],
                )
              : null,
          actions: [
            if (hasDeck && !_generating)
              IconButton(
                tooltip: 'Regenerate from latest class content',
                icon: const Icon(Icons.refresh),
                onPressed: _generate,
              ),
          ],
        ),
        body: _buildBody(hasDeck),
      ),
    );
  }

  Widget _buildBody(bool hasDeck) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator(color: Colors.tealAccent));
    }

    if (_generating) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(color: Colors.tealAccent),
            SizedBox(height: 16),
            Text(
              'Class ke poore content se revision deck ban raha hai…',
              style: TextStyle(color: Colors.white70),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      );
    }

    if (!hasDeck) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.style_outlined, color: Colors.white24, size: 56),
              const SizedBox(height: 16),
              const Text(
                'Abhi koi revision deck nahi bana.\nClass ke chat, board aur transcript se\nflashcards + quiz banayein?',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.white70, height: 1.4),
              ),
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: Colors.redAccent, fontSize: 12)),
              ],
              const SizedBox(height: 20),
              ElevatedButton.icon(
                style: ElevatedButton.styleFrom(backgroundColor: Colors.tealAccent, foregroundColor: Colors.black),
                icon: const Icon(Icons.auto_awesome),
                label: const Text('Generate Revision Deck'),
                onPressed: _generate,
              ),
            ],
          ),
        ),
      );
    }

    return TabBarView(
      children: [
        _FlashcardsView(flashcards: _deck!.flashcards),
        _QuizView(questions: _deck!.quiz),
      ],
    );
  }
}

// ============================================================
// FLASHCARDS — swipeable, tap-to-flip
// ============================================================
class _FlashcardsView extends StatefulWidget {
  final List<FlashcardModel> flashcards;
  const _FlashcardsView({required this.flashcards});

  @override
  State<_FlashcardsView> createState() => _FlashcardsViewState();
}

class _FlashcardsViewState extends State<_FlashcardsView> {
  final PageController _controller = PageController(viewportFraction: 0.88);
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    if (widget.flashcards.isEmpty) {
      return const Center(child: Text('Koi flashcards nahi mile', style: TextStyle(color: Colors.white54)));
    }
    return Column(
      children: [
        const SizedBox(height: 16),
        Text(
          '${_index + 1} / ${widget.flashcards.length}  •  tap to flip',
          style: const TextStyle(color: Colors.white38, fontSize: 12),
        ),
        Expanded(
          child: PageView.builder(
            controller: _controller,
            itemCount: widget.flashcards.length,
            onPageChanged: (i) => setState(() => _index = i),
            itemBuilder: (_, i) => Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 24),
              child: _FlipCard(card: widget.flashcards[i]),
            ),
          ),
        ),
        const SizedBox(height: 24),
      ],
    );
  }
}

class _FlipCard extends StatefulWidget {
  final FlashcardModel card;
  const _FlipCard({required this.card});

  @override
  State<_FlipCard> createState() => _FlipCardState();
}

class _FlipCardState extends State<_FlipCard> {
  bool _showBack = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => setState(() => _showBack = !_showBack),
      child: AnimatedSwitcher(
        duration: const Duration(milliseconds: 250),
        transitionBuilder: (child, animation) =>
            ScaleTransition(scale: animation, child: child),
        child: Container(
          key: ValueKey(_showBack),
          width: double.infinity,
          padding: const EdgeInsets.all(24),
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: _showBack
                  ? [const Color(0xFF10B981), const Color(0xFF059669)]
                  : [const Color(0xFF6366F1), const Color(0xFF8B5CF6)],
            ),
            borderRadius: BorderRadius.circular(20),
            boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.3), blurRadius: 16, offset: const Offset(0, 8))],
          ),
          alignment: Alignment.center,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  _showBack ? 'ANSWER' : 'QUESTION',
                  style: TextStyle(color: Colors.white.withOpacity(0.7), fontSize: 11, fontWeight: FontWeight.w700, letterSpacing: 1.2),
                ),
                const SizedBox(height: 16),
                Text(
                  _showBack ? widget.card.back : widget.card.front,
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600, height: 1.4),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ============================================================
// QUIZ — same look-and-feel as study_room_screen.dart's quiz card
// (kept as a separate lightweight widget here since the original
// `_QuizQuestionCard` is private to that file).
// ============================================================
class _QuizView extends StatelessWidget {
  final List<Map<String, dynamic>> questions;
  const _QuizView({required this.questions});

  @override
  Widget build(BuildContext context) {
    if (questions.isEmpty) {
      return const Center(child: Text('Koi quiz questions nahi mile', style: TextStyle(color: Colors.white54)));
    }
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: questions.length,
      itemBuilder: (_, i) => _RevisionQuizCard(question: questions[i], index: i),
    );
  }
}

class _RevisionQuizCard extends StatefulWidget {
  final Map<String, dynamic> question;
  final int index;
  const _RevisionQuizCard({required this.question, required this.index});

  @override
  State<_RevisionQuizCard> createState() => _RevisionQuizCardState();
}

class _RevisionQuizCardState extends State<_RevisionQuizCard> {
  bool _revealed = false;
  static const List<String> _labels = ['A', 'B', 'C', 'D', 'E', 'F'];

  @override
  Widget build(BuildContext context) {
    final questionText = (widget.question['question'] ?? '').toString();
    final answerText = (widget.question['answer'] ?? '').toString();
    final options = (widget.question['options'] as List?)?.map((e) => e.toString()).toList() ?? [];

    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.045),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withOpacity(0.06)),
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: EdgeInsets.fromLTRB(14, 14, 14, options.isEmpty ? 4 : 10),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 26,
                  height: 26,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    gradient: const LinearGradient(colors: [Color(0xFF6366F1), Color(0xFF8B5CF6)]),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Text('${widget.index + 1}', style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 13)),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(questionText, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600, fontSize: 14, height: 1.35)),
                ),
              ],
            ),
          ),
          if (options.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
              child: Wrap(
                spacing: 8,
                runSpacing: 8,
                children: List.generate(options.length, (i) {
                  final label = i < _labels.length ? _labels[i] : '${i + 1}';
                  return Container(
                    constraints: BoxConstraints(minWidth: (MediaQuery.of(context).size.width - 60) / 2),
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    decoration: BoxDecoration(
                      color: Colors.white.withOpacity(0.04),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: Colors.white.withOpacity(0.07)),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        CircleAvatar(radius: 9, backgroundColor: Colors.white10, child: Text(label, style: const TextStyle(color: Colors.white70, fontSize: 10, fontWeight: FontWeight.w700))),
                        const SizedBox(width: 8),
                        Flexible(child: Text(options[i], style: const TextStyle(color: Colors.white70, fontSize: 12.5))),
                      ],
                    ),
                  );
                }),
              ),
            ),
          AnimatedSize(
            duration: const Duration(milliseconds: 220),
            curve: Curves.easeOut,
            child: _revealed
                ? Container(
                    width: double.infinity,
                    margin: const EdgeInsets.fromLTRB(14, 0, 14, 14),
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                    decoration: BoxDecoration(
                      color: const Color(0xFF10B981).withOpacity(0.12),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: const Color(0xFF10B981).withOpacity(0.35)),
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Icon(Icons.check_circle_rounded, color: Color(0xFF34D399), size: 18),
                        const SizedBox(width: 8),
                        Expanded(child: Text(answerText, style: const TextStyle(color: Color(0xFFD1FAE5), fontWeight: FontWeight.w600, fontSize: 13))),
                      ],
                    ),
                  )
                : Padding(
                    padding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
                    child: InkWell(
                      borderRadius: BorderRadius.circular(10),
                      onTap: () => setState(() => _revealed = true),
                      child: Container(
                        padding: const EdgeInsets.symmetric(vertical: 10),
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                          color: Colors.white.withOpacity(0.05),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: const Text('Reveal Answer', style: TextStyle(color: Colors.tealAccent, fontWeight: FontWeight.w600, fontSize: 12.5)),
                      ),
                    ),
                  ),
          ),
        ],
      ),
    );
  }
}