"""
T0.8 (R1) — public legal pages (Play Console needs public URLs for Privacy Policy and the
"delete your account" web resource; the app links to the same pages).

    /legal/privacy/          Privacy Policy
    /legal/terms/            Terms of Service
    /legal/refund-policy/    Refund & cancellation policy (P-G2; the app's Settings > Refund policy opens it)
    /legal/delete-account/   How to delete your account + data (in-app steps and an email path for
                             people who can no longer open the app)

!! The policy text below is a STARTER DRAFT written from what the code actually collects. It is NOT
!! legal advice. Have your lawyer review it, fill the entity details, then set
!! LEGAL_PAGES_DRAFT_BANNER=false. Or host your own policy elsewhere and set PRIVACY_POLICY_URL /
!! TERMS_URL / DELETE_ACCOUNT_URL — these views then 302 there (the app links keep working).
"""
from html import escape

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect

UPDATED = "2026-10-13"


def _cfg(name, default=""):
    return getattr(settings, name, default) or default


def _lang(request):
    """S11 - `?lang=hi` -> "hi", anything else -> "en". Only English and Hindi pages exist; the app maps other languages to these."""
    raw = (request.GET.get("lang") or "").strip().lower().replace("_", "-").split("-")[0]
    return "hi" if raw == "hi" else "en"


def _page(title, body_html, lang="en", summary_html=""):
    banner = ""
    if getattr(settings, "LEGAL_PAGES_DRAFT_BANNER", True):
        banner = ('<div style="background:#fff3cd;border:1px solid #ffe69c;padding:10px 14px;border-radius:8px;'
                  'margin-bottom:18px;font-size:14px">DRAFT — pending legal review. Not final.</div>')
    html = f"""<!doctype html><html lang="{escape(lang)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} — {escape(_cfg('LEGAL_ENTITY_NAME','LearnScroll'))}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:760px;margin:0 auto;padding:24px;color:#1b1b1f}}
h1{{font-size:26px}}h2{{font-size:19px;margin-top:28px}}li{{margin:4px 0}}code{{background:#f1f1f4;padding:1px 5px;border-radius:4px}}
@media(prefers-color-scheme:dark){{body{{background:#121214;color:#e6e6ea}}code{{background:#26262b}}}}</style></head>
<body>{banner}<h1>{escape(title)}</h1><p><small>Last updated: {UPDATED}</small></p>{summary_html}{body_html}</body></html>"""
    return HttpResponse(html)


# S11 - Hindi summaries shown above the full English text when `?lang=hi`. The English text is the legally binding one.
# !! Starter wording, like the pages themselves: have it reviewed with the rest of the policy. To use a fully translated page
# !! instead, set PRIVACY_POLICY_URL_HI / TERMS_URL_HI - the Hindi views then 302 there.
_HI_NOTE = ('<div style="border:1px solid #c9c9d1;border-radius:8px;padding:12px 14px;margin:0 0 18px;font-size:15px">'
            '<b>सारांश (हिंदी)</b> — नीचे पूरा पाठ अंग्रेज़ी में है। कानूनी रूप से अंग्रेज़ी पाठ ही मान्य है।</div>')

_HI_PRIVACY = _HI_NOTE + """
<h2>मुख्य बातें</h2><ul>
<li><b>क्या जमा होता है:</b> अकाउंट की जानकारी (यूज़रनेम, ई-मेल/फ़ोन, जन्मतिथि), आपकी पोस्ट-चैट-टेस्ट गतिविधि, पेमेंट और वॉलेट रिकॉर्ड, डिवाइस और क्रैश की तकनीकी जानकारी, और सपोर्ट को भेजी गई चीज़ें।</li>
<li><b>कार्ड/UPI/बैंक की जानकारी</b> हमारा पेमेंट प्रोवाइडर संभालता है; हम उसे न देखते हैं न रखते हैं।</li>
<li><b>हम आपका निजी डेटा बेचते नहीं हैं।</b> सिर्फ़ ज़रूरी सेवा-प्रदाताओं के साथ, कानून माँगे तो, या आपकी सार्वजनिक पोस्ट के रूप में दूसरों के साथ साझा होता है।</li>
<li><b>18 साल से कम उम्र</b> के यूज़र्स को अतिरिक्त सुरक्षा मिलती है।</li>
<li><b>अकाउंट डिलीट:</b> माँगने पर 30 दिन का इंतज़ार, जिसमें आप कभी भी रद्द कर सकते हैं। कानून से रखने ज़रूरी रिकॉर्ड (जैसे पेमेंट) पहचान हटाकर रखे जाते हैं।</li>
<li><b>आपके अधिकार:</b> देखना, सुधारना, डाउनलोड करना, डिलीट करना, सहमति वापस लेना और शिकायत करना — ऐप की Settings में या हमसे संपर्क करके।</li></ul>
"""

_HI_TERMS = _HI_NOTE + """
<h2>मुख्य बातें</h2><ul>
<li><b>अकाउंट:</b> सही जानकारी दें, पासवर्ड सुरक्षित रखें; 18 से कम उम्र में माता-पिता/अभिभावक की अनुमति ज़रूरी है।</li>
<li><b>कंटेंट:</b> जो आप डालते हैं वह आपका रहता है, पर सेवा चलाने के लिए हमें उसे दिखाने की अनुमति देते हैं। गैरकानूनी, नफ़रत फैलाने वाला, दूसरों का अधिकार तोड़ने वाला या परेशान करने वाला कंटेंट मना है।</li>
<li><b>क्लास और टेस्ट:</b> कंटेंट और कीमत की ज़िम्मेदारी टीचर की है। नकल, पेड सामग्री बाँटना और रेफ़रल/रिवॉर्ड का दुरुपयोग मना है।</li>
<li><b>कॉइन और पेमेंट:</b> कॉइन INR में दिखी कीमत पर ख़रीदे जाते हैं; निकासी केवल योग्य कमाए हुए कॉइन पर, समीक्षा के बाद।</li>
<li><b>अकाउंट बंद:</b> आप Settings से कभी भी अकाउंट डिलीट कर सकते हैं।</li>
<li>भारतीय कानून लागू होता है।</li></ul>
"""


def _entity():
    return escape(_cfg("LEGAL_ENTITY_NAME", "LearnScroll"))


def _contact():
    mail = _cfg("SUPPORT_EMAIL") or _cfg("GRIEVANCE_OFFICER_EMAIL")
    return f'<a href="mailto:{escape(mail)}">{escape(mail)}</a>' if mail else "the support option inside the app (Settings → Help)"


def privacy_policy(request):
    lang = _lang(request)
    url = (lang == "hi" and _cfg("PRIVACY_POLICY_URL_HI")) or _cfg("PRIVACY_POLICY_URL")
    if url:
        return HttpResponseRedirect(url)
    g_name, g_mail = _cfg("GRIEVANCE_OFFICER_NAME"), _cfg("GRIEVANCE_OFFICER_EMAIL")
    grievance = (f"<p>Grievance Officer: {escape(g_name)} — <a href=\"mailto:{escape(g_mail)}\">{escape(g_mail)}</a>. "
                 f"We acknowledge within {int(_cfg('GRIEVANCE_ACK_HOURS', 24))} hours and resolve within "
                 f"{int(_cfg('GRIEVANCE_RESOLVE_DAYS', 15))} days.</p>") if g_name and g_mail else \
        f"<p>Questions or complaints: {_contact()}.</p>"
    body = f"""
<p>{_entity()} (“we”, “us”) runs the LearnScroll app: a learning network with posts, stories, chats, live and
recorded classes, assignments, test series and a coin wallet. This policy explains what we collect and why.</p>
<h2>1. What we collect</h2><ul>
<li><b>Account:</b> username, email and/or phone, password (stored hashed), date of birth, profile photo, bio, class / exam, privacy settings.</li>
<li><b>Content you create:</b> posts, stories, comments, reactions, chat messages and attachments, doubts, assignment submissions, test answers.</li>
<li><b>Classes &amp; learning activity:</b> enrolments, attendance, scores, certificates, notes you open.</li>
<li><b>Payments &amp; wallet:</b> coin purchases and withdrawals, order / payment ids and amounts. Card, UPI and bank credentials are handled by our payment provider (Razorpay) — we never see or store them. Withdrawal payout details you submit are stored to pay you.</li>
<li><b>Device &amp; technical:</b> push-notification token, device platform, app version, IP address, crash and performance reports (Sentry), security / abuse logs.</li>
<li><b>Location:</b> only when you choose to share your location in a chat (precise, sent as part of that message), and any city or place you type into a post. We do not track your location in the background.</li>
<li><b>Sign-in &amp; referral:</b> if you use Google Sign-In we receive your name, email and Google account id. If you installed through an invite link we read the Google Play install referrer (the invite / referral code).</li>
<li><b>Microphone, camera &amp; photos:</b> only when you use a feature that needs them (calls, live classes, voice notes, posting, scanning). Face detection and text recognition in the editor run on your phone.</li>
<li><b>Support:</b> tickets, bug reports and screenshots you send us.</li></ul>
<h2>2. How we use it</h2><ul><li>To run the service you asked for (accounts, feeds, chats, classes, payments).</li>
<li>To keep the community safe: moderation, copyright and abuse handling, fraud prevention.</li>
<li>To send notifications you have not turned off, and to fix crashes and improve the app.</li>
<li>To meet legal, tax and accounting obligations.</li></ul>
<p>We do not sell your personal data.</p>
<h2>3. Sharing</h2><p>Only with providers who process data for us (cloud hosting and storage, Firebase push messaging, Razorpay payments,
Sentry crash reporting, live-class video infrastructure, SMS / email delivery, Google for sign-in and speech recognition, AI providers
(Google Gemini / Anthropic) that receive the text you submit to an AI feature such as doubt help or summaries, and Meta Audience Network,
which receives device / advertising identifiers and ad interactions to show ads to adult users), when the law requires, and — for content you
post publicly — with other users. Private accounts and chats are visible only to the people you choose.</p>
<h2>4. Children</h2><p>Users under 18 get additional protections (restricted features and messaging controls). We ask for date of
birth to apply them. If you believe a child has given us data without a parent’s consent, contact us and we will remove it.</p>
<h2>5. Retention &amp; deletion</h2>
<p>You can delete your account in the app (Settings → Delete account) or at <a href="/legal/delete-account/">/legal/delete-account/</a>.
Your account then enters a 30-day waiting period: it keeps working and you can cancel the deletion any time in Settings → Data &amp; privacy. When the
period ends we sign you out everywhere and erase your posts, stories, comments, reactions, follows, study activity (streak, time spent, recaps, badges), settings and profile photo, and remove personal
identifiers from your account. Messages you sent in chats stay visible to the other participants without your name or photo. We keep records we are legally required to keep — coin / payment / withdrawal history and
moderation or copyright records — without personal identifiers on the account, for the period the law requires. Backups roll off within about 30 days.</p>
<h2>6. Your rights</h2><p>You may access, correct, download (Settings → Data &amp; privacy → Download my data) or delete your data, withdraw consent and complain to the Data Protection Board /
the relevant authority. Use the in-app settings or contact us.</p>
<h2>7. Security</h2><p>Encrypted transport (HTTPS), hashed passwords, signed expiring links for private class files, access controls and logging.
No system is perfectly secure; tell us at once if you suspect misuse.</p>
<h2>8. Contact</h2>{grievance}<p>Contact: {_contact()}</p>
<p><small>We may update this policy; material changes will be announced in the app.</small></p>"""
    return _page("Privacy Policy", body, lang=lang, summary_html=_HI_PRIVACY if lang == "hi" else "")


def terms_of_service(request):
    lang = _lang(request)
    url = (lang == "hi" and _cfg("TERMS_URL_HI")) or _cfg("TERMS_URL")
    if url:
        return HttpResponseRedirect(url)
    body = f"""
<p>By creating an account or using LearnScroll you agree to these terms with {_entity()}.</p>
<h2>1. Your account</h2><p>You must give accurate information, keep your password safe and be responsible for activity on your account.
If you are under 18 you confirm a parent or guardian permits your use.</p>
<h2>2. Content &amp; conduct</h2><p>You own what you post but grant us a licence to host and display it to run the service. Do not post anything unlawful,
hateful, sexual involving minors, infringing, misleading, or that harasses others, and do not upload content you do not have rights to.
We may remove content and suspend accounts that break these rules. Copyright complaints follow our
<a href="/legal/copyright-policy/">Copyright Policy</a>.</p>
<h2>3. Classes, tests &amp; teachers</h2><p>Teachers are responsible for their course content and pricing. Cheating, sharing paid material and abusing
referrals or rewards are prohibited and can lead to removal and forfeiture of rewards.</p>
<h2>4. Coins &amp; payments</h2><p>Coins are bought through our payment provider and spent inside the app. Purchases are charged in INR at the price shown.
Withdrawals are available only for eligible earned coins, after review, and may be refused for fraud or policy violations.
Refunds follow our <a href="/legal/refund-policy/">Refund Policy</a> and applicable law.</p>
<h2>5. Termination</h2><p>You can delete your account any time in Settings. We may suspend or end access for violations or legal reasons.</p>
<h2>6. Disclaimers &amp; liability</h2><p>The service is provided “as is”. To the extent allowed by law we are not liable for indirect losses, and our
total liability is limited to the amount you paid us in the 12 months before the claim.</p>
<h2>7. Governing law</h2><p>Indian law applies; courts at {escape(_cfg('LEGAL_JURISDICTION','the place of our registered office'))} have jurisdiction.</p>
<h2>8. Contact</h2><p>{_contact()} · <a href="/legal/privacy/">Privacy Policy</a></p>"""
    return _page("Terms of Service", body, lang=lang, summary_html=_HI_TERMS if lang == "hi" else "")


def delete_account_page(request):
    url = _cfg("DELETE_ACCOUNT_URL")
    if url:
        return HttpResponseRedirect(url)
    mail = _cfg("SUPPORT_EMAIL") or _cfg("GRIEVANCE_OFFICER_EMAIL")
    mail_html = (f'<a href="mailto:{escape(mail)}?subject=Delete%20my%20LearnScroll%20account">{escape(mail)}</a>'
                 if mail else "our support team")
    body = f"""
<h2>Delete your account in the app (fastest)</h2><ol>
<li>Open LearnScroll → <b>Profile → Settings → Delete account</b>.</li>
<li>Read the summary, enter your password (if your account has one) and type <code>DELETE</code>.</li>
<li>Confirm. Your account is scheduled for deletion in 30 days. Until then it still works and you can cancel in <b>Settings → Data &amp; privacy</b>; after that it is erased and you are signed out everywhere.</li></ol>
<h2>Can’t open the app?</h2><p>Email {mail_html} from the address or phone number registered on the account, with the subject
“Delete my LearnScroll account” and your username. We verify ownership and complete deletion within 30 days.</p>
<h2>Download your data first</h2><p>In the app: <b>Settings → Data &amp; privacy → Download my data</b>. We prepare a ZIP of your profile, posts, comments, stories, saved posts, likes, follows, the messages you sent, coin history, certificates, study activity (streak, time spent, recaps, badges, test attempts, class attendance) and consent history; it stays available for 7 days.</p>
<h2>What is deleted</h2><ul><li>Posts, stories, comments, reactions, saved items and follows</li>
<li>Study activity (streak, time spent, weekly recaps, badges) and your app, privacy and notification settings</li>
<li>Profile photo and personal details (name, email, phone, bio, date of birth)</li>
<li>Your sessions and push-notification tokens</li></ul>
<h2>What we keep, and why</h2><ul><li>Messages you sent in chats and groups stay in those conversations for the other participants, shown under a deleted account with no name or photo</li><li>Coin, payment and withdrawal records — tax / accounting law — with no personal identifiers on the account</li>
<li>Moderation and copyright records — legal obligations and safety</li></ul>
<p>Coins left in your wallet are forfeited on deletion; withdraw eligible coins first. A withdrawal in progress must finish before you can delete.</p>
<p>Partial deletion (keep the account, remove specific content) is possible by deleting those posts in the app.</p>
<p><a href="/legal/privacy/">Privacy Policy</a></p>"""
    return _page("Delete your LearnScroll account", body)


_HI_REFUND = _HI_NOTE + """
<h2>मुख्य बातें</h2><ul>
<li><b>कॉइन ख़रीदना:</b> इस्तेमाल हो चुके कॉइन वापस नहीं होते। पेमेंट कटा पर कॉइन नहीं आए तो हमसे संपर्क करें — हम जाँच कर कॉइन जोड़ेंगे या पैसे लौटाएँगे।</li>
<li><b>क्लास पास / टेस्ट सीरीज़:</b> जो क्लास नहीं हुई या टीचर ने रद्द की, उसके कॉइन वापस मिलते हैं।</li>
<li><b>कैंपस फ़ीस:</b> रिफ़ंड स्कूल/संस्थान की नीति के अनुसार; ग़लत या दोहरा पेमेंट हो तो संपर्क करें।</li>
<li>रिफ़ंड मंज़ूर होने पर पैसा उसी तरीके से वापस आता है, आमतौर पर 5–7 कामकाजी दिनों में।</li></ul>
"""


def refund_policy(request):
    lang = _lang(request)
    url = _cfg("REFUND_POLICY_URL")
    if url:
        return HttpResponseRedirect(url)
    days = escape(str(_cfg("REFUND_PROCESSING_DAYS", "5-7")))
    body = f"""
<p>This page explains when you can get money or coins back for purchases made on {_entity()}. Prices are in Indian rupees (INR).
Card / UPI details are handled by our payment provider and never reach our servers.</p>
<h2>1. Coin purchases</h2><ul>
<li>Coins are added to your wallet only after the payment provider confirms the payment. If your bank shows a debit but no coins arrived,
wait a few minutes (our system re-checks every 15 minutes), then contact us with the receipt or the payment ID.</li>
<li>If the payment was charged twice or by mistake, we refund the extra amount to the original payment method.</li>
<li>Coins you have already spent, gifted or withdrawn are not refundable. Unused coins are refunded only where the law requires it
or at our discretion, for example for a duplicate charge.</li></ul>
<h2>2. Class passes</h2><ul>
<li>A pass is held in escrow and paid to the teacher as classes are actually held. Classes that were cancelled or never held are refunded
to your wallet as coins.</li>
<li>If a classroom is closed or you are removed by the teacher without a rule violation, the unused part is returned as coins.</li>
<li>Passes that are used up or have expired are not refundable.</li></ul>
<h2>3. Test series</h2><ul>
<li>Coins are held until the whole attempt has been reviewed. If the creator removes the series or an attempt cannot be checked, the coins
are returned to your wallet.</li>
<li>A series you attempted and received results for is not refundable.</li></ul>
<h2>4. Campus (school / institute) fees</h2><ul>
<li>Fees are collected on behalf of your institute. Refunds follow the institute's own fee policy and are issued by the institute from the
fee desk; the receipt and the refund status show in the app.</li>
<li>If you paid the wrong amount or paid twice, tell the institute and us; the extra amount is returned to the original payment method.</li></ul>
<h2>5. How to ask for a refund</h2><p>Open <b>Wallet → Receipts</b>, note the receipt number, and contact {_contact()}.
We reply within 3 working days. Approved refunds go back to the original payment method (or to your wallet as coins where stated
above), normally within {days} working days. Your bank may take longer to show it.</p>
<h2>6. Withdrawals</h2><p>Coin withdrawals are separate from refunds and follow the withdrawal rules in the app (currently switched off).</p>
<p><a href="/legal/terms/">Terms of Service</a> · <a href="/legal/privacy/">Privacy Policy</a></p>"""
    return _page("Refund & Cancellation Policy", body, lang=lang, summary_html=_HI_REFUND if lang == "hi" else "")
