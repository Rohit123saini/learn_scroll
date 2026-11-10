"""T3.10 -- GET /profile/wallet/history/  (same envelope + item shape as /profile/coin-ledger/)."""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .wallet_unify import WalletFilters, wallet_history, wallet_totals


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class WalletHistoryView(APIView):
    """The user's complete coin history in ONE list (CoinLedger + the legacy tuitionclass ledger).
    Query: ?limit=30&offset=0.  Item = CoinLedgerSerializer fields + `source` ("ledger" | "legacy");
    legacy items have a NEGATIVE id and `metadata.legacy_reason`."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # S9 filters (all optional): ?direction=in|out &group=topups|spends|earnings|withdrawals|refunds|other
        # &date_from=YYYY-MM-DD &date_to=YYYY-MM-DD &q=text   +  ?totals=1 adds data.totals {in, out, count}.
        from .wallet_settings import user_timezone

        flt = WalletFilters.from_query(request.GET, tz=user_timezone(request.user.pk))
        total, rows = wallet_history(request.user, _int(request.GET.get("limit"), 30), _int(request.GET.get("offset"), 0), flt)
        for r in rows:
            r["created_at"] = r["created_at"].isoformat()
        data = {"count": total, "results": rows}
        if (request.GET.get("totals") or "") in {"1", "true"}:
            data["totals"] = wallet_totals(request.user, flt)
        return Response(
            {"status": True, "message": "Wallet history fetched successfully.", "data": data},
            status=status.HTTP_200_OK,
        )
