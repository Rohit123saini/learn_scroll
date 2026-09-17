// lib/wallet/models/wallet_models.dart
//
// ============================================================
// WALLET — MODELS
//
// Endpoints ab CONFIRMED hain — `user_profile_app_reference.md` (v6) aur
// `user_profile/views.py` se. Sab field names seedha in do jagah se aaye
// hain, guess nahi kiye:
//   - `UserProfileSerializer` (coin balance — `ProfileView`, GET /profile/)
//   - `CoinLedgerSerializer` (transaction history)
//   - `CoinPurchaseRequestSerializer` (buy-coin flow)
//   - `CoinWithdrawalRequestSerializer` (withdraw flow)
//
// ⚠️ Do cheezein jo pehle wallet_models.dart me galat maani gayi thi (koi
// file thi hi nahi, par wallet_screen.dart/wallet_service.dart usage se
// pata chalta tha kya assume kiya gaya tha) — dono yahan fix hain:
//
//   1. Balance ka apna koi model/endpoint NAHI hai. `User.coin` bas
//      `ProfileView` (GET /profile/) ke response ke `coin` field me aata
//      hai — isliye `WalletBalance.fromProfileJson` poore profile payload
//      se sirf wahi field nikalta hai.
//   2. `CoinLedger.amount` SIGNED hai (+credit / -debit) — koi alag
//      `type: "credit"/"debit"` field backend nahi bhejta. `LedgerEntry.type`
//      isliye amount ke sign se derive hota hai, aur UI ko unsigned
//      magnitude ke liye `.amount.abs()` (ya `.magnitude`) use karna hai,
//      warna debit rows pe "--50" jaisा double-negative dikhega.
//      `reason` field bhi backend nahi bhejta — `description` bhejta hai,
//      jo khaali ho sakta hai; is case me `transactionType` ka human label
//      fallback hai.
// ============================================================

DateTime? _parseDate(dynamic v) {
  if (v == null) return null;
  return DateTime.tryParse(v.toString());
}

/// `MIN_WITHDRAWAL_COINS` / `COIN_TO_INR_RATE` — `CoinWithdrawalRequest`
/// (user_profile/models.py) ke class-level constants ka client-side mirror,
/// sirf UI pre-check/estimate ke liye ("kam se kam N coins chahiye" jaisa
/// button-disable logic, aur "≈ ₹" estimate). Asli enforcement hamesha
/// server pe hai — 402 (insufficient balance) / 403 (not withdrawal-
/// eligible) dono server hi decide karta hai, ye sirf UX hai.
/// 🔧 Agar backend inhe badle to yahan bhi badalna — koi API in values ko
/// expose nahi karti abhi.
const int kMinWithdrawalCoins = 100;
const double kCoinToInrRate = 1;

// ------------------------------------------------------------
// Balance — `ProfileView` (GET /profile/) ke response se
// ------------------------------------------------------------

class WalletBalance {
  final int coinBalance;

  const WalletBalance({required this.coinBalance});

  /// Poore `/profile/` response se banaya jaata hai — us payload me profile
  /// ki baaki details bhi hoti hain (username, bio, ...), wallet ko sirf
  /// `coin` chahiye.
  factory WalletBalance.fromProfileJson(Map<String, dynamic> j) =>
      WalletBalance(coinBalance: (j['coin'] as num?)?.toInt() ?? 0);
}

// ------------------------------------------------------------
// Ledger — `CoinLedgerSerializer` (GET /profile/coin-ledger/)
// ------------------------------------------------------------

enum LedgerEntryType { credit, debit }

/// `CoinLedger.TransactionType` (user_profile/models.py) ke exact string
/// values — backend jo bhejta hai wahi yahan hai, koi extra invent nahi.
class LedgerTransactionType {
  static const earn = 'earn';
  static const purchase = 'purchase';
  static const spend = 'spend';
  static const refund = 'refund';
  static const giftSent = 'gift_sent';
  static const giftReceived = 'gift_received';
  static const adminAdjustment = 'admin_adjustment';
  static const campusReward = 'campus_reward';
  static const testseriesPurchase = 'testseries_purchase';
  static const testseriesPayout = 'testseries_payout';
  static const withdrawalRequested = 'withdrawal_requested';
  static const withdrawalCompleted = 'withdrawal_completed';
  static const withdrawalRejected = 'withdrawal_rejected';

  /// Description khaali ho to yahi label dikhta hai — koi naya
  /// transaction_type backend me add ho aur yahan na ho to bhi crash nahi
  /// hoga, raw value hi dikh jaayegi.
  static String label(String raw) => switch (raw) {
        earn => 'Earned',
        purchase => 'Purchased',
        spend => 'Spent',
        refund => 'Refunded',
        giftSent => 'Gift Sent',
        giftReceived => 'Gift Received',
        adminAdjustment => 'Admin Adjustment',
        campusReward => 'Campus Reward',
        testseriesPurchase => 'Test Series Purchase',
        testseriesPayout => 'Test Series Payout',
        withdrawalRequested => 'Withdrawal Requested',
        withdrawalCompleted => 'Withdrawal Completed',
        withdrawalRejected => 'Withdrawal Rejected',
        _ => raw,
      };
}

class LedgerEntry {
  final int id;
  final String transactionType;

  /// ⚠️ SIGNED — backend jaisa bhejta hai waisa hi (+credit / -debit).
  /// UI me magnitude dikhane ke liye `.magnitude` use karo, isko seedha
  /// nahi.
  final int amount;
  final int balanceAfter;
  final String reference;
  final String description;
  final Map<String, dynamic> metadata;
  final DateTime? createdAt;

  const LedgerEntry({
    required this.id,
    required this.transactionType,
    required this.amount,
    required this.balanceAfter,
    this.reference = '',
    this.description = '',
    this.metadata = const {},
    this.createdAt,
  });

  LedgerEntryType get type =>
      amount >= 0 ? LedgerEntryType.credit : LedgerEntryType.debit;

  /// Unsigned display value — row me `+magnitude` / `-magnitude` dikhao,
  /// kabhi `.amount` seedha nahi (wo already signed hai).
  int get magnitude => amount.abs();

  /// Backend `reason` naam ka koi field nahi bhejta — `description` bhejta
  /// hai, jo blank ho sakta hai. Blank ho to `transaction_type` ka human
  /// label fallback hai, taaki row kabhi khaali na dikhe.
  String get reason =>
      description.isNotEmpty ? description : LedgerTransactionType.label(transactionType);

  factory LedgerEntry.fromJson(Map<String, dynamic> j) => LedgerEntry(
        id: (j['id'] as num).toInt(),
        transactionType: (j['transaction_type'] ?? '').toString(),
        amount: (j['amount'] as num?)?.toInt() ?? 0,
        balanceAfter: (j['balance_after'] as num?)?.toInt() ?? 0,
        reference: (j['reference'] ?? '').toString(),
        description: (j['description'] ?? '').toString(),
        metadata: j['metadata'] is Map
            ? Map<String, dynamic>.from(j['metadata'] as Map)
            : const {},
        createdAt: _parseDate(j['created_at']),
      );
}

// ------------------------------------------------------------
// Purchase (top-up) — `CoinPurchaseRequestSerializer`
// ------------------------------------------------------------

class CoinPurchaseStatus {
  static const pending = 'pending';
  static const success = 'success';
  static const failed = 'failed';
}

class CoinPurchaseRequest {
  final int id;
  final String gateway;
  final String gatewayReference;
  final double amount; // rupees — DRF DecimalField, JSON me string aati hai
  final int coins;
  final String status; // pending | success | failed
  final String failureReason;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  const CoinPurchaseRequest({
    required this.id,
    required this.gatewayReference,
    required this.amount,
    required this.coins,
    this.gateway = '',
    this.status = CoinPurchaseStatus.pending,
    this.failureReason = '',
    this.createdAt,
    this.updatedAt,
  });

  bool get isPending => status == CoinPurchaseStatus.pending;
  bool get isSuccess => status == CoinPurchaseStatus.success;
  bool get isFailed => status == CoinPurchaseStatus.failed;

  factory CoinPurchaseRequest.fromJson(Map<String, dynamic> j) => CoinPurchaseRequest(
        id: (j['id'] as num).toInt(),
        gateway: (j['gateway'] ?? '').toString(),
        gatewayReference: (j['gateway_reference'] ?? '').toString(),
        // DRF DecimalField JSON me string return karta hai by default
        // ("99.00") — num aur string dono se safe parse.
        amount: double.tryParse(j['amount'].toString()) ?? 0,
        coins: (j['coins'] as num?)?.toInt() ?? 0,
        status: (j['status'] ?? CoinPurchaseStatus.pending).toString(),
        failureReason: (j['failure_reason'] ?? '').toString(),
        createdAt: _parseDate(j['created_at']),
        updatedAt: _parseDate(j['updated_at']),
      );
}

// ------------------------------------------------------------
// Withdrawal — `CoinWithdrawalRequestSerializer`
// ------------------------------------------------------------

class CoinWithdrawalStatus {
  static const pending = 'pending';
  static const processing = 'processing';
  static const success = 'success';
  static const rejected = 'rejected';
}

/// `CoinWithdrawalRequest.PayoutMethod` — exact backend values.
class PayoutMethod {
  static const bankTransfer = 'bank_transfer';
  static const upi = 'upi';
}

/// `payout_details` ki shape `payout_method` pe depend karti hai
/// (`CoinWithdrawalRequestSerializer.validate()` — backend/serializers.py):
///   bank_transfer -> {account_holder, account_number, ifsc}
///   upi           -> {upi_id}
/// Request body me DONO alag jaate hain: `payout_method` (outer field) aur
/// `payout_details` (isi class ka `.toJson()`).
class PayoutDetails {
  final String method; // PayoutMethod.bankTransfer | PayoutMethod.upi
  final String? accountHolder;
  final String? accountNumber;
  final String? ifsc;
  final String? upiId;

  const PayoutDetails.bankTransfer({
    required String accountHolder,
    required String accountNumber,
    required String ifsc,
  })  : method = PayoutMethod.bankTransfer,
        accountHolder = accountHolder,
        accountNumber = accountNumber,
        ifsc = ifsc,
        upiId = null;

  const PayoutDetails.upi({required String upiId})
      : method = PayoutMethod.upi,
        upiId = upiId,
        accountHolder = null,
        accountNumber = null,
        ifsc = null;

  Map<String, dynamic> toJson() {
    switch (method) {
      case PayoutMethod.bankTransfer:
        return {
          'account_holder': accountHolder,
          'account_number': accountNumber,
          'ifsc': ifsc,
        };
      case PayoutMethod.upi:
        return {'upi_id': upiId};
      default:
        return const {};
    }
  }

  /// GET response me `payout_details` raw JSON hoti hai (server-stored,
  /// method ke hisaab se shape badalti hai) — display ke liye seedha
  /// Map<String,dynamic> hi kaafi hai, isko wapas is class me parse karne
  /// ki zaroorat nahi padti.
}

class CoinWithdrawalRequest {
  final int id;
  final int coins;
  final String payoutMethod;

  /// Raw JSON jaisa server ne store kiya — `PayoutDetails.toJson()` ka
  /// output nahi hai zaroori, kyunki server response me wapas parse karne
  /// ki koi zaroorat nahi (sirf display).
  final Map<String, dynamic> payoutDetails;
  final String status; // pending | processing | success | rejected
  final String failureReason;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  const CoinWithdrawalRequest({
    required this.id,
    required this.coins,
    this.payoutMethod = '',
    this.payoutDetails = const {},
    this.status = CoinWithdrawalStatus.pending,
    this.failureReason = '',
    this.createdAt,
    this.updatedAt,
  });

  bool get isPending => status == CoinWithdrawalStatus.pending;
  bool get isProcessing => status == CoinWithdrawalStatus.processing;
  bool get isSuccess => status == CoinWithdrawalStatus.success;
  bool get isRejected => status == CoinWithdrawalStatus.rejected;

  /// `amount_inr` field backend model me hai, par `CoinWithdrawalRequestSerializer`
  /// isko serialize NAHI karta (fields list check karo — nahi hai). Isliye
  /// yahan client-side estimate hi hai, server ka snapshot nahi.
  double get estimatedInr => coins * kCoinToInrRate;

  factory CoinWithdrawalRequest.fromJson(Map<String, dynamic> j) => CoinWithdrawalRequest(
        id: (j['id'] as num).toInt(),
        coins: (j['coins'] as num?)?.toInt() ?? 0,
        payoutMethod: (j['payout_method'] ?? '').toString(),
        payoutDetails: j['payout_details'] is Map
            ? Map<String, dynamic>.from(j['payout_details'] as Map)
            : const {},
        status: (j['status'] ?? CoinWithdrawalStatus.pending).toString(),
        failureReason: (j['failure_reason'] ?? '').toString(),
        createdAt: _parseDate(j['created_at']),
        updatedAt: _parseDate(j['updated_at']),
      );
}