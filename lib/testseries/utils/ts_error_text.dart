import '../../l10n/app_localizations.dart';
import '../services/testseries_service.dart';

// ============================================================
// Exception → user ko dikhne wala localized message.
//
// Rule: UI kabhi `e.toString()` ya "NOT_AUTHENTICATED" jaisa raw string
// nahi dikhata. validation / conflict me backend ka message dikhta hai
// (e.g. "You have already reviewed this series") — wo user-facing hi hota hai.
// ============================================================

String tsErrorMessage(AppLocalizations l10n, Object error) {
  if (error is TestSeriesApiException) {
    switch (error.kind) {
      case TsErrorKind.network:
        return l10n.tsErrOffline;
      case TsErrorKind.timeout:
        return l10n.tsErrTimeout;
      case TsErrorKind.unauthorized:
        return l10n.tsErrUnauthorized;
      case TsErrorKind.forbidden:
        return l10n.tsErrForbidden;
      case TsErrorKind.notFound:
        return l10n.tsErrNotFound;
      case TsErrorKind.insufficientCoins:
        return l10n.testSeriesNotEnoughCoins;
      case TsErrorKind.rateLimited:
        return l10n.tsErrRateLimited;
      case TsErrorKind.server:
        return l10n.tsErrServer;
      case TsErrorKind.validation:
      case TsErrorKind.conflict:
        final m = error.message.trim();
        return m.isEmpty || m.startsWith('Request failed') ? l10n.somethingWentWrong : m;
      case TsErrorKind.unknown:
        return l10n.somethingWentWrong;
    }
  }
  return l10n.somethingWentWrong;
}
