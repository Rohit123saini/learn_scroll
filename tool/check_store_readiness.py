#!/usr/bin/env python3
"""P-I -- static store-readiness check for the FULL Flutter project (needs pubspec.yaml, android/, ios/).

    python3 tool/check_store_readiness.py --root .            # report; exit 1 on any ERROR
    python3 tool/check_store_readiness.py --root . --strict   # WARN also fails

No dependencies (stdlib only, no PyYAML). Pure text checks, so it cannot replace `flutter build` -- it catches the
things Play / App Review reject for and that are easy to forget:
  pubspec    : version pins (^ / any / ranges), pubspec.lock present, declared-but-unused packages, ad/tracking SDKs
  android    : minify + shrinkResources, proguard file, targetSdk, release not signed with debug, forbidden permissions,
               allowBackup / cleartext, key.properties + keystores git-ignored
  ios        : usage-description strings for the plugins in use, ITSAppUsesNonExemptEncryption, ATS, PrivacyInfo.xcprivacy
Written by reading only -- not run by the author.
"""
import argparse
import re
import sys
from pathlib import Path

TARGET_SDK_MIN = 35
FORBIDDEN_ANDROID_PERMS = ("MANAGE_EXTERNAL_STORAGE", "READ_PHONE_STATE", "SYSTEM_ALERT_WINDOW", "READ_SMS", "SEND_SMS",
                           "READ_CALL_LOG", "REQUEST_INSTALL_PACKAGES", "QUERY_ALL_PACKAGES")
TRACKING_SDKS = ("easy_audience_network_plus", "facebook_app_events", "flutter_facebook_auth", "appsflyer_sdk",
                 "adjust_sdk", "google_mobile_ads", "unity_ads_plugin", "applovin_max")
# plugin -> iOS Info.plist keys that must exist when the plugin is used
IOS_KEYS = {
    "camera": ["NSCameraUsageDescription", "NSMicrophoneUsageDescription"],
    "image_picker": ["NSPhotoLibraryUsageDescription", "NSCameraUsageDescription"],
    "mobile_scanner": ["NSCameraUsageDescription"],
    "record": ["NSMicrophoneUsageDescription"],
    "flutter_webrtc": ["NSCameraUsageDescription", "NSMicrophoneUsageDescription"],
    "livekit_client": ["NSCameraUsageDescription", "NSMicrophoneUsageDescription"],
    "speech_to_text": ["NSSpeechRecognitionUsageDescription", "NSMicrophoneUsageDescription"],
    "geolocator": ["NSLocationWhenInUseUsageDescription"],
    "photo_manager": ["NSPhotoLibraryUsageDescription", "NSPhotoLibraryAddUsageDescription"],
    "gal": ["NSPhotoLibraryAddUsageDescription"],
}
# packages that are used without an `import 'package:x'` line in lib/ (transitive / build-time / platform glue)
ALWAYS_OK = {"flutter", "flutter_localizations", "cupertino_icons", "flutter_test", "flutter_lints", "flutter_launcher_icons",
             "flutter_native_splash", "integration_test", "build_runner"}

results = []  # (level, area, message)


def add(level, area, msg):
    results.append((level, area, msg))


def read(path):
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def parse_pubspec(text):
    """-> {section: {package: constraint-string}} for dependencies / dev_dependencies (flat `name: value` lines only)."""
    out, section = {}, None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith(" ") and raw.rstrip().endswith(":"):
            section = raw.strip()[:-1]
            out.setdefault(section, {})
            continue
        if section in ("dependencies", "dev_dependencies") and re.match(r"^  [A-Za-z0-9_]+:", raw):
            name, _, value = raw.strip().partition(":")
            out[section][name] = value.split("#")[0].strip()
    return out


def check_pubspec(root):
    text = read(root / "pubspec.yaml")
    if text is None:
        add("ERROR", "pubspec", "pubspec.yaml not found under --root (upload the full project, not only lib/)")
        return set()
    deps = parse_pubspec(text)
    runtime = deps.get("dependencies", {})
    if not (root / "pubspec.lock").exists():
        add("ERROR", "pubspec", "pubspec.lock missing: commit it so every build resolves the same versions")
    loose = [n for n, v in runtime.items() if n not in ALWAYS_OK and (v in ("", "any") or v.startswith(("^", ">", "<", "~")))]
    for n in loose:
        add("WARN", "pubspec", "%s: %s is a range, not a pin (use an exact version, e.g. 1.2.3; pubspec.lock still pins builds)"
            % (n, runtime[n] or "any"))
    if re.search(r"^version:\s*\d+\.\d+\.\d+\+\d+\s*$", text, re.M) is None:
        add("ERROR", "pubspec", "`version: x.y.z+build` line missing/invalid (Play needs an increasing build number)")
    for n, v in runtime.items():
        if "git:" in v or v.startswith("git"):
            add("WARN", "pubspec", "%s comes from git: release builds are not reproducible" % n)
    # imports in lib/
    used = set()
    lib = root / "lib"
    if lib.is_dir():
        for f in lib.rglob("*.dart"):
            for m in re.finditer(r"^\s*(?:import|export)\s+'package:([a-z0-9_]+)/", read(f) or "", re.M):
                used.add(m.group(1))
    for n in sorted(runtime):
        if n not in ALWAYS_OK and n not in used:
            add("WARN", "pubspec", "%s is declared but never imported in lib/ (remove it, or confirm it is a native-only plugin)" % n)
    for n in sorted(used - set(runtime) - {"flutter"}):
        add("ERROR", "pubspec", "lib/ imports package:%s but pubspec.yaml does not declare it" % n)
    for n in TRACKING_SDKS:
        if n in runtime:
            add("WARN", "pubspec", "%s is an ads/tracking SDK: needs ATT prompt, NSPrivacyTracking=true, Play Data safety 'shared' answers" % n)
    return set(runtime)


def check_android(root):
    gradle = None
    for name in ("build.gradle", "build.gradle.kts"):
        gradle = read(root / "android" / "app" / name)
        if gradle is not None:
            break
    if gradle is None:
        add("ERROR", "android", "android/app/build.gradle(.kts) not found")
    else:
        if not re.search(r"(minifyEnabled|isMinifyEnabled)\s*(=)?\s*true", gradle):
            add("ERROR", "android", "release build: minify is not enabled (minifyEnabled true)")
        if not re.search(r"(shrinkResources|isShrinkResources)\s*(=)?\s*true", gradle):
            add("WARN", "android", "shrinkResources is not enabled")
        m = re.search(r"targetSdk(?:Version)?\s*(?:=)?\s*(\d+)", gradle)
        if m is None:
            add("WARN", "android", "targetSdk is not set explicitly (it follows flutter.targetSdkVersion): confirm it is >= %d" % TARGET_SDK_MIN)
        elif int(m.group(1)) < TARGET_SDK_MIN:
            add("ERROR", "android", "targetSdk %s < %d (Play rejects new uploads below the current target API)" % (m.group(1), TARGET_SDK_MIN))
        rel = re.search(r"release\s*\{(.*?)\n\s{8}\}", gradle, re.S)
        if rel and "signingConfigs.debug" in rel.group(1):
            add("ERROR", "android", "release buildType is signed with the DEBUG key")
        if "signingConfigs" not in gradle or "key.properties" not in gradle:
            add("WARN", "android", "no release signingConfig reading key.properties (see store_readiness/android/app-build.gradle.snippet)")
        if not (root / "android" / "app" / "proguard-rules.pro").exists():
            add("ERROR", "android", "android/app/proguard-rules.pro missing (Razorpay / LiveKit / Firebase need keep rules)")
    manifest = read(root / "android" / "app" / "src" / "main" / "AndroidManifest.xml")
    if manifest is None:
        add("ERROR", "android", "AndroidManifest.xml not found")
    else:
        for perm in FORBIDDEN_ANDROID_PERMS:
            if "android.permission.%s" % perm in manifest:
                add("ERROR", "android", "permission %s needs a Play declaration and is usually rejected" % perm)
        if 'android:allowBackup="true"' in manifest or "android:allowBackup" not in manifest:
            add("WARN", "android", 'set android:allowBackup="false" (tokens/DB would be copied to cloud backups)')
        if 'usesCleartextTraffic="true"' in manifest:
            add("ERROR", "android", "usesCleartextTraffic=true in the release manifest")
        if "autoVerify" not in manifest:
            add("WARN", "android", "no intent-filter with android:autoVerify (verified https app links to /u/, /p/, ...)")
    ign = (read(root / ".gitignore") or "") + (read(root / "android" / ".gitignore") or "")
    for needle in ("key.properties", ".jks"):
        if needle not in ign:
            add("ERROR", "android", "%s is not in .gitignore (signing secrets would be committed)" % needle)


def check_ios(root, used_packages):
    plist = read(root / "ios" / "Runner" / "Info.plist")
    if plist is None:
        add("ERROR", "ios", "ios/Runner/Info.plist not found")
        return
    needed = set()
    for pkg, keys in IOS_KEYS.items():
        if pkg in used_packages:
            needed.update(keys)
    for key in sorted(needed):
        m = re.search(r"<key>%s</key>\s*<string>(.*?)</string>" % key, plist, re.S)
        if m is None:
            add("ERROR", "ios", "Info.plist is missing %s (the app crashes / is rejected when the permission is requested)" % key)
        elif len(m.group(1).strip()) < 20:
            add("WARN", "ios", "%s text is too short for App Review" % key)
    if "ITSAppUsesNonExemptEncryption" not in plist:
        add("WARN", "ios", "ITSAppUsesNonExemptEncryption not set (App Store Connect asks for export compliance on every upload)")
    if re.search(r"NSAllowsArbitraryLoads</key>\s*<true/>", plist):
        add("ERROR", "ios", "NSAllowsArbitraryLoads is true")
    if "voip" in used_packages or "flutter_callkit_incoming" in used_packages:
        if "<string>voip</string>" not in plist:
            add("ERROR", "ios", "flutter_callkit_incoming is used but UIBackgroundModes has no 'voip'")
    if "firebase_messaging" in used_packages and "remote-notification" not in plist:
        add("ERROR", "ios", "firebase_messaging is used but UIBackgroundModes has no 'remote-notification'")
    privacy = read(root / "ios" / "Runner" / "PrivacyInfo.xcprivacy")
    if privacy is None:
        add("ERROR", "ios", "ios/Runner/PrivacyInfo.xcprivacy missing (Privacy Manifest is required)")
    elif any(n in used_packages for n in TRACKING_SDKS) and re.search(r"NSPrivacyTracking</key>\s*<false/>", privacy):
        add("ERROR", "ios", "a tracking SDK is present but NSPrivacyTracking is false")
    if not (root / "ios" / "Runner" / "Runner.entitlements").exists():
        add("WARN", "ios", "Runner.entitlements missing: needs aps-environment + associated-domains (applinks:<api host>)")
    ent = read(root / "ios" / "Runner" / "Runner.entitlements") or ""
    if ent and "applinks:" not in ent:
        add("WARN", "ios", "Runner.entitlements has no applinks: associated domain (universal links)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="Flutter project root (folder with pubspec.yaml)")
    ap.add_argument("--strict", action="store_true", help="treat WARN as failure")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    declared = check_pubspec(root)
    check_android(root)
    check_ios(root, declared)
    errors = [r for r in results if r[0] == "ERROR"]
    warns = [r for r in results if r[0] == "WARN"]
    for level, area, msg in sorted(results, key=lambda r: (r[0] != "ERROR", r[1])):
        print("%-5s [%s] %s" % (level, area, msg))
    print("\n%d error(s), %d warning(s)" % (len(errors), len(warns)))
    return 1 if errors or (args.strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
