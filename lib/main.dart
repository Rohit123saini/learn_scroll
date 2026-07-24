// import 'package:easy_audience_network_plus/easy_audience_network.dart';
// import 'package:flutter/material.dart';

// void main() => runApp(AdExampleApp());

// class AdExampleApp extends StatelessWidget {
//   @override
//   Widget build(BuildContext context) {
//     return MaterialApp(
//       debugShowCheckedModeBanner: false,
//       title: 'Audience Network Example',
//       theme: ThemeData(
//         primarySwatch: Colors.blue,
//         buttonTheme: ButtonThemeData(
//           textTheme: ButtonTextTheme.primary,
//           buttonColor: Colors.blue,
//         ),
//       ),
//       home: Scaffold(
//         appBar: AppBar(
//           title: Text(
//             'Audience Network Example',
//           ),
//         ),
//         body: AdsPage(),
//       ),
//     );
//   }
// }

// class AdsPage extends StatefulWidget {
//   final String idfa;

//   const AdsPage({Key? key, this.idfa = ''}) : super(key: key);

//   @override
//   AdsPageState createState() => AdsPageState();
// }

// class AdsPageState extends State<AdsPage> {
//   bool _isInterstitialAdLoaded = false;
//   bool _isRewardedAdLoaded = false;
//   InterstitialAd? _interstitialAd;
//   RewardedAd? _rewardedAd;

//   /// All widget ads are stored in this variable. When a button is pressed, its
//   /// respective ad widget is set to this variable and the view is rebuilt using
//   /// setState().
//   Widget _currentAd = SizedBox(
//     width: 0.0,
//     height: 0.0,
//   );

//   @override
//   void initState() {
//     super.initState();

//     // testingId is useful when you want to test if your implementation works in production
//     // without getting real ads, I believe it does not work properly on iOS,
//     // if you want to get your testingId, don't set any testingId and don't set testMode
//     EasyAudienceNetwork.init(
//       testingId: "b602d594afd2b0b327e07a06f36ca6a7e42546d0",
//       testMode: true,
//       iOSAdvertiserTrackingEnabled: true,
//     ).then((_) {
//       _loadInterstitialAd();
//       _loadRewardedVideoAd();
//     });
//   }

//   void _loadInterstitialAd() {
//     final interstitialAd = InterstitialAd(InterstitialAd.testPlacementId);
//     interstitialAd.listener = InterstitialAdListener(
//       onLoaded: () {
//         _isInterstitialAdLoaded = true;
//         print('interstitial ad loaded');
//       },
//       onError: (code, message) {
//         print('interstitial ad error\ncode = $code\nmessage = $message');
//       },
//       onDismissed: () {
//         // load next ad already
//         interstitialAd.destroy();
//         _isInterstitialAdLoaded = false;
//         _loadInterstitialAd();
//       },
//     );
//     interstitialAd.load();
//     _interstitialAd = interstitialAd;
//   }

//   void _loadRewardedVideoAd() {
//     final rewardedAd = RewardedAd(RewardedAd.testPlacementId);
//     rewardedAd.listener = RewardedAdListener(
//       onLoaded: () {
//         _isRewardedAdLoaded = true;
//         print('rewarded ad loaded');
//       },
//       onError: (code, message) {
//         print('rewarded ad error\ncode = $code\nmessage = $message');
//       },
//       onVideoClosed: () {
//         // load next ad already
//         rewardedAd.destroy();
//         _isRewardedAdLoaded = false;
//         _loadRewardedVideoAd();
//       },
//     );
//     rewardedAd.load();
//     _rewardedAd = rewardedAd;
//   }

//   @override
//   Widget build(BuildContext context) {
//     return Column(
//       mainAxisAlignment: MainAxisAlignment.start,
//       crossAxisAlignment: CrossAxisAlignment.center,
//       children: <Widget>[
//         Flexible(
//           child: Align(
//             alignment: Alignment(0, -1.0),
//             child: Padding(
//               padding: EdgeInsets.all(16),
//               child: _getAllButtons(),
//             ),
//           ),
//           fit: FlexFit.tight,
//           flex: 2,
//         ),
//         // Column(children: <Widget>[
//         //   _nativeAd(),
//         //   // _nativeBannerAd(),
//         //   _nativeAd(),
//         // ],),
//         Flexible(
//           child: Align(
//             alignment: Alignment(0, 1.0),
//             child: _currentAd,
//           ),
//           fit: FlexFit.tight,
//           flex: 3,
//         )
//       ],
//     );
//   }

//   Widget _getAllButtons() {
//     return GridView.count(
//       shrinkWrap: true,
//       crossAxisCount: 2,
//       childAspectRatio: 3,
//       children: <Widget>[
//         _getRaisedButton(title: "Banner Ad", onPressed: _showBannerAd),
//         _getRaisedButton(title: "Native Ad", onPressed: _showNativeAd),
//         _getRaisedButton(
//             title: "Native Banner Ad", onPressed: _showNativeBannerAd),
//         _getRaisedButton(
//             title: "Intestitial Ad", onPressed: _showInterstitialAd),
//         _getRaisedButton(title: "Rewarded Ad", onPressed: _showRewardedAd),
//       ],
//     );
//   }

//   Widget _getRaisedButton({required String title, void Function()? onPressed}) {
//     return Padding(
//       padding: EdgeInsets.all(8),
//       child: ElevatedButton(
//         onPressed: onPressed,
//         child: Text(
//           title,
//           textAlign: TextAlign.center,
//         ),
//       ),
//     );
//   }

//   _showInterstitialAd() {
//     final interstitialAd = _interstitialAd;

//     if (interstitialAd != null && _isInterstitialAdLoaded == true)
//       interstitialAd.show();
//     else
//       print("Interstial Ad not yet loaded!");
//   }

//   _showRewardedAd() {
//     final rewardedAd = _rewardedAd;

//     if (rewardedAd != null && _isRewardedAdLoaded) {
//       rewardedAd.show();
//     } else {
//       print("Rewarded Ad not yet loaded!");
//     }
//   }

//   _showBannerAd() {
//     setState(() {
//       _currentAd = BannerAd(
//         placementId: BannerAd.testPlacementId,
//         bannerSize: BannerSize.STANDARD,
//         listener: BannerAdListener(
//           onError: (code, message) =>
//               print('banner ad error\ncode: $code\nmessage:$message'),
//           onLoaded: () => print('banner ad loaded'),
//         ),
//       );
//     });
//   }

//   _showNativeBannerAd() {
//     setState(() {
//       _currentAd = _nativeBannerAd();
//     });
//   }

//   Widget _nativeBannerAd() {
//     return NativeAd(
//       placementId: NativeAd.testPlacementId,
//       adType: NativeAdType.NATIVE_BANNER_AD,
//       bannerAdSize: NativeBannerAdSize.HEIGHT_100,
//       width: double.infinity,
//       backgroundColor: Colors.blue,
//       titleColor: Colors.white,
//       descriptionColor: Colors.white,
//       buttonColor: Colors.deepPurple,
//       buttonTitleColor: Colors.white,
//       buttonBorderColor: Colors.white,
//       listener: NativeAdListener(
//         onError: (code, message) =>
//             print('native banner ad error\ncode: $code\nmessage:$message'),
//         onLoaded: () => print('native banner ad loaded'),
//         onMediaDownloaded: () => 'native banner ad media downloaded',
//       ),
//     );
//   }

//   _showNativeAd() {
//     setState(() {
//       _currentAd = _nativeAd();
//     });
//   }

//   Widget _nativeAd() {
//     return NativeAd(
//       placementId: NativeAd.testPlacementId,
//       adType: NativeAdType.NATIVE_AD_VERTICAL,
//       width: double.infinity,
//       height: 300,
//       backgroundColor: Colors.blue,
//       titleColor: Colors.white,
//       descriptionColor: Colors.white,
//       buttonColor: Colors.deepPurple,
//       buttonTitleColor: Colors.white,
//       buttonBorderColor: Colors.white,
//       listener: NativeAdListener(
//         onError: (code, message) =>
//             print('native ad error\ncode: $code\nmessage:$message'),
//         onLoaded: () => print('native ad loaded'),
//         onMediaDownloaded: () => 'native ad media downloaded',
//       ),
//       keepExpandedWhileLoading: true,
//       expandAnimationDuraion: 1000,
//     );
//   }
// }






























import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:background_downloader/background_downloader.dart';
import 'login/login_screen.dart';
import 'home.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await FileDownloader().start();
  await FileDownloader().configureNotification(
    running: TaskNotification('Downloading {filename}', '{progress}'),
    complete: TaskNotification('Download complete', '{filename}'),
    error: TaskNotification('Download failed', '{filename}'),
    progressBar: true,
    tapOpensFile: true,
  );
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});
  Future<bool> _checkAuth() async {
    final prefs = await SharedPreferences.getInstance();
    final String? token = prefs.getString('access_token');
    return token!= null && token.isNotEmpty;
  }
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'LearnScroll App',
      home: FutureBuilder<bool>(
        future: _checkAuth(),
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Scaffold(body: Center(child: CircularProgressIndicator()));
          }
          if (snapshot.hasData && snapshot.data == true) {
            return const HomeScreen();
          } else {
            return const LoginScreen();
          }
        },
      ),
    );
  }
}