// Path: lib/.../chat_screen.dart
// REPLACE the existing `_loadMoreMessages()` method's catch block with this.
// (poora method neeche hai for context — sirf `catch` part badla hai,
// baaki sab as-is rakha hai)

Future<void> _loadMoreMessages() async {
  if (_isLoadingMore || !_hasMoreMessages) return;
  setState(() => _isLoadingMore = true);

  final nextPage = _currentPage + 1;
  try {
    final older = await MessageApiService.getMessages(
      widget.conversation.id,
      page: nextPage,
      pageSize: _kPageSize,
    );
    if (!mounted) return;

    if (older.isEmpty) {
      setState(() {
        _hasMoreMessages = false;
        _isLoadingMore = false;
      });
      return;
    }

    final prevMaxExtent =
        _scrollController.hasClients ? _scrollController.position.maxScrollExtent : 0.0;
    final prevOffset = _scrollController.hasClients ? _scrollController.offset : 0.0;

    setState(() {
      _messages.insertAll(0, older.reversed.toList());
      _currentPage = nextPage;
      _isLoadingMore = false;
      if (older.length < _kPageSize) _hasMoreMessages = false;
    });
    _scanAlreadyDownloaded();

    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scrollController.hasClients) return;
      final newMaxExtent = _scrollController.position.maxScrollExtent;
      final diff = newMaxExtent - prevMaxExtent;
      if (diff > 0) {
        _scrollController.jumpTo(prevOffset + diff);
      }
    });
  } on MessageApiException catch (e) {
    // 🔥 FIX — DRF `PageNumberPagination` out-of-range page pe hamesha 404
    // deta hai. Ye asal error nahi hai, matlab bas "aur purane messages
    // hain hi nahi" (isse pehle exactly `_kPageSize` messages waale page
    // ke baad `_hasMoreMessages` galat `true` reh jaata tha aur wahi
    // failing request baar-baar retry hoti thi — yahi tumhare "purani
    // stickers load nahi hote" wale case ka asli bug tha).
    if (mounted) {
      setState(() {
        _isLoadingMore = false;
        if (e.statusCode == 404) {
          _hasMoreMessages = false;
        }
      });
      if (e.statusCode != 404) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Failed to load older messages: $e")),
        );
      }
    }
  } catch (e) {
    if (mounted) {
      setState(() => _isLoadingMore = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Failed to load older messages: $e")),
      );
    }
  }
}