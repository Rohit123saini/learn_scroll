import UIKit
import AVKit
import Flutter

/// Real OS-level Picture-in-Picture for the LiveClass room, iOS 15+.
///
/// Unlike Android (which just shrinks the whole Activity surface into the
/// floating window), iOS needs a dedicated video-call PiP content source:
/// an `AVPictureInPictureController` pointed at an
/// `AVPictureInPictureVideoCallViewController` that hosts an
/// `AVSampleBufferDisplayLayer`. This file wires up that mechanism fully —
/// start/stop, lifecycle delegate callbacks back to Dart, the display layer.
///
/// ⚠️ ONE PIECE THIS FILE CANNOT FINISH ON ITS OWN: actually feeding video
/// frames into `_displayLayer`. `livekit_client`'s iOS side renders the
/// local camera track through its own internal `RTCMTLVideoView` (via
/// flutter_webrtc), and that frame stream isn't exposed to app-level native
/// code today. To close the loop, `feed(pixelBuffer:)` below needs to be
/// called from a small addition on the `livekit_client`/`flutter_webrtc`
/// iOS pod side — a secondary `RTCVideoRenderer` attached to the same
/// `RTCVideoTrack` that already renders into the in-app video tile, calling
/// back into this class. That's a ~20-line patch in
/// `ios/Classes/*VideoView*.swift` inside the plugin's own source (found via
/// `flutter pub deps` -> pub-cache path), not something this app-level file
/// can reach into from outside. Flagging this explicitly rather than
/// pretending frames are flowing when they aren't yet.
@available(iOS 15.0, *)
class PipManager: NSObject, AVPictureInPictureControllerDelegate,
    AVPictureInPictureSampleBufferPlaybackDelegate {

    static let shared = PipManager()

    private var pipController: AVPictureInPictureController?
    private let displayLayer = AVSampleBufferDisplayLayer()
    private var sourceView: UIView?
    private var channel: FlutterMethodChannel?
    private var pipEnabled = false

    func attach(to messenger: FlutterBinaryMessenger, rootView: UIView) {
        channel = FlutterMethodChannel(name: "learnscroll/pip", binaryMessenger: messenger)
        channel?.setMethodCallHandler { [weak self] call, result in
            guard let self = self else { return }
            switch call.method {
            case "setPipEnabled":
                let args = call.arguments as? [String: Any]
                self.pipEnabled = (args?["enabled"] as? Bool) ?? false
                if !self.pipEnabled { self.pipController?.stopPictureInPicture() }
                result(nil)
            case "enterPip":
                result(self.enterPip())
            case "isPipSupported":
                result(AVPictureInPictureController.isPictureInPictureSupported())
            default:
                result(FlutterMethodNotImplemented)
            }
        }
        setupController(rootView: rootView)
    }

    private func setupController(rootView: UIView) {
        guard AVPictureInPictureController.isPictureInPictureSupported() else { return }

        let sourceView = UIView(frame: .zero)
        sourceView.isHidden = true // never shown inline — PiP source only
        rootView.addSubview(sourceView)
        self.sourceView = sourceView

        let callVC = AVPictureInPictureVideoCallViewController()
        callVC.preferredContentSize = CGSize(width: 9, height: 16)
        callVC.view.layer.addSublayer(displayLayer)

        let contentSource = AVPictureInPictureController.ContentSource(
            activeVideoCallSourceView: sourceView,
            contentViewController: callVC
        )
        let controller = AVPictureInPictureController(contentSource: contentSource)
        controller.delegate = self
        controller.canStartPictureInPictureAutomaticallyFromInline = true
        self.pipController = controller
    }

    private func enterPip() -> Bool {
        guard pipEnabled, let controller = pipController,
              controller.isPictureInPicturePossible else { return false }
        controller.startPictureInPicture()
        return true
    }

    /// See the header note above — this is the intended integration point
    /// once the plugin-side frame tap exists.
    func feed(pixelBuffer: CVPixelBuffer, presentationTime: CMTime) {
        // Wrap into a CMSampleBuffer and enqueue on `displayLayer`.
        // Deliberately left as the documented gap above rather than a
        // silent no-op wearing the appearance of a finished feature.
    }

    // MARK: AVPictureInPictureControllerDelegate
    func pictureInPictureControllerDidStartPictureInPicture(_ controller: AVPictureInPictureController) {
        channel?.invokeMethod("onPipModeChanged", arguments: true)
    }

    func pictureInPictureControllerDidStopPictureInPicture(_ controller: AVPictureInPictureController) {
        channel?.invokeMethod("onPipModeChanged", arguments: false)
    }

    // MARK: AVPictureInPictureSampleBufferPlaybackDelegate (required stubs — live feed, not seekable media)
    func pictureInPictureController(_ controller: AVPictureInPictureController, setPlaying playing: Bool) {}
    func pictureInPictureControllerTimeRangeForPlayback(_ controller: AVPictureInPictureController) -> CMTimeRange {
        CMTimeRange(start: .zero, duration: .positiveInfinity)
    }
    func pictureInPictureControllerIsPlaybackPaused(_ controller: AVPictureInPictureController) -> Bool { false }
    func pictureInPictureController(_ controller: AVPictureInPictureController, didTransitionToRenderSize newRenderSize: CMVideoDimensions) {}
    func pictureInPictureController(_ controller: AVPictureInPictureController, skipByInterval skipInterval: CMTime, completion completionHandler: @escaping () -> Void) {
        completionHandler()
    }
}
