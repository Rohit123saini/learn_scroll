// lib/wallet/services/wallet_service.dart
//
// ============================================================
// WALLET — API SERVICE
//
// ✅ Endpoints ab CONFIRMED hain (pehle placeholder the) —
// `user_profile_app_reference.md` (v6, §7 endpoint table) aur
// `user_profile/views.py` se, guess nahi. Mount: `path('profile/',
// include('user_profile.urls'))`, isliye `_base = Api.baseUrl + '/profile'`
// — campus module (`campus_service.dart`) jaisa hi pattern, alag base bas.
//
// Auth bhi ab real hai: `AuthService.getValidToken()`, placeholder token
// function hata diya.
//
// ⚠️ ARCHITECTURE FIX — ye important hai:
// Pehle is file me `verifyPurchase()` tha jo `POST /wallet/purchase/verify/`
// pe order_id/payment_id/signature bhejta tha, jaise client khud purchase
// confirm karta ho. Backend me aisa NAHI hai. `BuyCoinConfirmView`
// (`POST /profile/buy-coin/confirm/`) ek asli GATEWAY WEBHOOK hai —
// `permission_classes = [AllowAny]`, signature Razorpay ke apne secret se
// verify hoti hai (`_verify_gateway_webhook_signature`), koi
// `IsAuthenticated` check nahi hai. Matlab:
//   - Ye endpoint Flutter app se kabhi call NAHI karna — na verify, na
//     confirm, kisi bhi tarah se. Ye sirf Razorpay (ya jo bhi gateway) ka
//     server seedha backend ko hit karta hai.
//   - Client ka kaam sirf itna hai: `initiatePurchase()` se PENDING request
//     banao (Razorpay order/txn id ke saath), Razorpay checkout SDK khud
//     handle karo, aur checkout ke baad bas `refreshAfterPurchase()`
//     (balance + ledger dono re-fetch) call karo — coins already credited
//     honge jab tak webhook process ho chuka hai. Agar turant refresh pe
//     balance nahi badla, thodi der baad ek aur refresh try karo (webhook
//     delivery instant nahi hoti) — koi client-side "confirm" call mat
//     jodna, wo endpoint isi wajah se `AllowAny` hai ki app usko trust na
//     kare.
// 🔧 CONFIRM WITH BACKEND (razorpay order creation ye app expose nahi karta
// is upload me) — Razorpay order kahan/kaise banta hai (client-side keys se
// ya kisi alag backend call se) wallet_topup_screen.dart ka scope hai, is
// file ka nahi.
// ============================================================

import 'dart:convert';
import 'package:http/http.dart' as http;

import '../models/wallet_models.dart';
import '../../services/auth_service.dart';
import '../../utils/api.dart';

const Duration kApiTimeout = Duration(seconds: 15);

class WalletService {
  WalletService._();
  static final WalletService instance = WalletService._();

  static String get _base => '${Api.baseUrl}/profile';

  Future<Map<String, String>> _authHeaders({bool json = true}) async {
    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) {
      throw WalletApiException('NOT_AUTHENTICATED', 401);
    }
    return {
      'Authorization': 'Bearer $token',
      if (json) 'Content-Type': 'application/json',
    };
  }

  /// Har `user_profile` response isi envelope me aata hai:
  /// `{"status": bool, "message": str, "data"/"errors": ...}`. Error pe
  /// `message` (ya, validation errors ke liye, `errors`) nikaal ke
  /// exception me daalte hain — "Request failed (400)" se user ko kuch
  /// samajh nahi aata, backend ka asli message aata hai to samajh aata hai.
  Never _fail(http.Response r) {
    String message = 'Request failed (${r.statusCode})';
    try {
      final decoded = jsonDecode(utf8.decode(r.bodyBytes));
      if (decoded is Map) {
        if (decoded['message'] != null) {
          message = decoded['message'].toString();
        } else if (decoded['errors'] is Map && (decoded['errors'] as Map).isNotEmpty) {
          final first = (decoded['errors'] as Map).entries.first;
          final v = first.value;
          message = '${first.key}: ${v is List ? v.join(', ') : v}';
        }
      }
    } catch (_) {
      // body JSON nahi tha — default message hi theek hai
    }
    throw WalletApiException(message, r.statusCode);
  }

  Map<String, dynamic> _unwrap(http.Response r) {
    final decoded = jsonDecode(utf8.decode(r.bodyBytes));
    if (decoded is! Map) return const {};
    final data = decoded['data'];
    return data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
  }

  /// `coin-ledger/` `paginate_queryset()` use karta hai — agar project-wide
  /// pagination on hai to `data` ek `{"results": [...], ...}` dict hoga,
  /// warna seedha list. `coin-withdrawals/` GET kabhi paginate nahi karta
  /// (view khud confirm karta hai — seedha `serializer.data`), hamesha
  /// plain list. Dono handle karne ke liye same tolerant parse.
  List<dynamic> _asList(dynamic data) {
    if (data is List) return data;
    if (data is Map && data['results'] is List) return data['results'] as List;
    return const [];
  }

  // ---------------- Balance ----------------

  /// `GET /profile/` — `ProfileView`. Poora profile payload aata hai,
  /// yahan sirf `coin` nikala jaata hai. Koi alag "balance" endpoint nahi
  /// hai.
  Future<WalletBalance> getBalance() async {
    final res = await http
        .get(Uri.parse('$_base/'), headers: await _authHeaders())
        .timeout(kApiTimeout);
    if (res.statusCode != 200) _fail(res);
    return WalletBalance.fromProfileJson(_unwrap(res));
  }

  // ---------------- Ledger ----------------

  /// `GET /profile/coin-ledger/` — read-only, newest first
  /// (`CoinLedger.Meta.ordering`). `limit`/`offset` yahan optimistic hain —
  /// is upload me pagination class confirm nahi hui, isliye bhej to rahe
  /// hain (harmless agar backend ignore kare) par response ko List AUR
  /// paginated dict dono shape me parse karte hain.
  Future<List<LedgerEntry>> getLedger({int limit = 30, int offset = 0}) async {
    final uri = Uri.parse('$_base/coin-ledger/').replace(
      queryParameters: {'limit': '$limit', 'offset': '$offset'},
    );
    final res = await http.get(uri, headers: await _authHeaders()).timeout(kApiTimeout);
    if (res.statusCode != 200) _fail(res);
    final decoded = jsonDecode(utf8.decode(res.bodyBytes));
    final data = decoded is Map ? decoded['data'] : null;
    return _asList(data)
        .map((e) => LedgerEntry.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  // ---------------- Top-up (coin purchase) ----------------

  /// `POST /profile/buy-coin/` — `BuyCoinView`. PENDING request banata hai,
  /// coins CREDIT NAHI karta (wo webhook confirm karta hai — upar file
  /// header dekho). Idempotent hai `gatewayReference` pe: same reference
  /// dobara bhejne pe wahi existing request wapas milti hai, duplicate
  /// nahi banta.
  Future<CoinPurchaseRequest> initiatePurchase({
    required String gatewayReference,
    required double amount,
    required int coins,
    String gateway = 'razorpay',
  }) async {
    final res = await http
        .post(
          Uri.parse('$_base/buy-coin/'),
          headers: await _authHeaders(),
          body: jsonEncode({
            'gateway_reference': gatewayReference,
            'amount': amount.toStringAsFixed(2),
            'coins': coins,
            'gateway': gateway,
          }),
        )
        .timeout(kApiTimeout);
    if (res.statusCode != 200 && res.statusCode != 201) _fail(res);
    return CoinPurchaseRequest.fromJson(_unwrap(res));
  }

  /// Razorpay checkout ke baad ye call karo — koi client-side "verify"/
  /// "confirm" endpoint nahi hai (upar file header dekho). Balance +
  /// ledger dono fresh laata hai taaki webhook already process ho chuka ho
  /// to turant dikh jaaye.
  Future<(WalletBalance, List<LedgerEntry>)> refreshAfterPurchase() async {
    final results = await Future.wait([getBalance(), getLedger()]);
    return (results[0] as WalletBalance, results[1] as List<LedgerEntry>);
  }

  // ---------------- Withdrawal ----------------

  /// `POST /profile/coin-withdrawals/` — `CoinWithdrawalRequestView`.
  /// Do alag rejection shapes hain, dono client ko clearly dikhne chahiye
  /// (`WalletApiException.isEligibilityRejected` / `.isInsufficientBalance`
  /// se check karo):
  ///   403 — eligibility fail (fraud.is_withdrawal_eligible) — "tumhare
  ///         paas withdrawal-eligible coins kam hain" (mixed balance me
  ///         sirf purchased/gifted portion eligible hai, saara balance
  ///         nahi). Coins move NAHI hote.
  ///   402 — seedha insufficient balance. Coins move NAHI hote.
  /// Dono me koi partial debit ya orphan request row nahi banti — server
  /// dono ek hi atomic block me karta hai.
  Future<CoinWithdrawalRequest> requestWithdrawal({
    required int coins,
    required PayoutDetails payoutDetails,
  }) async {
    if (coins < kMinWithdrawalCoins) {
      // Client-side pre-check sirf UX ke liye — asli floor server pe hai.
      throw WalletApiException('below_minimum_withdrawal', 400);
    }
    final res = await http
        .post(
          Uri.parse('$_base/coin-withdrawals/'),
          headers: await _authHeaders(),
          body: jsonEncode({
            'coins': coins,
            'payout_method': payoutDetails.method,
            'payout_details': payoutDetails.toJson(),
          }),
        )
        .timeout(kApiTimeout);
    if (res.statusCode != 200 && res.statusCode != 201) _fail(res);
    return CoinWithdrawalRequest.fromJson(_unwrap(res));
  }

  /// `GET /profile/coin-withdrawals/` — apni saari withdrawal requests,
  /// newest first. View koi query param support nahi karta (poora, un-
  /// paginated list return karta hai), isliye yahan `limit`/`offset` nahi
  /// hai — pichle version me the, par backend unko kabhi padhta hi nahi
  /// tha.
  Future<List<CoinWithdrawalRequest>> getWithdrawals() async {
    final res = await http
        .get(Uri.parse('$_base/coin-withdrawals/'), headers: await _authHeaders())
        .timeout(kApiTimeout);
    if (res.statusCode != 200) _fail(res);
    final decoded = jsonDecode(utf8.decode(res.bodyBytes));
    final data = decoded is Map ? decoded['data'] : null;
    return _asList(data)
        .map((e) => CoinWithdrawalRequest.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }
}

class WalletApiException implements Exception {
  final String message;
  final int statusCode;
  WalletApiException(this.message, this.statusCode);

  /// Withdrawal request: eligibility rejection (403) — enough coins, par
  /// enough *withdrawal-eligible* coins nahi (mixed balance case).
  bool get isEligibilityRejected => statusCode == 403;

  /// Withdrawal request: seedha insufficient balance (402).
  bool get isInsufficientBalance => statusCode == 402;

  bool get isNotAuthenticated => statusCode == 401;

  @override
  String toString() => message;
}