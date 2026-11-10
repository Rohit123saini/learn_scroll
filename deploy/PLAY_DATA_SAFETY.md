# Play Console — Data safety & account-deletion answers (DRAFT derived from the code, T0.8)

Verify each line against the final build (SDKs add collection) before submitting.

## Account deletion (Policy → App content → Data deletion)
* In-app path: **Profile → Settings → Delete account**
* Web URL to enter in Play Console: `https://<API_HOST>/legal/delete-account/`
* Privacy policy URL: `https://<API_HOST>/legal/privacy/`  (Terms: `/legal/terms/`)
* Data erased on deletion: posts, stories, comments, reactions, saved items, follows, sessions, device tokens, photo, personal details. Chat messages remain for other participants, attributed to an anonymised deleted account.
* Data retained: coin / payment / withdrawal records, moderation & copyright records (no personal identifiers on the account).

## Data types (collected → purpose; all encrypted in transit; user can request deletion)
| Play category | Data | Shared? | Purpose |
|---|---|---|---|
| Personal info | Name, username, email, phone, date of birth | No (providers only) | Account, app functionality, safety (age) |
| Photos & videos | Profile photo, posts, stories, class media, chat attachments | Public if the user posts publicly | App functionality |
| Audio | Voice / live-class audio (LiveKit) | No | App functionality |
| Messages | In-app chat, comments, doubts | Other participants | App functionality |
| Financial info | Purchase & withdrawal history, payout details | Razorpay / payout provider | Payments, accounting |
| App activity | Interactions, test scores, attendance | No | App functionality, personalisation |
| App info & performance | Crash logs, diagnostics (Sentry) | Sentry (processor) | Analytics / stability |
| App activity (product analytics, X3) | Aggregate counters of screen visits / settings field names changed / notification opens / profile completion - **no user id, no values, no free text**, only for users who switched Analytics ON, never minors | No (own server only) | Analytics |
| Device or other IDs | FCM push token | Google (processor) | Notifications |

Not collected: contacts, health, SMS/call logs (confirm against the manifest permissions). **Precise location IS collected when a user shares their location in chat — see P-G3 below.**
Payment card / UPI credentials are entered in Razorpay Checkout and never reach our servers.

## Ads (H3 audit — NOT yet reflected in the table above)
The feed shows Meta Audience Network native ads (`easy_audience_network_plus`, home.dart / `FeedAdSlot`). The table above lists no ad SDK, so before submitting:
* Play Console → App content → **Ads: "Yes, my app contains ads"**.
* Data safety: add what the Audience Network SDK collects and **shares with Meta** for *Advertising / marketing*: Device or other IDs (Advertising ID), App activity (ad interactions / impressions), App info & performance (diagnostics), approximate location (IP-derived). Copy the exact list from Meta's current SDK data-disclosure page for the version in pubspec.lock — do not rely on this draft.
* Manifest: the Advertising ID permission (`com.google.android.gms.permission.AD_ID`) is needed by the SDK on Android 13+ targets; declare its use in Play Console → Advertising ID.
* Minors: no ads for a known under-18 account (`ads_enabled=false`, backend `core/me_context.py`, kill switch `ADS_OFF_FOR_MINORS`). ⚠️ An account with **no date of birth is not treated as a minor** and does get ads — decide whether that is acceptable for Play's Families / DPDP child rules (children's data: verifiable parental consent, no behavioural advertising).
* DPDP Act 2023 / privacy policy: the policy page (`/legal/privacy/`) must name Meta Audience Network as a third party that receives device/ad identifiers, and the app needs a notice + consent for it. Today the app only has the sign-in line "By continuing you agree to Terms & Privacy" (`LegalConsentText`) and the Settings → Privacy Policy link; there is no ads-specific consent or "ads personalisation" control.


---
## P-G3 — corrections found by reading the code (apply these in Play Console before submitting)

Re-check with `python deploy/tools/data_safety_audit.py --app <flutter project>` against the REAL pubspec.lock + manifest.

| # | Gap in the table above | Evidence in code | Play Console answer |
|---|---|---|---|
| 1 | Precise location is collected | `message/.../chat_state_send_attach.dart` (`Geolocator.getCurrentPosition`) — only when the user taps "share location" | Location → Precise location: Collected, **not** shared with third parties, optional, purpose App functionality; ephemeral = No (stored in the chat message) |
| 2 | Approximate location text | `post/models.py` `location` JSON (city / country typed by the user) | Covered by "Messages / user content"; no extra category |
| 3 | Device or other IDs: only FCM listed | per-install `device_id` (`login/models.py`, `message/models.py DeviceToken`), IP address + user agent in sessions / login audit (`login/models.py`, `post/models.py`) | Device or other IDs: Collected — account security, fraud prevention; IP is "Device or other IDs" |
| 4 | Third-party AI processors missing | `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` (settings.py), `post/ai_assist.py` — doubt / summary / quiz text is sent to the AI provider | Messages / Other user-generated content: **Shared** (service provider, AI feature). Privacy policy must name the AI provider |
| 5 | Google Sign-In | `google_sign_in` (login, connected accounts) | Personal info: email, name, user id: collected via Google Sign-In (optional login method) |
| 6 | Razorpay SDK | `razorpay_flutter` (wallet/razorpay) collects device info + payment data itself | Financial info: **Shared with Razorpay** (payments); Device IDs shared with Razorpay (fraud prevention) |
| 7 | Speech recognition | `speech_to_text` — audio goes to the OS speech service (Google on Android) | Audio: collected; shared = No if on-device, else disclose Google as service provider. Check which mode the app uses |
| 8 | Install referrer | `referrals/deferred_referral.dart` (`android_play_install_referrer`) | Device or other IDs / App activity: referral code only; purpose Analytics + referral rewards |
| 9 | Google Fonts | `google_fonts` downloads fonts from Google (IP to Google). Bundle the fonts as assets to remove this | Optional: bundle fonts (`GoogleFonts.config.allowRuntimeFetching = false` + assets) |
| 10 | Ads | `easy_audience_network_plus` — section "Ads" above is still TODO; **no consent / personalisation control exists** | Declare ads = Yes, Advertising ID, Meta as recipient; add consent before first ad for EEA/UK; decide DOB-less accounts (treated as adults today) |
| 11 | Camera / mic / storage permissions | `Permission.camera`, `Permission.microphone`, `Permission.storage`, `Permission.notification` in lib; `mobile_scanner`, `camera`, `record` | Photos & videos + Audio: collected (already listed); ML Kit face detection / OCR is **on-device**, nothing leaves the phone |

| 12 | Call UI / VoIP token | `flutter_callkit_incoming` (incoming call screen; iOS PushKit token) | Device or other IDs: covered by row 3 (push token); purpose App functionality (calls) |

### In-app links Play and Apple look for (verified in code)
* Privacy Policy: Settings → About → Privacy Policy (`about_privacy_policy`, `/legal/privacy/`)
* Terms: Settings → About → Terms (`/legal/terms/`)
* Refund policy: Settings → About → Refund policy (`/legal/refund-policy/`, P-G2)
* Delete account: Settings → About → Delete account (`DeleteAccountScreen`); web URL `/legal/delete-account/` for Play Console
* Data export: Settings → Data & privacy (`data_rights`) → Download my data
* Sign-in screens show the consent line (`LegalConsentText`)

### Still open (needs a decision, not code)
* Ads consent / "ads personalisation" switch (DPDP + Play Families). Until then declare ads honestly.
* Under-18 accounts: no ads (`ADS_OFF_FOR_MINORS`) — confirm that is enough for Play's Families policy if the app targets children.
* Privacy policy text must name: Razorpay, Firebase/Google, Sentry, LiveKit host, the AI provider(s), Meta Audience Network.
