package com.learnscroll.app

import android.app.PictureInPictureParams
import android.content.res.Configuration
import android.os.Build
import android.util.Rational
import androidx.annotation.NonNull
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * Real OS-level Picture-in-Picture for the LiveClass room.
 *
 * How this actually works: Android PiP shrinks the WHOLE Activity surface
 * into the floating window — it does not need a separate video feed. Since
 * Flutter renders as one surface, once we call enterPictureInPictureMode(),
 * whatever `live_session_screen.dart` is drawing at that moment is what
 * shows up in the floating window. So the Dart side's job (see
 * pip_service.dart + the onPipModeChanged listener in live_session_screen.dart)
 * is to rebuild into a minimal, chrome-free, video-only layout the instant
 * this native side reports isInPictureInPictureMode == true — otherwise the
 * PiP window will show buttons/chat/appbar squeezed into a postage stamp.
 *
 * `pipEnabled` is controlled entirely from Dart: true only while the user is
 * actually inside a live session room (set in LiveSessionScreen's
 * _afterJoined / dispose). PiP must never trigger from any other screen.
 */
class MainActivity : FlutterActivity() {
    private val PIP_CHANNEL = "learnscroll/pip"
    private var pipEnabled = false
    private lateinit var methodChannel: MethodChannel

    override fun configureFlutterEngine(@NonNull flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        methodChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, PIP_CHANNEL)
        methodChannel.setMethodCallHandler { call, result ->
            when (call.method) {
                "setPipEnabled" -> {
                    pipEnabled = call.argument<Boolean>("enabled") ?: false
                    result.success(null)
                }
                "enterPip" -> result.success(enterPip())
                "isPipSupported" -> result.success(Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                else -> result.notImplemented()
            }
        }
    }

    /**
     * Manual trigger (e.g. the existing mini-view button) AND the auto path
     * below both funnel through here.
     *
     * Aspect ratio is fixed to a portrait-ish camera tile (9:16). If you
     * later want it to match the actual active speaker's real video
     * dimensions, pass the ratio in from Dart via the same "enterPip" call
     * instead of hardcoding it.
     */
    private fun enterPip(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return false
        if (!pipEnabled) return false
        val params = PictureInPictureParams.Builder()
            .setAspectRatio(Rational(9, 16))
            .build()
        return try {
            enterPictureInPictureMode(params)
            true
        } catch (e: IllegalStateException) {
            // Activity not resumed / not in a state that allows PiP right now.
            false
        }
    }

    /**
     * Fired the instant the user leaves the app via home button / recents /
     * task switch — this is the actual "backgrounds but video keeps
     * floating" trigger, distinct from onPause (which also fires for things
     * like a system dialog popping up, where we do NOT want to enter PiP).
     */
    override fun onUserLeaveHint() {
        super.onUserLeaveHint()
        if (pipEnabled) enterPip()
    }

    override fun onPictureInPictureModeChanged(
        isInPictureInPictureMode: Boolean,
        newConfig: Configuration
    ) {
        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig)
        methodChannel.invokeMethod("onPipModeChanged", isInPictureInPictureMode)
    }
}