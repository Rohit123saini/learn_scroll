// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Hindi (`hi`).
class AppLocalizationsHi extends AppLocalizations {
  AppLocalizationsHi([String locale = 'hi']) : super(locale);

  @override
  String get appTitle => 'LearnScroll';

  @override
  String get inviteEarn => 'आमंत्रित करें और कमाएं';

  @override
  String get inviteEarnSub => 'दोस्तों को आमंत्रित करें और रिवॉर्ड्स पाएं';

  @override
  String inviteEarnSubCoins(int coins) {
    return 'दोस्तों को आमंत्रित करें और $coins कॉइन्स कमाएं';
  }

  @override
  String get inviteCta => 'आमंत्रित करें';

  @override
  String get searchHint => 'कोर्स, टेस्ट, नोटिस खोजें...';

  @override
  String get liveNow => 'अभी लाइव';

  @override
  String get liveBadge => 'लाइव';

  @override
  String liveNowCardSubtitle(String teacher, int viewers) {
    return '$teacher · $viewers देख रहे हैं';
  }

  @override
  String get seeAll => 'सभी देखें';

  @override
  String get join => 'जुड़ें';

  @override
  String get yourFeed => 'आपकी फ़ीड';

  @override
  String get following => 'फॉलोइंग';

  @override
  String get addFriends => 'दोस्त जोड़ें';

  @override
  String get startClass => 'क्लास शुरू करें';

  @override
  String get startTestSeries => 'टेस्ट सीरीज़ शुरू करें';

  @override
  String get assignments => 'असाइनमेंट';

  @override
  String get testSeries => 'टेस्ट सीरीज़';

  @override
  String get notices => 'सूचनाएं';

  @override
  String get wallet => 'वॉलेट';

  @override
  String get yourClassrooms => 'आपकी कक्षाएं';

  @override
  String get joinClassroom => '+ जुड़ें';

  @override
  String get jumpBackIn => 'वापस जुड़ें';

  @override
  String get themeToggleTooltip => 'डार्क मोड बदलें';

  @override
  String get languageToggleTooltip => 'भाषा बदलें';

  @override
  String get notificationsTooltip => 'सूचनाएं';

  @override
  String get newPostTooltip => 'नई पोस्ट बनाएं';

  @override
  String get navHome => 'होम';

  @override
  String get navDiscover => 'खोजें';

  @override
  String get navCreate => 'बनाएं';

  @override
  String get navNotifications => 'सूचनाएं';

  @override
  String get navProfile => 'प्रोफ़ाइल';

  @override
  String get save => 'सहेजें';

  @override
  String get saveFailed => 'बुकमार्क अपडेट करने में विफल';

  @override
  String get like => 'लाइक';

  @override
  String get comment => 'टिप्पणी';

  @override
  String get share => 'शेयर';

  @override
  String commentsCount(int count) {
    return '$count टिप्पणियां';
  }

  @override
  String get sessionExpiredRedirecting =>
      'सत्र समाप्त हो गया। लॉगिन पर अनुप्रेषित किया जा रहा है...';

  @override
  String get feedErrorTitle => 'फ़ीड लोड नहीं हो सकी';

  @override
  String get feedErrorSubtitle =>
      'अपना इंटरनेट कनेक्शन जांचें और पुनः प्रयास करें।';

  @override
  String get retry => 'पुनः प्रयास करें';

  @override
  String get feedEmptyTitle => 'अभी कोई पोस्ट नहीं है';

  @override
  String get feedEmptySubtitle =>
      'अपनी फ़ीड भरने के लिए क्रिएटरों को फ़ॉलो करें या विषय खोजें।';

  @override
  String featureComingSoon(String feature) {
    return '$feature जल्द आ रहा है!';
  }

  @override
  String get authOr => 'या';

  @override
  String get authGoogleCredentialsFailed =>
      'Google क्रेडेंशियल्स नहीं मिल सके। कृपया फिर से कोशिश करें।';

  @override
  String get authGoogleSignedIn => 'Google से साइन इन हो गया';

  @override
  String get authGoogleSignInFailed =>
      'Google साइन-इन विफल रहा। कृपया फिर से कोशिश करें।';

  @override
  String get loginSuccessful => 'लॉगिन सफल!';

  @override
  String get loginWelcomeBack => 'वापसी पर स्वागत है';

  @override
  String get loginSubtitle => 'जारी रखने के लिए साइन इन करें';

  @override
  String get loginUsernameOrEmail => 'यूज़रनेम या ईमेल';

  @override
  String get loginUsernameRequired => 'यूज़रनेम या ईमेल आवश्यक है';

  @override
  String get loginPassword => 'पासवर्ड';

  @override
  String get loginPasswordRequired => 'पासवर्ड आवश्यक है';

  @override
  String get loginForgotPassword => 'पासवर्ड भूल गए?';

  @override
  String get loginSignIn => 'साइन इन करें';

  @override
  String get loginContinueWithGoogle => 'Google से जारी रखें';

  @override
  String get loginNoAccount => 'खाता नहीं है? ';

  @override
  String get loginSignUp => 'साइन अप करें';

  @override
  String get signupSuccessful => 'साइन अप सफल!';

  @override
  String get signupUsernameExists => 'इस यूज़रनेम से यूज़र पहले से मौजूद है';

  @override
  String get signupEmailExists => 'इस ईमेल से खाता पहले से मौजूद है';

  @override
  String get signupGoogleSuccessful => 'Google से खाता बन गया';

  @override
  String get signupVerifyEmail => 'ईमेल पता सत्यापित करें';

  @override
  String get signupOtpSentTo => 'हमने एक सत्यापन कोड इस पते पर भेजा है';

  @override
  String get signupOtpInvalid => 'कृपया एक मान्य OTP दर्ज करें';

  @override
  String get signupVerifyAndCreate => 'सत्यापित करें और खाता बनाएं';

  @override
  String get signupCreateAccount => 'खाता बनाएं';

  @override
  String get signupSubtitle =>
      'LearnScroll के साथ शुरुआत करने के लिए साइन अप करें';

  @override
  String get signupWithGoogle => 'Google से साइन अप करें';

  @override
  String get signupOrEmail => 'या ईमेल से साइन अप करें';

  @override
  String get signupUsername => 'यूज़रनेम';

  @override
  String get signupUsernameRequired => 'यूज़रनेम आवश्यक है';

  @override
  String get signupEmail => 'ईमेल पता';

  @override
  String get signupEmailRequired => 'ईमेल आवश्यक है';

  @override
  String get signupEmailInvalid => 'मान्य ईमेल पता दर्ज करें';

  @override
  String get signupContact => 'संपर्क नंबर';

  @override
  String get signupContactRequired => 'संपर्क नंबर आवश्यक है';

  @override
  String get signupContactInvalid => 'मान्य मोबाइल नंबर दर्ज करें';

  @override
  String get signupFirstName => 'पहला नाम';

  @override
  String get signupLastName => 'अंतिम नाम';

  @override
  String get signupFieldRequired => 'आवश्यक';

  @override
  String get signupPassword => 'पासवर्ड';

  @override
  String get signupPasswordRequired => 'पासवर्ड आवश्यक है';

  @override
  String get signupPasswordMinLength =>
      'पासवर्ड कम से कम 8 अक्षरों का होना चाहिए';

  @override
  String get signupConfirmPassword => 'पासवर्ड की पुष्टि करें';

  @override
  String get signupConfirmPasswordRequired => 'पासवर्ड की पुष्टि आवश्यक है';

  @override
  String get signupPasswordsNoMatch => 'पासवर्ड मेल नहीं खाते';

  @override
  String get signupButton => 'साइन अप करें';

  @override
  String get forgotOtpSentSuccess => 'OTP सफलतापूर्वक भेजा गया!';

  @override
  String get forgotOtpRequired => 'कृपया OTP दर्ज करें';

  @override
  String get forgotPasswordUpdated => 'पासवर्ड सफलतापूर्वक अपडेट हुआ!';

  @override
  String get forgotTitleStep1 => 'पासवर्ड भूल गए?';

  @override
  String get forgotTitleStep2 => 'OTP सत्यापित करें';

  @override
  String get forgotTitleStep3 => 'पासवर्ड रीसेट करें';

  @override
  String get forgotSubtitleStep1 =>
      'सत्यापन OTP पाने के लिए अपना पंजीकृत ईमेल या फ़ोन नंबर दर्ज करें।';

  @override
  String get forgotSubtitleStep2 =>
      'अपने पंजीकृत संपर्क पर भेजा गया 6-अंकीय सत्यापन कोड दर्ज करें।';

  @override
  String get forgotSubtitleStep3 =>
      'रीसेट पूरा करने के लिए अपना नया पासवर्ड दर्ज करें और उसकी पुष्टि करें।';

  @override
  String get forgotIdentityLabel => 'ईमेल या फ़ोन नंबर';

  @override
  String get forgotFieldRequired => 'यह फ़ील्ड आवश्यक है';

  @override
  String get forgotOtpLabel => 'OTP कोड दर्ज करें';

  @override
  String get forgotNewPassword => 'नया पासवर्ड';

  @override
  String get forgotNewPasswordRequired => 'नया पासवर्ड आवश्यक है';

  @override
  String get forgotConfirmNewPassword => 'नए पासवर्ड की पुष्टि करें';

  @override
  String get forgotConfirmPasswordRequired => 'पासवर्ड की पुष्टि आवश्यक है';

  @override
  String get forgotPasswordsNoMatch => 'पासवर्ड मेल नहीं खाते';

  @override
  String get forgotSendOtp => 'OTP भेजें';

  @override
  String get forgotVerifyOtp => 'OTP सत्यापित करें';

  @override
  String get forgotUpdatePassword => 'पासवर्ड अपडेट करें';

  @override
  String get completeProfileTitle => 'आखिरी कदम';

  @override
  String get completeProfileSubtitle =>
      'अपना खाता पूरा करने के लिए कृपया अपना फ़ोन नंबर जोड़ें';

  @override
  String get completeProfilePhone => 'फ़ोन नंबर';

  @override
  String get completeProfilePhoneRequired => 'फ़ोन नंबर आवश्यक है';

  @override
  String get completeProfilePhoneInvalid => 'मान्य मोबाइल नंबर दर्ज करें';

  @override
  String get completeProfileContinue => 'जारी रखें';

  @override
  String get cancel => 'रद्द करें';

  @override
  String get somethingWentWrong => 'कुछ गलत हो गया';

  @override
  String get testSubmit => 'जमा करें';

  @override
  String get testSubmitFailed => 'टेस्ट जमा करने में विफल';

  @override
  String get testExitTitle => 'टेस्ट से बाहर निकलें?';

  @override
  String get testExitBody => 'अभी बाहर निकलने पर आपकी प्रगति खो जाएगी।';

  @override
  String get testExitConfirm => 'बाहर निकलें';

  @override
  String get testPaletteTitle => 'प्रश्न पैलेट';

  @override
  String testAnsweredOf(int answered, int total) {
    return '$answered/$total उत्तर दिए गए';
  }

  @override
  String get testSeriesErrorTitle => 'टेस्ट लोड नहीं हो सका';

  @override
  String get testNoQuestions => 'कोई प्रश्न नहीं मिला';

  @override
  String questionOf(int number, int total) {
    return 'प्रश्न $number में से $total';
  }

  @override
  String questionShort(int number) {
    return 'प्र$number';
  }

  @override
  String marksShort(num marks) {
    return '$marks अंक';
  }

  @override
  String get answerHintSelectOne => 'एक विकल्प चुनें';

  @override
  String get answerHintSelectMultiple => 'लागू होने वाले सभी चुनें';

  @override
  String get answerHintMatch => 'निम्नलिखित का मिलान करें';

  @override
  String get answerHintArrange => 'सही क्रम में व्यवस्थित करें';

  @override
  String get answerHintText => 'अपना उत्तर टाइप करें';

  @override
  String get testTypeAnswerHint => 'यहां अपना उत्तर टाइप करें...';

  @override
  String get testAttachPhoto => 'फोटो जोड़ें';

  @override
  String get testMatchSelect => 'एक मिलान चुनें';

  @override
  String get testPrevious => 'पिछला';

  @override
  String get testNext => 'आगे';

  @override
  String get testRateTitle => 'इस टेस्ट सीरीज़ को रेट करें';

  @override
  String get testRateSubtitle => 'हमें बताएं यह कैसी थी';

  @override
  String get testReviewHint => 'एक रिव्यू लिखें (वैकल्पिक)';

  @override
  String get testSubmitReview => 'रिव्यू जमा करें';

  @override
  String get testReviewThanks => 'आपकी प्रतिक्रिया के लिए धन्यवाद!';

  @override
  String get testAskQueryTitle => 'कोई सवाल है?';

  @override
  String get testAskQuerySubtitle => 'इस टेस्ट सीरीज़ के बारे में पूछें';

  @override
  String get testQueryHint => 'यहां अपना सवाल टाइप करें...';

  @override
  String get testQueryAnonymous => 'गुमनाम रूप से पूछें';

  @override
  String get testSendQuery => 'भेजें';

  @override
  String get testQueryEmpty => 'कृपया अपना सवाल दर्ज करें';

  @override
  String get testQuerySent => 'आपका सवाल भेज दिया गया है';

  @override
  String get testResultTitle => 'टेस्ट परिणाम';

  @override
  String get testFinalScore => 'अंतिम स्कोर';

  @override
  String get testAutoScore => 'ऑटो स्कोर';

  @override
  String get testTotalMarks => 'कुल अंक';

  @override
  String get testPercentage => 'प्रतिशत';

  @override
  String get testAwaitingCheckNote =>
      'कुछ उत्तर मैन्युअल जांच की प्रतीक्षा में हैं';

  @override
  String get testSubmittedOn => 'जमा करने की तिथि';

  @override
  String get testCheckedOn => 'जांच की तिथि';

  @override
  String get testAttemptNumber => 'प्रयास संख्या';

  @override
  String get testRateSeries => 'सीरीज़ को रेट करें';

  @override
  String get testAskQuery => 'सवाल पूछें';

  @override
  String get testBreakdown => 'प्रश्न विवरण';

  @override
  String get testAwaitingReview => 'समीक्षा की प्रतीक्षा में';

  @override
  String get answerCorrect => 'सही';

  @override
  String get answerIncorrect => 'गलत';

  @override
  String get assignmentReviewed => 'समीक्षा हो गई';

  @override
  String assignmentMarksOf(num awarded, num max) {
    return '$awarded/$max अंक';
  }

  @override
  String get testYourAnswer => 'आपका उत्तर';

  @override
  String get answerNotAnswered => 'उत्तर नहीं दिया';

  @override
  String get homeTab => 'होम';

  @override
  String get campusTab => 'कैंपस';

  @override
  String get classesTab => 'कक्षाएं';

  @override
  String get chatTab => 'चैट';

  @override
  String get profileTab => 'प्रोफ़ाइल';

  @override
  String get confirm => 'पुष्टि करें';

  @override
  String openFailed(String error) {
    return 'खोलने में विफल: $error';
  }

  @override
  String get assignmentsTabPending => 'बाकी';

  @override
  String get assignmentsTabSubmitted => 'जमा किए गए';

  @override
  String get assignmentsTabChecked => 'जांचे गए';

  @override
  String get assignmentsErrorTitle => 'असाइनमेंट लोड नहीं हो सके';

  @override
  String get assignmentsEmptyTitle => 'अभी कोई असाइनमेंट नहीं है';

  @override
  String get assignmentsEmptySubtitle =>
      'आपकी कक्षाओं के असाइनमेंट यहां दिखेंगे।';

  @override
  String assignmentPostedBy(String postedBy) {
    return '$postedBy द्वारा पोस्ट किया गया';
  }

  @override
  String assignmentGradeValue(String grade) {
    return 'ग्रेड: $grade';
  }

  @override
  String assignmentTotalMarks(num totalMarks) {
    return 'कुल अंक: $totalMarks';
  }

  @override
  String get assignmentNoDueDate => 'कोई नियत तिथि नहीं';

  @override
  String assignmentDueOn(String date) {
    return '$date तक जमा करें';
  }

  @override
  String get assignmentOverdue => 'समय सीमा समाप्त';

  @override
  String get assignmentDueToday => 'आज देय';

  @override
  String get assignmentDueTomorrow => 'कल देय';

  @override
  String assignmentDaysLeft(int days) {
    return '$days दिन शेष';
  }

  @override
  String get assignmentStatusChecked => 'जांचा गया';

  @override
  String get assignmentStatusPartiallyChecked => 'आंशिक रूप से जांचा गया';

  @override
  String get assignmentStatusSubmitted => 'जमा किया गया';

  @override
  String get assignmentStatusLate => 'देर से';

  @override
  String get assignmentStatusMissing => 'अनुपलब्ध';

  @override
  String get assignmentNothingToSubmit => 'जमा करने के लिए कुछ नहीं है';

  @override
  String get assignmentSubmitConfirmTitle => 'असाइनमेंट जमा करें?';

  @override
  String assignmentSubmitConfirmUnanswered(int unanswered) {
    return '$unanswered प्रश्नों के उत्तर नहीं दिए गए हैं। फिर भी जमा करें?';
  }

  @override
  String get assignmentSubmitConfirmBody =>
      'क्या आप वाकई इस असाइनमेंट को जमा करना चाहते हैं?';

  @override
  String get assignmentSubmitSuccess => 'असाइनमेंट सफलतापूर्वक जमा हो गया';

  @override
  String get assignmentSubmitFailed => 'असाइनमेंट जमा करने में विफल';

  @override
  String get assignmentDueLabel => 'देय तिथि';

  @override
  String get assignmentTotalMarksLabel => 'कुल अंक';

  @override
  String get assignmentQuestionsLabel => 'प्रश्न';

  @override
  String get assignmentOpenAttachment => 'अनुलग्नक खोलें';

  @override
  String get assignmentLateNotice => 'यह असाइनमेंट देर से जमा किया गया था';

  @override
  String get assignmentYourAnswer => 'आपका उत्तर';

  @override
  String get assignmentAnswerHint => 'यहां अपना उत्तर टाइप करें...';

  @override
  String get assignmentNoQuestions => 'कोई प्रश्न नहीं मिला';

  @override
  String get assignmentAttachFile => 'फ़ाइल जोड़ें';

  @override
  String get assignmentRemoveFile => 'फ़ाइल हटाएं';

  @override
  String assignmentAnsweredOf(int answered, int total) {
    return '$answered/$total उत्तर दिए गए';
  }

  @override
  String get assignmentSubmit => 'जमा करें';

  @override
  String get assignmentResult => 'परिणाम';

  @override
  String get assignmentMarksAwarded => 'दिए गए अंक';

  @override
  String get assignmentGrade => 'ग्रेड';

  @override
  String get assignmentSubmittedOn => 'जमा करने की तिथि';

  @override
  String get assignmentCheckedOn => 'जांच की तिथि';

  @override
  String get assignmentPartiallyCheckedNote =>
      'कुछ उत्तर मैन्युअल जांच की प्रतीक्षा में हैं';

  @override
  String get assignmentTeacherFeedback => 'शिक्षक की प्रतिक्रिया';

  @override
  String get assignmentNoWrittenAnswer => 'कोई लिखित उत्तर नहीं दिया गया';

  @override
  String get assignmentOpenSubmittedFile => 'जमा की गई फ़ाइल खोलें';

  @override
  String get assignmentAwaitingReview => 'समीक्षा की प्रतीक्षा में';

  @override
  String get assignmentAttachPhoto => 'फोटो जोड़ें';

  @override
  String get testSeriesTabAll => 'सभी';

  @override
  String get testSeriesTabFree => 'मुफ़्त';

  @override
  String get testSeriesTabPaid => 'सशुल्क';

  @override
  String get testSeriesEmptyTitle => 'अभी कोई टेस्ट सीरीज़ नहीं है';

  @override
  String get testSeriesEmptySubtitle =>
      'आपकी कक्षाओं की टेस्ट सीरीज़ यहां दिखेंगी।';

  @override
  String testSeriesCoins(int coins) {
    return '$coins कॉइन्स';
  }

  @override
  String get testSeriesFree => 'मुफ़्त';

  @override
  String testSeriesBy(String creator) {
    return '$creator द्वारा';
  }

  @override
  String testSeriesMarks(num marks) {
    return '$marks अंक';
  }

  @override
  String testSeriesDuration(int minutes) {
    return '$minutes मिनट';
  }

  @override
  String testSeriesAttempts(int attempts) {
    return '$attempts प्रयास';
  }

  @override
  String get testSeriesStart => 'शुरू करें';

  @override
  String get testSeriesResume => 'फिर से शुरू करें';

  @override
  String get testSeriesViewResult => 'परिणाम देखें';

  @override
  String get testSeriesNoRatings => 'अभी कोई रेटिंग नहीं';

  @override
  String testSeriesRating(String rating, int reviewCount) {
    return '$rating ($reviewCount रिव्यू)';
  }

  @override
  String get testStatusChecked => 'जांचा गया';

  @override
  String get testStatusPartiallyChecked => 'आंशिक रूप से जांचा गया';

  @override
  String get testStatusSubmitted => 'जमा किया गया';

  @override
  String get testStatusInProgress => 'जारी है';

  @override
  String get testSeriesPayTitle => 'टेस्ट सीरीज़ अनलॉक करें';

  @override
  String testSeriesPayBody(int coins) {
    return 'इस टेस्ट सीरीज़ की कीमत $coins कॉइन्स है। जारी रखें?';
  }

  @override
  String get testSeriesNotEnoughCoins =>
      'इस टेस्ट सीरीज़ के लिए आपके पास पर्याप्त कॉइन्स नहीं हैं';

  @override
  String get testSeriesQuestionsLabel => 'प्रश्न';

  @override
  String get testSeriesDurationLabel => 'अवधि';

  @override
  String get testSeriesNoTimeLimit => 'कोई समय सीमा नहीं';

  @override
  String get testSeriesAttemptsLabel => 'प्रयास';

  @override
  String get testSeriesYourAttempt => 'आपका प्रयास';

  @override
  String testSeriesTimerNotice(int minutes) {
    return 'इस टेस्ट की समय सीमा $minutes मिनट है';
  }

  @override
  String get testSeriesReviews => 'रिव्यू';

  @override
  String get testSeriesNoReviews => 'अभी कोई रिव्यू नहीं';

  @override
  String get testTimeUp => 'समय समाप्त!';

  @override
  String get testSubmitConfirmTitle => 'टेस्ट जमा करें?';

  @override
  String testSubmitConfirmUnanswered(int unanswered) {
    return '$unanswered प्रश्नों के उत्तर नहीं दिए गए हैं। फिर भी जमा करें?';
  }

  @override
  String get testSubmitConfirmBody =>
      'क्या आप वाकई इस टेस्ट को जमा करना चाहते हैं?';

  @override
  String get viewAll => 'सभी देखें';

  @override
  String get settingsTitle => 'सेटिंग्स';

  @override
  String get settingsLanguageSection => 'भाषा';

  @override
  String get settingsThemeSection => 'दिखावट';

  @override
  String get settingsThemeLight => 'लाइट';

  @override
  String get settingsThemeDark => 'डार्क';

  @override
  String get settingsThemeSystem => 'डिवाइस जैसा';

  @override
  String get settingsAccountSection => 'अकाउंट';

  @override
  String get settingsLogout => 'लॉग आउट';

  @override
  String get settingsLogoutConfirm =>
      'ऐप इस्तेमाल करने के लिए दोबारा साइन इन करना होगा।';

  @override
  String get roleAdmin => 'कैंपस एडमिन';

  @override
  String get rolePrincipalHod => 'प्रिंसिपल / HOD';

  @override
  String get roleClassTeacher => 'क्लास टीचर';

  @override
  String get roleSubjectTeacher => 'सब्जेक्ट टीचर';

  @override
  String get roleNonTeaching => 'ऑफिस स्टाफ';

  @override
  String get roleStudent => 'स्टूडेंट';

  @override
  String get roleParent => 'अभिभावक';

  @override
  String get roleManagement => 'मैनेजमेंट';

  @override
  String get roleNone => 'सदस्य';

  @override
  String get campusSwitchTooltip => 'कैंपस बदलें';

  @override
  String get campusLoadFailed => 'कैंपस लोड नहीं हो पाया';

  @override
  String get campusNoneTitle => 'आप अभी किसी कैंपस में नहीं हैं';

  @override
  String get campusNoneSubtitle =>
      'अपने कॉलेज या स्कूल से जुड़ने को कहें, या अपना कैंपस बनाएं।';

  @override
  String get campusPendingTitle => 'अप्रूवल का इंतज़ार';

  @override
  String get campusPendingBody =>
      'यह कैंपस अभी समीक्षा में है। अप्रूव होने के बाद आप स्टाफ और स्टूडेंट जोड़ पाएंगे।';

  @override
  String get campusRejectedTitle => 'कैंपस अप्रूव नहीं हुआ';

  @override
  String get campusRejectedBody =>
      'यह कैंपस अप्रूव नहीं हुआ। अगर आपको लगता है कि यह गलती है तो सपोर्ट से बात करें।';

  @override
  String get campusMyClassTitle => 'मेरी क्लास';

  @override
  String get campusMySectionsTitle => 'मेरे सेक्शन';

  @override
  String get campusAllSectionsTitle => 'सभी सेक्शन';

  @override
  String get campusManagementTitle => 'कैंपस ओवरव्यू';

  @override
  String get campusNoSectionsAssigned =>
      'आपको अभी कोई सेक्शन नहीं मिला है। कैंपस एडमिन यह सेट करता है।';

  @override
  String get campusSectionsCount => 'सेक्शन';

  @override
  String get campusSubjectsCount => 'विषय';

  @override
  String get campusAttendanceLabel => 'हाज़िरी';

  @override
  String get campusTapToView => 'देखने के लिए टैप करें';

  @override
  String get campusMarkAttendance => 'हाज़िरी लगाएं';

  @override
  String get campusStudents => 'स्टूडेंट';

  @override
  String get campusTimetableTitle => 'टाइम टेबल';

  @override
  String get campusTimetableLoadFailed => 'टाइम टेबल लोड नहीं हो पाया';

  @override
  String get campusNoClassesToday => 'इस दिन कोई पीरियड नहीं है';

  @override
  String get campusUnknownSubject => 'विषय';

  @override
  String get campusSearchStudents => 'नाम या रोल नंबर से खोजें';

  @override
  String get campusNoMatches => 'इस खोज से कोई स्टूडेंट नहीं मिला';

  @override
  String campusStudentCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count स्टूडेंट',
      one: '1 स्टूडेंट',
    );
    return '$_temp0';
  }

  @override
  String get campusRosterLoadFailed => 'स्टूडेंट लिस्ट लोड नहीं हो पाई';

  @override
  String get campusRosterEmptyTitle => 'इस सेक्शन में कोई स्टूडेंट नहीं';

  @override
  String get campusRosterEmptySubtitle =>
      'मौजूदा सेशन में एनरोल होने के बाद स्टूडेंट यहां दिखेंगे।';

  @override
  String get campusNoAttendancePermissionTitle => 'आप यह हाज़िरी नहीं लगा सकते';

  @override
  String get campusNoAttendancePermissionBody =>
      'इस सेक्शन की हाज़िरी सिर्फ़ क्लास टीचर या उस विषय का असाइन किया गया टीचर लगा सकता है।';

  @override
  String get campusNoticesTitle => 'सूचनाएं';

  @override
  String get campusNoticesLoadFailed => 'सूचनाएं लोड नहीं हो पाईं';

  @override
  String get campusNoNotices => 'अभी कोई सूचना नहीं';

  @override
  String get campusPostNotice => 'सूचना डालें';

  @override
  String get noticeAudienceLabel => 'किसे दिखेगी';

  @override
  String get noticeTitleLabel => 'शीर्षक';

  @override
  String get noticeBodyLabel => 'संदेश';

  @override
  String get noticePinLabel => 'सबसे ऊपर पिन करें';

  @override
  String get noticePinHint => '7 दिन तक पिन रहेगी';

  @override
  String get noticeSendLabel => 'सूचना डालें';

  @override
  String get noticeEmptyError => 'डालने से पहले शीर्षक और संदेश भरें।';

  @override
  String get noticeNoSessionError => 'इस कैंपस का कोई चालू सेशन नहीं है।';

  @override
  String get noticeScopeWholeCampus => 'पूरा कैंपस';

  @override
  String get noticeScopeCampus => 'कैंपस';

  @override
  String get noticeScopeClass => 'क्लास';

  @override
  String get noticeScopeDepartment => 'विभाग';

  @override
  String get noticeScopeSection => 'सेक्शन';

  @override
  String get attendanceWholeDay => 'पूरा दिन';

  @override
  String get attendanceSubjectLabel => 'विषय';

  @override
  String get attendancePresent => 'उपस्थित';

  @override
  String get attendanceAbsent => 'अनुपस्थित';

  @override
  String get attendanceTotal => 'कुल';

  @override
  String get attendanceOverall => 'कुल हाज़िरी';

  @override
  String get attendanceBySubject => 'विषय के हिसाब से';

  @override
  String get attendanceNoSubjectData => 'अभी विषयवार रिकॉर्ड नहीं है';

  @override
  String get attendanceSummaryFailed => 'हाज़िरी लोड नहीं हो पाई';

  @override
  String get attendanceAlreadyMarked =>
      'इस दिन की हाज़िरी पहले ही लग चुकी है। बदलाव के लिए दूसरी तारीख चुनें।';

  @override
  String attendanceSaveLabel(int count) {
    return '$count स्टूडेंट की हाज़िरी सेव करें';
  }

  @override
  String attendanceSaving(int done, int total) {
    return '$total में से $done सेव हो रहे हैं';
  }

  @override
  String attendanceSavedAll(int count) {
    return '$count स्टूडेंट की हाज़िरी सेव हो गई';
  }

  @override
  String attendanceSavedPartial(int saved, int failed) {
    return '$saved सेव हुए, $failed नहीं हो पाए';
  }

  @override
  String attendanceFailedCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count स्टूडेंट सेव नहीं हुए',
      one: '1 स्टूडेंट सेव नहीं हुआ',
    );
    return '$_temp0';
  }

  @override
  String attendanceBelowThreshold(int percent) {
    return 'हाज़िरी आपके कैंपस की $percent% की ज़रूरत से कम है।';
  }

  @override
  String timeMinutesAgo(int count) {
    return '$count मिनट पहले';
  }

  @override
  String timeHoursAgo(int count) {
    return '$count घंटे पहले';
  }

  @override
  String timeDaysAgo(int count) {
    return '$count दिन पहले';
  }

  @override
  String get searchFilterAll => 'सभी';

  @override
  String get searchFilterPeople => 'लोग';

  @override
  String get searchFilterNotices => 'सूचनाएं';

  @override
  String get searchFilterAssignments => 'असाइनमेंट';

  @override
  String get searchFilterTests => 'टेस्ट सीरीज़';

  @override
  String get searchFilterMessages => 'मैसेज';

  @override
  String get searchSectionPeople => 'लोग';

  @override
  String get searchSectionNotices => 'कैंपस सूचनाएं';

  @override
  String get searchSectionAssignments => 'असाइनमेंट';

  @override
  String get searchSectionTests => 'टेस्ट सीरीज़';

  @override
  String get searchSectionMessages => 'मैसेज';

  @override
  String get searchRecentTitle => 'हाल ही में';

  @override
  String get searchRecentClear => 'सभी हटाएं';

  @override
  String get searchEmptyPrompt =>
      'लोगों, क्लासेस, सूचनाओं, असाइनमेंट और टेस्ट के लिए खोजें';

  @override
  String searchNoResultsFor(String query) {
    return '\"$query\" के लिए कोई परिणाम नहीं मिला';
  }

  @override
  String get searchDetailUnavailable =>
      'इस रिज़ल्ट का पूरा डिटेल व्यू अभी वायर नहीं हुआ है';

  @override
  String get couldNotOpenClassroom => 'यह क्लासरूम नहीं खुल सका।';

  @override
  String get couldNotOpenClass => 'यह क्लास नहीं खुल सकी।';

  @override
  String get camera => 'कैमरा';

  @override
  String get recordVideo => 'वीडियो रिकॉर्ड करें';

  @override
  String get chooseFromGallery => 'गैलरी से चुनें';

  @override
  String get yourStory => 'आपकी स्टोरी';

  @override
  String get couldNotUploadStory => 'आपकी स्टोरी अपलोड नहीं हो सकी।';

  @override
  String get noClassroomsWithReferrals =>
      'आपके किसी भी क्लासरूम में रेफरल अभी चालू नहीं है।';

  @override
  String get couldNotCreateReferralLink => 'आपका रेफरल लिंक नहीं बन सका।';

  @override
  String get open => 'खोलें';

  @override
  String inviteEarnedSoFar(int earned, int pending) {
    return 'अब तक आपने ₹$earned कमाए हैं — ₹$pending आने वाले हैं';
  }

  @override
  String get inviteShareClassroomLink =>
      'किसी क्लासरूम का लिंक शेयर करें — जब तक वे एनरोल रहें, हर दिन % कमीशन कमाएं';

  @override
  String referralCommissionEarned(String percent, String name) {
    return 'आप \"$name\" में रेफर किए हर छात्र से रोज़ाना फीस का $percent% कमाएंगे।';
  }

  @override
  String failedWithError(String error) {
    return 'विफल: $error';
  }

  @override
  String filesSelectedCount(int count) {
    return '$count चुनी गईं';
  }
}
