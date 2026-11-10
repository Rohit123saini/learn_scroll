# P-I -- android/app/proguard-rules.pro. Packages come from lib/ imports (razorpay_flutter, firebase_*, livekit_client /
# flutter_webrtc, sentry_flutter, ffmpeg_kit_flutter_new, google_mlkit_*, flutter_callkit_incoming, record, ...).
# Keep rules are deliberately narrow; widen only after a release-build crash points at a class.

# Flutter / plugin registrant
-keep class io.flutter.app.** { *; }
-keep class io.flutter.plugin.** { *; }
-keep class io.flutter.embedding.** { *; }
-dontwarn io.flutter.embedding.engine.deferredcomponents.**

# Razorpay (uses reflection + annotations)
-keepattributes *Annotation*
-dontwarn com.razorpay.**
-keep class com.razorpay.** { *; }
-optimizations !method/inlining/*
-keepclasseswithmembers class * { public void onPayment*(...); }

# Firebase / Google Play services
-keep class com.google.firebase.** { *; }
-keep class com.google.android.gms.** { *; }
-dontwarn com.google.firebase.**
-dontwarn com.google.android.gms.**

# LiveKit + WebRTC
-keep class io.livekit.** { *; }
-keep class livekit.org.webrtc.** { *; }
-keep class org.webrtc.** { *; }
-dontwarn org.webrtc.**
-dontwarn livekit.org.webrtc.**

# Sentry (native part; Dart stack traces need --split-debug-info symbols uploaded, see STORE_READINESS.md)
-keep class io.sentry.** { *; }
-dontwarn io.sentry.**

# FFmpegKit (JNI)
-keep class com.antonkarpenko.ffmpegkit.** { *; }
-keep class com.arthenica.ffmpegkit.** { *; }
-dontwarn com.arthenica.**

# ML Kit (text recognition, face detection) -- optional language models are not bundled
-dontwarn com.google.mlkit.vision.text.chinese.**
-dontwarn com.google.mlkit.vision.text.devanagari.**
-dontwarn com.google.mlkit.vision.text.japanese.**
-dontwarn com.google.mlkit.vision.text.korean.**

# CallKit-style incoming-call UI + foreground services
-keep class com.hiennv.flutter_callkit_incoming.** { *; }

# Play Core split-install classes referenced by Flutter but not shipped
-dontwarn com.google.android.play.core.**
