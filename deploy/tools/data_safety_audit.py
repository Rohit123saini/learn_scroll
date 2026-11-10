#!/usr/bin/env python3
"""P-G3: compare what the Flutter app really contains (packages, permissions) with deploy/PLAY_DATA_SAFETY.md.

    python deploy/tools/data_safety_audit.py --app /path/to/flutter_project [--doc deploy/PLAY_DATA_SAFETY.md]

Reads (read-only): <app>/pubspec.lock (falls back to `import 'package:...'` in lib/), android/app/src/*/AndroidManifest.xml,
ios/Runner/Info.plist. Prints every SDK / permission that collects data but is NOT mentioned in the doc. Exit 1 if any.
It cannot know what an SDK collects in a newer version: copy the final list from each vendor's disclosure page.
"""
import argparse
import re
import sys
from pathlib import Path

# package -> (what it collects / where it goes, keywords that must appear in the doc to count as disclosed)
SDKS = {
    "firebase_messaging": ("FCM token, device id -> Google", ["fcm"]),
    "firebase_core": ("Firebase install id -> Google", ["firebase", "fcm"]),
    "sentry_flutter": ("crash logs, diagnostics -> Sentry", ["sentry"]),
    "razorpay_flutter": ("payment + device info -> Razorpay", ["razorpay"]),
    "livekit_client": ("voice / video media in calls and live classes", ["livekit"]),
    "flutter_webrtc": ("voice / video media", ["livekit", "webrtc"]),
    "easy_audience_network_plus": ("Advertising ID, ad interactions -> Meta", ["audience network"]),
    "google_sign_in": ("Google account email / name / id token -> Google", ["google sign"]),
    "google_fonts": ("fonts fetched from Google servers (IP address)", ["google fonts"]),
    "android_play_install_referrer": ("install referrer string (campaign / invite code) <- Google Play", ["install referrer"]),
    "geolocator": ("PRECISE LOCATION when the user shares a location in chat", ["location"]),
    "speech_to_text": ("voice audio processed by the phone's speech recogniser (Google / Apple)", ["speech"]),
    "record": ("microphone audio (voice notes, voiceovers)", ["audio"]),
    "camera": ("camera photos / video", ["camera", "photos"]),
    "image_picker": ("photos / videos the user picks", ["photos"]),
    "photo_manager": ("gallery access", ["photos"]),
    "device_info_plus": ("device model / OS / per-install id", ["device"]),
    "mobile_scanner": ("camera (QR scan)", ["camera", "qr"]),
    "flutter_callkit_incoming": ("call UI / VoIP push token", ["callkit", "call"]),
    "google_mlkit_text_recognition": ("on-device only (not collected)", []),
    "google_mlkit_face_detection": ("on-device only (not collected)", []),
}
PERMS = {
    "ACCESS_FINE_LOCATION": ["location"], "ACCESS_COARSE_LOCATION": ["location"],
    "CAMERA": ["camera"], "RECORD_AUDIO": ["audio"], "READ_CONTACTS": ["contacts"],
    "READ_MEDIA_IMAGES": ["photos"], "READ_MEDIA_VIDEO": ["photos", "video"],
    "READ_EXTERNAL_STORAGE": ["photos"], "WRITE_EXTERNAL_STORAGE": ["photos"],
    "com.google.android.gms.permission.AD_ID": ["advertising id"],
    "POST_NOTIFICATIONS": [], "READ_PHONE_STATE": ["phone"], "READ_SMS": ["sms"], "RECEIVE_SMS": ["sms"],
    "SYSTEM_ALERT_WINDOW": [], "FOREGROUND_SERVICE_MICROPHONE": [], "FOREGROUND_SERVICE_CAMERA": [],
}
PLIST_KEYS = ["NSCameraUsageDescription", "NSMicrophoneUsageDescription", "NSPhotoLibraryUsageDescription",
              "NSLocationWhenInUseUsageDescription", "NSContactsUsageDescription", "NSSpeechRecognitionUsageDescription",
              "NSUserTrackingUsageDescription"]


def packages(app: Path):
    lock = app / "pubspec.lock"
    if lock.exists():
        return set(re.findall(r"^  ([a-z0-9_]+):\s*$", lock.read_text(errors="ignore"), re.M)), "pubspec.lock"
    found = set()
    for f in (app / "lib").rglob("*.dart"):
        found.update(re.findall(r"^import 'package:([a-z0-9_]+)", f.read_text(errors="ignore"), re.M))
    return found, "lib/ imports (no pubspec.lock found)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--doc", default=str(Path(__file__).resolve().parents[1] / "PLAY_DATA_SAFETY.md"))
    a = ap.parse_args()
    raw = Path(a.doc).read_text(errors="ignore")
    # "Not collected: ..." lines say what we do NOT collect: they must never count as a disclosure
    app, doc = Path(a.app), "\n".join(l for l in raw.lower().splitlines() if not l.strip().startswith("not collected")).lower()
    bad = []
    pk, src = packages(app)
    print(f"packages from {src}: {len(pk)}")
    for name, (what, kws) in sorted(SDKS.items()):
        if name in pk and kws and not any(k in doc for k in kws):
            bad.append(f"SDK {name}: {what}  -> not in doc")
    for mf in app.glob("android/app/src/*/AndroidManifest.xml"):
        text = mf.read_text(errors="ignore")
        for perm in re.findall(r'uses-permission[^>]*android:name="([^"]+)"', text):
            short = perm.split(".")[-1]
            key = perm if perm in PERMS else short
            kws = PERMS.get(key)
            if kws is None:
                print(f"  (note) {mf.name}: permission {short} not classified - review by hand")
            elif kws and not any(k in doc for k in kws):
                bad.append(f"permission {short} ({mf.parent.parent.name}) -> not in doc")
    plist = app / "ios/Runner/Info.plist"
    if plist.exists():
        t = plist.read_text(errors="ignore")
        for k in PLIST_KEYS:
            if k in t:
                print(f"  iOS declares {k}: make sure the matching data type is in the privacy nutrition label")
    for b in bad:
        print("MISMATCH:", b)
    print(f"{len(bad)} mismatch(es)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
