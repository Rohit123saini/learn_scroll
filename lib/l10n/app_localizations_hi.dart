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
  String get assignmentPickFromCamera => 'फ़ोटो खींचें';

  @override
  String get assignmentPickFromGallery => 'गैलरी से चुनें';

  @override
  String get assignmentPickFailed =>
      'फ़ाइल चुनी नहीं जा सकी, कृपया फिर से कोशिश करें।';

  @override
  String get assignmentFileNotFound => 'यह फ़ाइल अब उपलब्ध नहीं है।';

  @override
  String get assignmentNoInternet =>
      'अपना इंटरनेट कनेक्शन जांचें और फिर से कोशिश करें।';

  @override
  String get assignmentCreateTitle => 'असाइनमेंट बनाएं';

  @override
  String get assignmentTitleLabel => 'शीर्षक';

  @override
  String get assignmentDescriptionLabel => 'विवरण';

  @override
  String get assignmentDescriptionHint =>
      'विद्यार्थियों को क्या करना है? यहाँ निर्देश लिखें…';

  @override
  String get assignmentPickDueDate => 'जमा करने की तारीख चुनें';

  @override
  String get assignmentClearDueDate => 'हटाएं';

  @override
  String get assignmentStructuredToggleLabel => 'संरचित प्रश्न';

  @override
  String get assignmentStructuredToggleHint =>
      'एक लिखित उत्तर की जगह ऑटो-ग्रेडेड बहुविकल्पीय या क्रम-आधारित प्रश्नों के लिए इसे चालू करें।';

  @override
  String get assignmentTotalMarksHint =>
      'वैकल्पिक — कोई निश्चित स्केल न होने पर खाली छोड़ें';

  @override
  String get assignmentAddQuestion => 'प्रश्न जोड़ें';

  @override
  String get assignmentQuestionTypeLabel => 'प्रश्न का प्रकार';

  @override
  String get assignmentQuestionTypeText => 'लिखित उत्तर';

  @override
  String get assignmentQuestionTypeMcq => 'बहुविकल्पीय (एक उत्तर)';

  @override
  String get assignmentQuestionTypeMsq => 'बहुविकल्पीय (कई उत्तर)';

  @override
  String get assignmentQuestionTypeList => 'क्रमबद्ध सूची';

  @override
  String get assignmentQuestionTextHint => 'अपना प्रश्न लिखें…';

  @override
  String get assignmentQuestionMarksLabel => 'अंक';

  @override
  String get assignmentAddOption => 'विकल्प जोड़ें';

  @override
  String get assignmentOptionHint => 'विकल्प का टेक्स्ट';

  @override
  String get assignmentRemoveOption => 'विकल्प हटाएं';

  @override
  String get assignmentRemoveQuestion => 'प्रश्न हटाएं';

  @override
  String get assignmentCreateValidationTitle => 'कृपया एक शीर्षक दर्ज करें';

  @override
  String get assignmentCreateValidationQuestion =>
      'हर प्रश्न में प्रश्न-पाठ, अंक, और (यदि लागू हो) एक सही उत्तर चिह्नित होना चाहिए';

  @override
  String get assignmentCreateValidationOptions =>
      'इस प्रश्न में कम से कम 2 विकल्प जोड़ें';

  @override
  String get assignmentCreateValidationCorrect =>
      'इस प्रश्न के लिए कम से कम एक सही विकल्प चिह्नित करें';

  @override
  String get assignmentCreateSuccess => 'असाइनमेंट बन गया';

  @override
  String get assignmentCreateFailed =>
      'असाइनमेंट नहीं बन सका, कृपया फिर से कोशिश करें।';

  @override
  String get assignmentGradeSectionTitle => 'इस सबमिशन को ग्रेड करें';

  @override
  String get assignmentFeedbackHint => 'प्रतिक्रिया (वैकल्पिक)';

  @override
  String get assignmentGradeSaved => 'ग्रेड सेव हो गया';

  @override
  String get assignmentGradeFailed =>
      'ग्रेड सेव नहीं हो सका, कृपया फिर से कोशिश करें।';

  @override
  String get assignmentReviewAnswerTitle => 'इस उत्तर की समीक्षा करें';

  @override
  String get assignmentReviewSaved => 'समीक्षा सेव हो गई';

  @override
  String get assignmentReviewFailed =>
      'समीक्षा सेव नहीं हो सकी, कृपया फिर से कोशिश करें।';

  @override
  String get assignmentNotShared =>
      'साझा नहीं किया गया — सिर्फ आप ये परिणाम देख सकते हैं';

  @override
  String get assignmentSharePublicNote =>
      'लिंक रखने वाला कोई भी यह परिणाम देख सकता है';

  @override
  String get assignmentShareResult => 'शेयर करें';

  @override
  String get assignmentUnshareResult => 'शेयर करना बंद करें';

  @override
  String get assignmentLinkCopied =>
      'लिंक कॉपी हो गया — इसे रखने वाला कोई भी आपका परिणाम देख सकता है';

  @override
  String get assignmentPublishFailed =>
      'यह परिणाम शेयर नहीं हो सका, कृपया फिर से कोशिश करें।';

  @override
  String get assignmentUnpublishFailed =>
      'शेयर करना बंद नहीं हो सका, कृपया फिर से कोशिश करें।';

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
  String get noticeSelectDepartmentError =>
      'यह नोटिस किस विभाग के लिए है, चुनें।';

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
  String openFailed(String error) {
    return 'खोलने में विफल: $error';
  }

  @override
  String failedWithError(String error) {
    return 'विफल: $error';
  }

  @override
  String filesSelectedCount(int count) {
    return '$count चुनी गईं';
  }

  @override
  String get noCommentsYet => 'अभी तक कोई टिप्पणी नहीं';

  @override
  String get beFirstToComment => 'सबसे पहले अपनी राय साझा करें';

  @override
  String get addCommentHint => 'टिप्पणी लिखें…';

  @override
  String replyingTo(String name) {
    return '@$name को जवाब दे रहे हैं';
  }

  @override
  String get like => 'पसंद';

  @override
  String get reply => 'जवाब दें';

  @override
  String viewReplies(int count) {
    return '$count जवाब';
  }

  @override
  String get hideReplies => 'जवाब छुपाएं';

  @override
  String get loadingReplies => 'लोड हो रहा है…';

  @override
  String get edited => 'संपादित';

  @override
  String get you => 'आप';

  @override
  String get editComment => 'टिप्पणी संपादित करें';

  @override
  String get commentEdited => 'टिप्पणी अपडेट हुई';

  @override
  String editFailed(String error) {
    return 'संपादित नहीं हो सकी: $error';
  }

  @override
  String get deleteComment => 'टिप्पणी हटाएं?';

  @override
  String get deleteCommentBody => 'यह वापस नहीं ली जा सकती।';

  @override
  String get delete => 'हटाएं';

  @override
  String deleteFailed(String error) {
    return 'हटाई नहीं जा सकी: $error';
  }

  @override
  String get hideComment => 'टिप्पणी छुपाएं';

  @override
  String get commentHidden => 'टिप्पणी छुपाई गई';

  @override
  String hideFailed(String error) {
    return 'छुपाई नहीं जा सकी: $error';
  }

  @override
  String get attachCameraPhoto => 'कैमरा फोटो';

  @override
  String get attachCameraVideo => 'कैमरा वीडियो';

  @override
  String get attachGalleryPhoto => 'गैलरी फोटो';

  @override
  String get attachGalleryVideo => 'गैलरी वीडियो';

  @override
  String get attachDocument => 'दस्तावेज़';

  @override
  String get fileTooLargeTitle => 'फ़ाइल बहुत बड़ी है';

  @override
  String fileTooLargeBody(String size) {
    return 'यह फ़ाइल ${size}MB की है — सीमा 200MB है। कृपया छोटी फ़ाइल चुनें।';
  }

  @override
  String uploadingPercent(String percent) {
    return '$percent% अपलोड हो रहा है';
  }

  @override
  String get compressing => 'कंप्रेस हो रहा है…';

  @override
  String openFileFailed(String error) {
    return 'फ़ाइल नहीं खुली: $error';
  }

  @override
  String get ok => 'ठीक है';

  @override
  String get addCaptionHint => 'कैप्शन लिखें…';

  @override
  String get download => 'डाउनलोड';

  @override
  String downloadedFile(String fileName) {
    return 'डाउनलोड हुई: $fileName';
  }

  @override
  String downloadFailed(String error) {
    return 'डाउनलोड विफल: $error';
  }

  @override
  String get reactionUpdateFailed =>
      'प्रतिक्रिया अपडेट नहीं हो सकी — अपना कनेक्शन जांचें';

  @override
  String get loadingDocument => 'दस्तावेज़ लोड हो रहा है…';

  @override
  String get previewNotSupported =>
      'इस फ़ाइल के लिए पूर्वावलोकन उपलब्ध नहीं है';

  @override
  String get directDownloadOpen => 'सीधे डाउनलोड करें और खोलें';

  @override
  String get openDownloadedFile => 'डाउनलोड की गई फ़ाइल खोलें';

  @override
  String viewersCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count दर्शक',
      one: '1 दर्शक',
    );
    return '$_temp0';
  }

  @override
  String get viewersTitle => 'दर्शक';

  @override
  String get couldntLoadViewers => 'दर्शक लोड नहीं हो सके।';

  @override
  String get noViewsYet => 'अभी तक कोई नहीं देखा।';

  @override
  String get tsSourceIndividual => 'व्यक्तिगत';

  @override
  String get tsSourceCampus => 'कैंपस';

  @override
  String get tsSourceLiveClass => 'लाइव क्लास';

  @override
  String get tsSearchHint => 'टेस्ट सीरीज़ खोजें';

  @override
  String get tsNoSearchResults => 'इस खोज से कोई टेस्ट सीरीज़ नहीं मिली';

  @override
  String get tsLoadMoreFailed =>
      'और लोड नहीं हो सके। पुनः प्रयास के लिए टैप करें।';

  @override
  String get tsErrOffline =>
      'इंटरनेट कनेक्शन नहीं है। अपना नेटवर्क जांचें और पुनः प्रयास करें।';

  @override
  String get tsErrTimeout =>
      'सर्वर से जवाब आने में बहुत देर हुई। कृपया पुनः प्रयास करें।';

  @override
  String get tsErrUnauthorized =>
      'आपका सत्र समाप्त हो गया है। कृपया दोबारा लॉगिन करें।';

  @override
  String get tsErrForbidden => 'आपके पास इस टेस्ट की पहुंच नहीं है।';

  @override
  String get tsErrNotFound => 'यह टेस्ट अब उपलब्ध नहीं है।';

  @override
  String get tsErrRateLimited =>
      'बहुत ज़्यादा अनुरोध हो गए। कृपया थोड़ी देर रुककर पुनः प्रयास करें।';

  @override
  String get tsErrServer =>
      'हमारी ओर से कुछ गड़बड़ हो गई। कृपया थोड़ी देर में पुनः प्रयास करें।';

  @override
  String get tsAttemptAgain => 'फिर से प्रयास करें';

  @override
  String get tsUnlimited => 'असीमित';

  @override
  String tsAttemptsUsedOf(int used, int allowed) {
    return '$allowed में से $used प्रयास इस्तेमाल हुए';
  }

  @override
  String get tsAttemptHistory => 'आपके प्रयास';

  @override
  String tsAttemptRow(int n) {
    return 'प्रयास $n';
  }

  @override
  String get tsSeriesArchived =>
      'यह टेस्ट सीरीज़ आर्काइव हो चुकी है और शुरू नहीं की जा सकती।';

  @override
  String tsViewAllReviews(int count) {
    return 'सभी रिव्यू देखें ($count)';
  }

  @override
  String get tsReviewsTitle => 'रिव्यू';

  @override
  String get tsPendingSynced => 'आपका लंबित टेस्ट जमा हो गया।';

  @override
  String get tsQuestionUnsupported =>
      'यह प्रश्न प्रकार आपके ऐप वर्ज़न में उपलब्ध नहीं है। कृपया ऐप अपडेट करें।';

  @override
  String get tsQuestionImage => 'प्रश्न की तस्वीर';

  @override
  String get tsMarkForReview => 'समीक्षा के लिए चिह्नित करें';

  @override
  String get tsUnmarkReview => 'समीक्षा चिह्न हटाएं';

  @override
  String get tsLegendAnswered => 'उत्तर दिया';

  @override
  String get tsLegendNotAnswered => 'उत्तर नहीं दिया';

  @override
  String get tsLegendMarked => 'चिह्नित';

  @override
  String get tsOrderNotArranged =>
      'अभी क्रम नहीं लगाया — क्रम लगाने के लिए खींचें';

  @override
  String tsSubmitConfirmMarked(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count प्रश्न समीक्षा के लिए चिह्नित हैं।',
      one: '1 प्रश्न समीक्षा के लिए चिह्नित है।',
    );
    return '$_temp0';
  }

  @override
  String tsTimeLeftWarning(int minutes) {
    String _temp0 = intl.Intl.pluralLogic(
      minutes,
      locale: localeName,
      other: '$minutes मिनट बचे हैं',
      one: '1 मिनट बचा है',
    );
    return '$_temp0';
  }

  @override
  String get tsTimeUpLocked => 'समय समाप्त। आपके उत्तर जमा किए जा रहे हैं…';

  @override
  String get tsSubmitPendingTitle => 'अभी जमा नहीं हो सका';

  @override
  String get tsSubmitPendingBody =>
      'आपके उत्तर इस डिवाइस पर सुरक्षित हैं। कनेक्शन जांचें और पुनः प्रयास करें।';

  @override
  String get tsRetrySubmit => 'पुनः प्रयास करें';

  @override
  String get tsExitBody =>
      'आपके उत्तर सुरक्षित हैं। आप यह टेस्ट बाद में जारी रख सकते हैं।';

  @override
  String get tsExitBodyTimerRunning =>
      'आपके उत्तर सुरक्षित हैं, लेकिन टाइमर चलता रहेगा। आप टेस्ट पेज से जारी रख सकते हैं।';

  @override
  String get tsDraftRestored => 'आपके पिछले उत्तर वापस आ गए हैं।';

  @override
  String tsSemanticsTimer(String time) {
    return 'बचा हुआ समय $time';
  }

  @override
  String get tsFromCamera => 'कैमरा';

  @override
  String get tsFromGallery => 'गैलरी';

  @override
  String get tsRemovePhoto => 'फोटो हटाएं';

  @override
  String tsPhotoTooLarge(int mb) {
    return 'फोटो $mb MB से बड़ी है। कृपया छोटी फोटो चुनें।';
  }

  @override
  String get tsPhotoError =>
      'कैमरा या गैलरी नहीं खुल सकी। सेटिंग्स में ऐप की अनुमतियां जांचें।';

  @override
  String get tsPhotoMissing =>
      'जोड़ी गई फोटो अब उपलब्ध नहीं है। कृपया उसे दोबारा जोड़ें।';

  @override
  String get tsCorrectAnswer => 'सही उत्तर';

  @override
  String get tsYourPhoto => 'आपकी जोड़ी गई फोटो';

  @override
  String tsSummaryCorrect(int count) {
    return 'सही: $count';
  }

  @override
  String tsSummaryIncorrect(int count) {
    return 'गलत: $count';
  }

  @override
  String tsSummarySkipped(int count) {
    return 'छोड़े गए: $count';
  }

  @override
  String tsSummaryAwaiting(int count) {
    return 'समीक्षा बाकी: $count';
  }

  @override
  String get languageSectionTitle => 'भाषा';

  @override
  String get themeSectionTitle => 'थीम';

  @override
  String get notificationSettingsTitle => 'नोटिफिकेशन सेटिंग्स';

  @override
  String get notificationSettingsLoadFailed =>
      'नोटिफिकेशन सेटिंग्स लोड नहीं हो पाईं';

  @override
  String get notifChannelsSectionTitle => 'चैनल';

  @override
  String get notifChannelPush => 'पुश नोटिफिकेशन';

  @override
  String get notifChannelEmail => 'ईमेल';

  @override
  String get notifChannelSms => 'एसएमएस';

  @override
  String get notifChannelWhatsapp => 'व्हाट्सऐप';

  @override
  String get notifDigestSectionTitle => 'ईमेल डाइजेस्ट';

  @override
  String get notifDigestOff => 'बंद';

  @override
  String get notifDigestDaily => 'रोज़ाना';

  @override
  String get notifDigestWeekly => 'साप्ताहिक';

  @override
  String get notifCategoriesSectionTitle => 'इनके बारे में सूचित करें';

  @override
  String get notifCategoriesHint =>
      'कैटेगरी बंद करने पर भी वह आपकी नोटिफिकेशन लिस्ट में दिखेगी — बस पुश, ईमेल, एसएमएस या व्हाट्सऐप अलर्ट नहीं भेजा जाएगा।';

  @override
  String get notifCategoryLiveClasses => 'लाइव क्लासेस और सेशन';

  @override
  String get notifCategoryAssignmentsTests => 'असाइनमेंट और टेस्ट';

  @override
  String get notifCategoryMessagesCalls => 'मैसेज और कॉल';

  @override
  String get notifCategorySocial => 'पोस्ट और रिव्यू';

  @override
  String get notifCategoryPayments => 'भुगतान और वॉलेट';

  @override
  String get notifCategoryCampus => 'कैंपस नोटिस';

  @override
  String get languageEnglish => 'अंग्रेज़ी';

  @override
  String get languageHindi => 'हिन्दी';

  @override
  String get languageSystemDefault => 'सिस्टम डिफ़ॉल्ट';

  @override
  String get themeLight => 'लाइट';

  @override
  String get themeDark => 'डार्क';

  @override
  String get themeSystem => 'सिस्टम डिफ़ॉल्ट';

  @override
  String get liveClassesTitle => 'लाइव क्लासेस';

  @override
  String get searchClassroomsHint => 'क्लासरूम खोजें';

  @override
  String get filterAll => 'सभी';

  @override
  String get filterMine => 'मेरे';

  @override
  String get couldNotLoadClassrooms => 'क्लासरूम लोड नहीं हो पाए';

  @override
  String get checkConnectionRetry => 'अपना कनेक्शन जांचें और दोबारा कोशिश करें';

  @override
  String get noClassroomsFound => 'कोई क्लासरूम नहीं मिला';

  @override
  String get tryDifferentSearch => 'अलग खोज या फ़िल्टर आज़माएं';

  @override
  String enrolledCountLabel(int count) {
    return '$count नामांकित';
  }

  @override
  String get classroomDetailTitle => 'क्लासरूम';

  @override
  String get upcomingSessionsTitle => 'आगामी सत्र';

  @override
  String get noUpcomingSessions => 'कोई आगामी सत्र नहीं';

  @override
  String get materialsTitle => 'सामग्री';

  @override
  String get noMaterialsYet => 'अभी तक कोई सामग्री नहीं';

  @override
  String get viewScheduleCta => 'शेड्यूल देखें';

  @override
  String get requestToJoinCta => 'जुड़ने का अनुरोध करें';

  @override
  String get choosePassTitle => 'पास चुनें';

  @override
  String passSubtitle(String price, int days) {
    return '$price कॉइन - $days दिन मान्य';
  }

  @override
  String get couponCodeOptional => 'कूपन कोड (वैकल्पिक)';

  @override
  String get messageToTeacherOptional => 'शिक्षक को संदेश (वैकल्पिक)';

  @override
  String get sendRequestCta => 'अनुरोध भेजें';

  @override
  String get liveSessionTitle => 'लाइव सत्र';

  @override
  String get couldNotJoinSession => 'सत्र में शामिल नहीं हो सके';

  @override
  String get recordingBadge => 'रिकॉर्डिंग';

  @override
  String get raiseHandCta => 'हाथ उठाएं';

  @override
  String get typeMessageHint => 'संदेश लिखें';

  @override
  String get dashboardTitle => 'होम';

  @override
  String get couldNotLoadDashboard => 'आपका डैशबोर्ड लोड नहीं हो पाया';

  @override
  String viewerCountLabel(int count) {
    return '$count देख रहे हैं';
  }

  @override
  String get myProgressTitle => 'मेरी प्रगति';

  @override
  String get attendanceLabel => 'उपस्थिति';

  @override
  String get streakLabel => 'दिन की लगातार उपस्थिति';

  @override
  String get certificatesLabel => 'प्रमाणपत्र';

  @override
  String get myEarningsTitle => 'मेरी कमाई';

  @override
  String get totalEarnedLabel => 'कुल कमाई';

  @override
  String get walletTitle => 'वॉलेट';

  @override
  String get couldNotLoadWallet => 'आपका वॉलेट लोड नहीं हो पाया';

  @override
  String get coinBalanceLabel => 'कॉइन बैलेंस';

  @override
  String get buyCoinsCta => 'कॉइन खरीदें';

  @override
  String get buyCoinsTitle => 'कॉइन खरीदें';

  @override
  String get withdrawCta => 'निकासी करें';

  @override
  String get cancelCta => 'रद्द करें';

  @override
  String get continueCta => 'जारी रखें';

  @override
  String get orderStartedMessage =>
      'ऑर्डर शुरू हुआ - कॉइन जोड़ने के लिए भुगतान पूरा करें';

  @override
  String get transactionsTitle => 'लेन-देन';

  @override
  String get noTransactionsYet => 'अभी तक कोई लेन-देन नहीं';

  @override
  String get withdrawalsTitle => 'निकासी अनुरोध';

  @override
  String get coinsUnit => 'कॉइन';

  @override
  String get actionCta => 'कार्रवाई करें';

  @override
  String get answerCta => 'उत्तर दें';

  @override
  String get answerDoubtTitle => 'प्रश्न का उत्तर दें';

  @override
  String get askCta => 'पूछें';

  @override
  String get askDoubtTitle => 'एक प्रश्न पूछें';

  @override
  String get assignRoomHint => 'कमरा';

  @override
  String get assignmentsTitle => 'असाइनमेंट';

  @override
  String get breakoutRoomsTitle => 'ब्रेकआउट रूम';

  @override
  String get certificatesReportCardsTitle => 'प्रमाणपत्र और रिपोर्ट कार्ड';

  @override
  String get certificatesTab => 'प्रमाणपत्र';

  @override
  String get chatReportsTab => 'चैट रिपोर्ट';

  @override
  String get classroomInfoTitle => 'क्लासरूम जानकारी';

  @override
  String get classroomReferralSummaryTitle => 'क्लासरूम रेफ़रल सारांश';

  @override
  String get classroomReportsTab => 'क्लासरूम रिपोर्ट';

  @override
  String get closeBreakoutCta => 'कमरे बंद करें';

  @override
  String get commissionEarnedLabel => 'अर्जित कमीशन';

  @override
  String get couldNotLoadAssignments => 'असाइनमेंट लोड नहीं हो पाए';

  @override
  String get couldNotLoadModerationData => 'मॉडरेशन डेटा लोड नहीं हो पाया';

  @override
  String get couldNotLoadPasses => 'पास लोड नहीं हो पाए';

  @override
  String get couldNotLoadRecordings => 'रिकॉर्डिंग लोड नहीं हो पाईं';

  @override
  String get couldNotLoadReferrals => 'रेफ़रल लोड नहीं हो पाए';

  @override
  String get createCta => 'बनाएं';

  @override
  String get daysAbbrev => 'दिन';

  @override
  String get dismissCta => 'खारिज करें';

  @override
  String get doneCta => 'हो गया';

  @override
  String get doubtsTab => 'प्रश्न';

  @override
  String get enterReferralCodeHint => 'रेफ़रल कोड दर्ज करें';

  @override
  String get generateParentCodeCta => 'पैरेंट कोड बनाएं';

  @override
  String get giftsTitle => 'उपहार';

  @override
  String get gradeCta => 'ग्रेड दें';

  @override
  String get gradeSubmissionTitle => 'सबमिशन को ग्रेड करें';

  @override
  String get holidaysTab => 'छुट्टियां';

  @override
  String get homeworkLabel => 'गृहकार्य';

  @override
  String get issueCertificateCta => 'प्रमाणपत्र जारी करें';

  @override
  String get mainRoomLabel => 'मुख्य कमरा';

  @override
  String get managePassesTitle => 'पास प्रबंधित करें';

  @override
  String get marksLabel => 'अंक';

  @override
  String get moderationTitle => 'मॉडरेशन';

  @override
  String get newPassTitle => 'नया पास';

  @override
  String get noAssignmentsYet => 'अभी तक कोई असाइनमेंट नहीं';

  @override
  String get noCertificatesYet => 'अभी तक कोई प्रमाणपत्र नहीं';

  @override
  String get noChatReportsPending => 'कोई चैट रिपोर्ट लंबित नहीं';

  @override
  String get noClassroomReportsPending => 'कोई क्लासरूम रिपोर्ट लंबित नहीं';

  @override
  String get noDoubtsYet => 'अभी तक कोई प्रश्न नहीं पूछा गया';

  @override
  String get noGiftsYet => 'अभी तक कोई उपहार नहीं';

  @override
  String get noHolidaysListed => 'कोई छुट्टी सूचीबद्ध नहीं';

  @override
  String get noNoticesYet => 'अभी तक कोई सूचना नहीं';

  @override
  String get noParentQueriesYet => 'अभी तक कोई पैरेंट प्रश्न नहीं';

  @override
  String get noParticipantsYet => 'अभी तक कोई प्रतिभागी नहीं';

  @override
  String get noPassesYet => 'अभी तक कोई पास नहीं';

  @override
  String get noRecordingsYet => 'अभी तक कोई रिकॉर्डिंग नहीं';

  @override
  String get noReferralsYet => 'अभी तक कोई रेफ़रल नहीं';

  @override
  String get noReportCardsYet => 'अभी तक कोई रिपोर्ट कार्ड नहीं';

  @override
  String get noSubmissionsYet => 'अभी तक कोई सबमिशन नहीं';

  @override
  String get noticesTab => 'सूचनाएं';

  @override
  String get parentCodeGeneratedTitle => 'पैरेंट एक्सेस कोड';

  @override
  String get parentObserverConnectedMessage =>
      'जुड़ गए — अब आप क्लास देख सकते हैं';

  @override
  String get parentQueriesTitle => 'पैरेंट प्रश्न';

  @override
  String get participantsTab => 'प्रतिभागी';

  @override
  String get passTitleLabel => 'पास शीर्षक';

  @override
  String get passesTitle => 'पास';

  @override
  String get peopleYouReferredTitle => 'जिन्हें आपने रेफ़र किया';

  @override
  String get priceInCoinsLabel => 'कीमत (कॉइन)';

  @override
  String recordingsPendingNote(int count) {
    return '$count रिकॉर्डिंग अभी भी प्रोसेस हो रही हैं';
  }

  @override
  String get recordingsTitle => 'रिकॉर्डिंग';

  @override
  String get redeemCta => 'रिडीम करें';

  @override
  String get referAndEarnTitle => 'रेफ़र करें और कमाएं';

  @override
  String get referralRedeemedMessage => 'रेफ़रल कोड रिडीम हो गया';

  @override
  String get replyCta => 'जवाब दें';

  @override
  String get replyToParentTitle => 'पैरेंट को जवाब दें';

  @override
  String get reportCardsTab => 'रिपोर्ट कार्ड';

  @override
  String roomNumberLabel(int n) {
    return 'कमरा $n';
  }

  @override
  String roomsActiveLabel(int count) {
    return '$count कमरे सक्रिय हैं';
  }

  @override
  String roomsCountLabel(int n) {
    return '$n कमरे';
  }

  @override
  String get saveCta => 'सहेजें';

  @override
  String get sendCta => 'भेजें';

  @override
  String timesRedeemedLabel(int count) {
    return '$count बार रिडीम हुआ';
  }

  @override
  String get validityDaysLabel => 'वैधता (दिन)';

  @override
  String get yourReferralCodeLabel => 'आपका रेफ़रल कोड';

  @override
  String get submitAnswerTitle => 'अपना उत्तर सबमिट करें';

  @override
  String get submitCta => 'सबमिट करें';

  @override
  String get newCouponTitle => 'नया कूपन';

  @override
  String get couponCodeLabel => 'कूपन कोड';

  @override
  String get discountPercentLabel => 'छूट %';

  @override
  String get validForDaysLabel => 'मान्यता (दिन)';

  @override
  String get maxUsesOptionalLabel => 'अधिकतम उपयोग (वैकल्पिक)';

  @override
  String get couponsTab => 'कूपन';

  @override
  String get noCouponsYet => 'अभी तक कोई कूपन नहीं';

  @override
  String discountPercentValueLabel(int percent) {
    return '$percent% छूट';
  }

  @override
  String discountAmountValueLabel(String amount) {
    return '$amount कॉइन की छूट';
  }

  @override
  String couponUsageLabel(int used, String max) {
    return 'उपयोग हुआ $used / $max';
  }

  @override
  String get wishlistTitle => 'विशलिस्ट';

  @override
  String get couldNotLoadWishlist => 'आपकी विशलिस्ट लोड नहीं हो पाई';

  @override
  String get wishlistEmptyTitle => 'आपकी विशलिस्ट खाली है';

  @override
  String get wishlistEmptySubtitle =>
      'किसी क्लासरूम को यहां सेव करने के लिए हार्ट पर टैप करें';

  @override
  String get attachFileOptionalCta => 'फ़ाइल संलग्न करें (वैकल्पिक)';

  @override
  String get draftRestored => 'ड्राफ़्ट वापस लाया गया';

  @override
  String get removeVideoFirst => 'पहले वीडियो हटाएं';

  @override
  String maxMediaAllowed(int count) {
    return 'अधिकतम $count मीडिया की अनुमति है';
  }

  @override
  String onlyNMoreCouldBeAdded(int count) {
    return 'केवल $count और जोड़ी जा सकती थीं';
  }

  @override
  String galleryError(String error) {
    return 'गैलरी त्रुटि: $error';
  }

  @override
  String cameraError(String error) {
    return 'कैमरा त्रुटि: $error';
  }

  @override
  String get removeImagesFirst => 'वीडियो के लिए पहले फ़ोटो हटाएं';

  @override
  String videoError(String error) {
    return 'वीडियो त्रुटि: $error';
  }

  @override
  String get addToPost => 'पोस्ट में जोड़ें';

  @override
  String get gallery => 'गैलरी';

  @override
  String get videoFromGallery => 'गैलरी से वीडियो';

  @override
  String get gifSticker => 'GIF / स्टिकर';

  @override
  String get gifComingSoon => 'GIF सुविधा जल्द आ रही है!';

  @override
  String get voiceNoteAttached => 'वॉइस नोट जोड़ा गया (सिम्युलेटेड)';

  @override
  String get discardPostTitle => 'पोस्ट हटाएं?';

  @override
  String get discardPostBody =>
      'आपका ड्राफ़्ट सहेजा गया है। आप बाद में जारी रख सकते हैं।';

  @override
  String get keepEditing => 'लिखना जारी रखें';

  @override
  String get discard => 'हटाएं';

  @override
  String get writeSomethingOrAddMedia => 'कुछ लिखें या मीडिया जोड़ें';

  @override
  String postTooLong(int count) {
    return 'पोस्ट $count अक्षरों से अधिक नहीं हो सकती';
  }

  @override
  String get categoriesLoading => 'श्रेणियां लोड हो रही हैं…';

  @override
  String get categoryLoadFailed => 'श्रेणी लोड नहीं हो सकी';

  @override
  String get uploadingMedia => 'मीडिया अपलोड हो रहा है…';

  @override
  String get posting => 'पोस्ट हो रहा है…';

  @override
  String get postCreatedSuccess => 'पोस्ट सफलतापूर्वक बनाई गई!';

  @override
  String get whatsOnYourMind => 'आपके मन में क्या है?';

  @override
  String get addPhotoOrVideoTooltip => 'फ़ोटो या वीडियो जोड़ें';

  @override
  String get emojisTooltip => 'इमोजी';

  @override
  String get voiceNoteTooltip => 'वॉइस नोट';

  @override
  String get visibilityPublic => 'सार्वजनिक';

  @override
  String get visibilityNetwork => 'नेटवर्क';

  @override
  String get visibilityPrivate => 'निजी';

  @override
  String get quickPostTitle => 'क्विक पोस्ट';

  @override
  String savedAgo(String time) {
    return '$time सहेजा गया';
  }

  @override
  String get postButton => 'पोस्ट करें';

  @override
  String get postedExclaim => 'पोस्ट हो गई!';

  @override
  String get timeAgoJustNow => 'अभी';

  @override
  String timeAgoMinutes(int count) {
    return '$count मिनट पहले';
  }

  @override
  String timeAgoHours(int count) {
    return '$count घंटे पहले';
  }

  @override
  String timeAgoDays(int count) {
    return '$count दिन पहले';
  }

  @override
  String get addLocationSheetTitle => 'स्थान जोड़ें';

  @override
  String get addLocationTagPlaceholder => 'स्थान टैग जोड़ें';

  @override
  String get addOptionLabel => 'विकल्प जोड़ें';

  @override
  String get addSomeContentFirst => 'पहले कुछ सामग्री जोड़ें';

  @override
  String get addTitleOptional => 'शीर्षक जोड़ें (वैकल्पिक)';

  @override
  String get attachmentRemoved => 'अटैचमेंट हटाया गया';

  @override
  String attachmentsCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count अटैचमेंट',
      one: '1 अटैचमेंट',
    );
    return '$_temp0';
  }

  @override
  String get autoPublishSubtitle => 'निर्धारित समय पर अपने आप प्रकाशित होगा';

  @override
  String get captionLabel => 'कैप्शन';

  @override
  String get capturePhotoNow => 'अभी एक फ़ोटो लें';

  @override
  String get categoryNoun => 'श्रेणी';

  @override
  String get categoryRequiredError => 'श्रेणी चुनना आवश्यक है';

  @override
  String get categoryRequiredLabel => 'श्रेणी *';

  @override
  String get clearAllButton => 'सब हटाएं';

  @override
  String get clearButton => 'हटाएं';

  @override
  String get clearEverythingBody =>
      'शीर्षक, सामग्री, मीडिया, पोल और सहेजा गया ड्राफ़्ट — सब एक साथ हट जाएंगे। यह पूर्ववत नहीं किया जा सकता।';

  @override
  String get clearEverythingTitle => 'सब हटाएं?';

  @override
  String get clearLocationLabel => 'स्थान हटाएं';

  @override
  String get clearSearchTooltip => 'खोज हटाएं';

  @override
  String get clearTitleTooltip => 'शीर्षक हटाएं';

  @override
  String get closeLabel => 'बंद करें';

  @override
  String get contentMediaPollRequired => 'सामग्री, मीडिया या पोल आवश्यक है';

  @override
  String couldNotSelectDocuments(String error) {
    return 'दस्तावेज़ नहीं चुने जा सके: $error';
  }

  @override
  String couldNotSelectImages(String error) {
    return 'गैलरी से फ़ोटो नहीं चुनी जा सकीं: $error';
  }

  @override
  String couldNotSelectVideo(String error) {
    return 'गैलरी से वीडियो नहीं चुना जा सका: $error';
  }

  @override
  String get dismissLabel => 'बंद करें';

  @override
  String draftCachedOnDevice(String label) {
    return '$label · इस डिवाइस पर सहेजा गया';
  }

  @override
  String draftSavedAt(String time) {
    return 'ड्राफ़्ट $time सहेजा गया';
  }

  @override
  String get draftSavedJustNow => 'ड्राफ़्ट अभी सहेजा गया';

  @override
  String draftSavedMinutesAgo(int count) {
    return 'ड्राफ़्ट $count मिनट पहले सहेजा गया';
  }

  @override
  String draftSavedSecondsAgo(int count) {
    return 'ड्राफ़्ट $count सेकंड पहले सहेजा गया';
  }

  @override
  String get editImageLabel => 'फ़ोटो संपादित करें';

  @override
  String get editVideoLabel => 'वीडियो संपादित करें';

  @override
  String get everythingCleared => 'सब कुछ हटा दिया गया';

  @override
  String failedToLoadCategories(String error) {
    return 'श्रेणियां लोड नहीं हो सकीं: $error';
  }

  @override
  String get filesLabel => 'फ़ाइलें';

  @override
  String get fillAllPollOptions => 'सभी पोल विकल्प भरें';

  @override
  String get locationLabel => 'स्थान';

  @override
  String maxFilesAllowed(int count) {
    return 'अधिकतम $count फ़ाइलों की अनुमति है';
  }

  @override
  String pdfPagesCount(int count) {
    return '$count पेज';
  }

  @override
  String fileSizeKb(int count) {
    return '$count KB';
  }

  @override
  String get newPostTitle => 'नई पोस्ट';

  @override
  String noMatchForQuery(String query) {
    return '\"$query\" के लिए कोई मेल नहीं मिला';
  }

  @override
  String onlyNMoreFilesCouldBeAdded(int count) {
    return 'केवल $count और फ़ाइल जोड़ी जा सकती थीं';
  }

  @override
  String get photosLabel => 'फ़ोटो';

  @override
  String get pollLabel => 'पोल';

  @override
  String get pollNeedsTwoOptions => 'पोल में कम से कम 2 विकल्प होने चाहिए';

  @override
  String pollOptionNumber(int number) {
    return 'विकल्प $number';
  }

  @override
  String get pollOptionRemoved => 'पोल विकल्प हटाया गया';

  @override
  String get pollOptionsMustDiffer => 'पोल विकल्प एक जैसे नहीं हो सकते';

  @override
  String get postDetailsLabel => 'पोस्ट विवरण';

  @override
  String postScheduledFor(String time) {
    return 'पोस्ट $time के लिए निर्धारित की गई!';
  }

  @override
  String get quickPostBannerSubtitle =>
      'बस टेक्स्ट लिखना है? यहां तेज़ी से लिखें';

  @override
  String get removeAttachmentLabel => 'अटैचमेंट हटाएं';

  @override
  String get removeOptionTooltip => 'विकल्प हटाएं';

  @override
  String get removePollTooltip => 'पोल हटाएं';

  @override
  String get scheduleInOneHour => '1 घंटे में';

  @override
  String get scheduleLabel => 'समय निर्धारित करें';

  @override
  String get scheduleThisEvening => 'आज शाम';

  @override
  String get scheduleTomorrowEvening => 'कल शाम';

  @override
  String get scheduleTomorrowMorning => 'कल सुबह';

  @override
  String searchWithin(String title) {
    return '$title खोजें';
  }

  @override
  String get searchingHashtags => 'हैशटैग खोजे जा रहे हैं…';

  @override
  String get searchingPeople => 'लोग खोजे जा रहे हैं…';

  @override
  String get selectCategoryError => 'एक श्रेणी चुनें';

  @override
  String get selectCategoryTitle => 'श्रेणी चुनें';

  @override
  String get selectFutureTime => 'भविष्य का समय चुनें';

  @override
  String get selectSubcategoryError => 'एक उपश्रेणी चुनें';

  @override
  String get selectSubcategoryTitle => 'उपश्रेणी चुनें';

  @override
  String get shootQuickVideoClip => 'एक छोटा वीडियो शूट करें';

  @override
  String get subcategoryNoun => 'उपश्रेणी';

  @override
  String get subcategoryRequiredLabel => 'उपश्रेणी';

  @override
  String get takePhotoLabel => 'फ़ोटो लें';

  @override
  String get undoLabel => 'पूर्ववत करें';

  @override
  String get unsavedCloseWarning =>
      'आपने जो लिखा है वह अभी सहेजा नहीं गया है। अभी बंद करने पर यह खो जाएगा।';

  @override
  String get useCameraLabel => 'कैमरा इस्तेमाल करें';

  @override
  String get videosLabel => 'वीडियो';

  @override
  String get visibilityAnyoneCanSee => 'कोई भी देख सकता है';

  @override
  String get visibilityConnections => 'कनेक्शन';

  @override
  String get visibilityJustYou => 'सिर्फ़ आप';

  @override
  String get visibilityOnlyMe => 'सिर्फ़ मैं';

  @override
  String get visibilityOnlyYourNetwork => 'सिर्फ़ आपका नेटवर्क';

  @override
  String get whatsOnYourMindHashtags =>
      'आपके मन में क्या है? पहुंच बढ़ाने के लिए #हैशटैग इस्तेमाल करें';

  @override
  String get writeSomethingAboutMedia => 'इस मीडिया के बारे में कुछ लिखें...';

  @override
  String get addMediaLabel => 'मीडिया जोड़ें';

  @override
  String get addPhotosVideosMinTwo => 'फ़ोटो/वीडियो जोड़ें — कम से कम 2';

  @override
  String get addTextLabel => 'टेक्स्ट जोड़ें';

  @override
  String get addingMusicProgress => 'संगीत जोड़ा जा रहा है…';

  @override
  String get aspectPortrait => '4:5 पोर्ट्रेट';

  @override
  String get aspectReel => '9:16 रील';

  @override
  String get aspectSquare => '1:1 वर्ग';

  @override
  String get aspectWide => '16:9 वाइड';

  @override
  String get autoBeatSyncLabel => 'ऑटो (बीट सिंक)';

  @override
  String get autoEditTitle => 'ऑटो एडिट';

  @override
  String get autoModeDescription =>
      'ऑटो: एनर्जी के हिसाब से कट, ट्रांज़िशन और केन बर्न्स, BPM पता लगाया गया।';

  @override
  String get backgroundMusicPlaceholder => 'बैकग्राउंड संगीत चुनें';

  @override
  String get beatSyncSetupFailed => 'बीट-सिंक कट सेट अप नहीं हो सका।';

  @override
  String get blendingTransitionsProgress => 'ट्रांज़िशन जोड़े जा रहे हैं…';

  @override
  String get canvasColorGradeTooltip => 'कैनवास और कलर ग्रेड';

  @override
  String canvasGradeSummary(String aspect, String grade) {
    return '$aspect · $grade ग्रेड — बदलने के लिए ट्यून आइकन दबाएं';
  }

  @override
  String get canvasLabel => 'कैनवास';

  @override
  String get captionFontNotReady =>
      'कैप्शन फ़ॉन्ट अभी तैयार हो रहा है — थोड़ी देर में फिर कोशिश करें, वरना रेंडर करते समय यह टेक्स्ट छोड़ दिया जाएगा।';

  @override
  String get captionForClipHint => 'इस क्लिप के लिए कैप्शन';

  @override
  String get cc0OnlyNotice => 'केवल कॉपीराइट-मुक्त (CC0) संगीत दिखाया जाता है';

  @override
  String get changeMusicButton => 'संगीत बदलें';

  @override
  String get chooseMusicFromDevice => 'डिवाइस से चुनें';

  @override
  String get clipLengthLabel => 'क्लिप की लंबाई';

  @override
  String clipNumberDuration(int number, String duration) {
    return 'क्लिप $number · $durationसे';
  }

  @override
  String get clipSetupFailed => 'क्लिप सेट अप नहीं हो सके।';

  @override
  String get clipTextDialogTitle => 'क्लिप टेक्स्ट';

  @override
  String get colorGradeLabel => 'कलर ग्रेड';

  @override
  String get couldntAddMusicTrack =>
      'संगीत ट्रैक मोंटाज में नहीं जोड़ा जा सका।';

  @override
  String get couldntAddTrack => 'वह ट्रैक नहीं जोड़ा जा सका — फिर कोशिश करें।';

  @override
  String get couldntBlendTransitions =>
      'क्लिप के बीच ट्रांज़िशन नहीं जुड़ सके।';

  @override
  String couldntDownloadTrack(int code) {
    return 'वह ट्रैक डाउनलोड नहीं हो सका (सर्वर ने कहा $code)। फिर कोशिश करें।';
  }

  @override
  String couldntProcessClip(int number) {
    return 'क्लिप $number प्रोसेस नहीं हो सकी — यह खराब या असमर्थित फ़ॉर्मेट में हो सकती है। इसे हटाएं या बदलें।';
  }

  @override
  String get couldntTrimTrack =>
      'वह ट्रैक ट्रिम नहीं हो सका — अलग क्लिप लंबाई आज़माएं।';

  @override
  String get doneLabel => 'हो गया';

  @override
  String get emptySearchPrompt => 'कुछ खोजें...';

  @override
  String get exportButton => 'एक्सपोर्ट';

  @override
  String get gradeBw => 'ब्लैक एंड व्हाइट';

  @override
  String get gradeMoody => 'मूडी';

  @override
  String get gradeNone => 'कोई नहीं';

  @override
  String get gradeVibrant => 'वाइब्रेंट';

  @override
  String get gradeVintage => 'विंटेज';

  @override
  String get gradeWarm => 'वार्म';

  @override
  String get hitRegenerateHint =>
      'नए कैनवास/ग्रेड के साथ फिर से रेंडर करने के लिए Regenerate दबाएं।';

  @override
  String inPointSeconds(String seconds) {
    return 'इन-पॉइंट: $secondsसे';
  }

  @override
  String get kenBurnsLabel => 'केन बर्न्स';

  @override
  String get lastClipLabel => 'आख़िरी क्लिप';

  @override
  String get manualModeDescription =>
      'मैनुअल: क्लिप बराबर बंटी हैं, सब कुछ खुद सेट करें।';

  @override
  String get manualSetupLabel => 'मैनुअल सेटअप';

  @override
  String get nextMusicButton => 'आगे: संगीत';

  @override
  String get noInternetConnectionRetry =>
      'इंटरनेट कनेक्शन नहीं है — अपना कनेक्शन जांचें और फिर कोशिश करें।';

  @override
  String get placingCutsProgress => 'बीट पर कट लगाए जा रहे हैं…';

  @override
  String preparingClipOfTotal(int current, int total) {
    return 'क्लिप $current / $total तैयार हो रही है…';
  }

  @override
  String preparingOfTotal(int current, int total) {
    return '$current / $total तैयार हो रहा है…';
  }

  @override
  String get readingMusicProgress => 'संगीत पढ़ा जा रहा है…';

  @override
  String get regenerateButton => 'फिर से बनाएं';

  @override
  String get renderingMontageFailed => 'मोंटाज रेंडर नहीं हो सका।';

  @override
  String get retryButton => 'फिर कोशिश करें';

  @override
  String scoringClipProgress(int current, int total) {
    return 'क्लिप $current / $total जांची जा रही है…';
  }

  @override
  String get searchDidntGoThrough => 'खोज नहीं हो सकी — फिर कोशिश करें।';

  @override
  String get searchFreesoundHint => 'Freesound पर खोजें (जैसे lofi, guitar)';

  @override
  String get settingUpClipsProgress => 'क्लिप सेट अप हो रहे हैं…';

  @override
  String get someMediaNotOptimized =>
      'कुछ मीडिया ऑप्टिमाइज़ नहीं हो सका, लेकिन जैसा था वैसे जोड़ दिया गया।';

  @override
  String get sourceLengthUnknown => 'स्रोत की लंबाई अज्ञात — इन-पॉइंट बंद है';

  @override
  String speedLabel(String value) {
    return 'गति: ${value}x';
  }

  @override
  String get startPointLabel => 'शुरुआती बिंदु';

  @override
  String get textAddedCheckLabel => 'टेक्स्ट ✓';

  @override
  String get transCircle => 'सर्कल';

  @override
  String get transDissolve => 'डिसॉल्व';

  @override
  String get transFlash => 'फ़्लैश';

  @override
  String get transGlitch => 'ग्लिच';

  @override
  String get transRadial => 'रेडियल';

  @override
  String get transRgbSplit => 'RGB स्प्लिट';

  @override
  String get transShake => 'शेक';

  @override
  String get transSlide => 'स्लाइड';

  @override
  String get transSmoothSlide => 'स्मूद स्लाइड';

  @override
  String get transSqueeze => 'स्क्वीज़';

  @override
  String get transWipe => 'वाइप';

  @override
  String get transZoom => 'ज़ूम';

  @override
  String get trimSpeedLabel => 'ट्रिम और गति';

  @override
  String get useThisSoundButton => 'यह साउंड इस्तेमाल करें';

  @override
  String get addClipLabel => 'क्लिप जोड़ें';

  @override
  String get addMorePhotosVideosHint =>
      'और फ़ोटो/वीडियो जोड़ें — सबको मिलाकर एक वीडियो बनेगा';

  @override
  String get addPhotoLabel => 'फ़ोटो जोड़ें';

  @override
  String get addPhotosTabLabel => 'फ़ोटो जोड़ें';

  @override
  String get addStickerTitle => 'स्टिकर जोड़ें';

  @override
  String get adjustTabLabel => 'एडजस्ट';

  @override
  String get analyzingLabel => 'जांच हो रही है…';

  @override
  String get applyLabel => 'लागू करें';

  @override
  String autoEnhanceFailed(String error) {
    return 'ऑटो-एन्हांस नहीं हो सका: $error';
  }

  @override
  String get autoEnhanceLabel => 'ऑटो-एन्हांस';

  @override
  String get autoPlacedOnFaceHint =>
      'पहचाने गए चेहरे पर अपने आप लगाया गया — बाद में खींचें/घुमाएं।';

  @override
  String get batteryLabel => 'बैटरी';

  @override
  String get boomerangBakeFailed => 'बूमरैंग इफ़ेक्ट नहीं बन सका।';

  @override
  String get boomerangOnHint => 'बूमरैंग ऑन — आगे + पीछे लूप';

  @override
  String get boomerangTabLabel => 'बूमरैंग';

  @override
  String get brightnessLabel => 'ब्राइटनेस';

  @override
  String get brushLabel => 'ब्रश';

  @override
  String get chooseCoverFrame => 'कवर फ़्रेम चुनें';

  @override
  String get clearDrawingTooltip => 'ड्राइंग हटाएं';

  @override
  String get clip1ThisMediaLabel => 'क्लिप 1 (यह फ़ोटो/वीडियो)';

  @override
  String get clip2TransitionLabel => 'क्लिप 2 में ट्रांज़िशन';

  @override
  String clipNormalizeFailed(int number) {
    return 'क्लिप $number प्रोसेस नहीं हो सकी।';
  }

  @override
  String clipPinchZoomPan(int number) {
    return 'क्लिप $number — ज़ूम के लिए पिंच करें, घुमाने के लिए खींचें';
  }

  @override
  String clipPreparingOfTotal(int current, int total) {
    return 'क्लिप $current/$total तैयार हो रही है…';
  }

  @override
  String clipSelectFailed(String error) {
    return 'वह क्लिप नहीं चुनी जा सकी: $error';
  }

  @override
  String get clipsJoinFailed => 'क्लिप जोड़ी नहीं जा सकीं।';

  @override
  String get clipsTabLabel => 'क्लिप';

  @override
  String clipsWillMakeVideoWith(int count) {
    return '$count क्लिप मिलाकर वीडियो बनेगा (इस फ़ोटो/वीडियो के साथ)';
  }

  @override
  String get color2GradientHint => 'रंग 2 (ग्रेडिएंट) — ऊपर एक स्वैच दबाएं';

  @override
  String get contrastLabel => 'कंट्रास्ट';

  @override
  String get coverPreviewUnavailable => 'कवर प्रीव्यू उपलब्ध नहीं है';

  @override
  String get coverTabLabel => 'कवर';

  @override
  String get cropFailed => 'क्रॉप नहीं हो सका।';

  @override
  String get dateLabel => 'तारीख़';

  @override
  String get deleteLabel => 'हटाएं';

  @override
  String downloadFailedCode(int code) {
    return 'डाउनलोड विफल ($code)';
  }

  @override
  String get drawTabLabel => 'ड्रॉ';

  @override
  String get editLabel => 'संपादित करें';

  @override
  String editSaveFailed(String error) {
    return 'संपादन सहेजा नहीं जा सका: $error';
  }

  @override
  String eraserApplyFailed(String error) {
    return 'इरेज़र लागू नहीं हो सका: $error';
  }

  @override
  String get eraserHint =>
      'किसी दाग/वॉटरमार्क पर पेंट करें, फिर Apply दबाएं। हल्के बैकग्राउंड पर छोटे धब्बों के लिए सबसे अच्छा।';

  @override
  String get eraserTabLabel => 'इरेज़र';

  @override
  String get erasingLabel => 'मिटाया जा रहा है…';

  @override
  String get exportWithMusicFailedTryWithout =>
      'संगीत के साथ एक्सपोर्ट नहीं हुआ — बिना संगीत के फिर कोशिश करें';

  @override
  String extraClipsWillJoinEnd(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: 'आख़िर में $count अतिरिक्त क्लिप जुड़ेंगी',
      one: 'आख़िर में 1 अतिरिक्त क्लिप जुड़ेगी',
    );
    return '$_temp0';
  }

  @override
  String get faceFiltersTitle => 'फेस फ़िल्टर';

  @override
  String get filtersTabLabel => 'फ़िल्टर';

  @override
  String get flipHorizontalTooltip => 'क्षैतिज पलटें';

  @override
  String get flipVerticalTooltip => 'लंबवत पलटें';

  @override
  String get fontLabel => 'फ़ॉन्ट';

  @override
  String get fxBakeFailed => 'इफ़ेक्ट लागू नहीं हो सका।';

  @override
  String get fxTabLabel => 'FX';

  @override
  String get keepOriginalAudioToo => 'वीडियो का असली ऑडियो भी रखें';

  @override
  String get lengthStaysAsTrimSet =>
      'इसकी लंबाई वही रहेगी जो Trim टैब में सेट है';

  @override
  String get liveStickersTitle => 'लाइव स्टिकर';

  @override
  String get loadingEllipsis => 'लोड हो रहा है...';

  @override
  String mediaAddFailed(String error) {
    return 'वह फ़ोटो/वीडियो नहीं जोड़ी जा सकी: $error';
  }

  @override
  String montageExportFailed(String error) {
    return 'मोंटाज एक्सपोर्ट नहीं हो सका: $error';
  }

  @override
  String musicAddFailed(String error) {
    return 'संगीत नहीं जोड़ा जा सका: $error';
  }

  @override
  String get musicAttachFailed => 'संगीत जोड़ा नहीं जा सका।';

  @override
  String get musicMixFailed => 'संगीत मिक्स नहीं हो सका।';

  @override
  String musicSelectFailed(String error) {
    return 'वह संगीत नहीं चुना जा सका: $error';
  }

  @override
  String get musicTabLabel => 'संगीत';

  @override
  String get musicTrackLabel => 'संगीत ट्रैक';

  @override
  String get myStickersTitle => 'मेरे स्टिकर';

  @override
  String get nextClipTransitionLabel => 'अगली क्लिप में ट्रांज़िशन';

  @override
  String get noExtraClipsYet =>
      'अभी कोई अतिरिक्त क्लिप नहीं — एक के बाद एक जोड़ने के लिए कुछ जोड़ें';

  @override
  String get noFaceDetectedNote => '(कोई चेहरा नहीं मिला — बीच में लगाया गया)';

  @override
  String get orLabel => 'या';

  @override
  String photoSelectFailed(String error) {
    return 'वह फ़ोटो नहीं चुनी जा सकी: $error';
  }

  @override
  String get primaryClipFxFailed => 'मुख्य क्लिप पर इफ़ेक्ट लागू नहीं हो सके।';

  @override
  String get primaryClipTrimFailed => 'मुख्य क्लिप ट्रिम नहीं हो सकी।';

  @override
  String get redoTooltip => 'फिर से करें';

  @override
  String get removeBackToEditing => 'हटाएं — संपादन पर वापस जाएं';

  @override
  String get removeMusicTooltip => 'संगीत हटाएं';

  @override
  String get resetTooltip => 'रीसेट';

  @override
  String get resetZoomLabel => 'ज़ूम रीसेट करें';

  @override
  String get rotateLabel => 'घुमाएं';

  @override
  String get rotateRightLabel => 'दाएं घुमाएं';

  @override
  String get saturationLabel => 'सैचुरेशन';

  @override
  String get searchFailedRetry => 'खोज विफल — फिर कोशिश करें';

  @override
  String get speedAdjustFailed => 'गति समायोजित नहीं हो सकी।';

  @override
  String get speedTabLabel => 'गति';

  @override
  String get startFromLabel => 'यहां से शुरू करें';

  @override
  String get stickersTabLabel => 'स्टिकर';

  @override
  String get styleLabel => 'स्टाइल';

  @override
  String get tapAddStickerHint =>
      'Add Sticker दबाएं, फिर उसे फ़ोटो पर खींचें/घुमाएं';

  @override
  String get tapAddTextHint => 'Add Text दबाएं, फिर उसे फ़ोटो पर खींचें/घुमाएं';

  @override
  String get tapToEnableBoomerang => 'बूमरैंग चालू करने के लिए दबाएं';

  @override
  String get textTabLabel => 'टेक्स्ट';

  @override
  String get timeLabel => 'समय';

  @override
  String transformFailed(String error) {
    return 'वह लागू नहीं हो सका: $error';
  }

  @override
  String get transitionChainFailed => 'ट्रांज़िशन आपस में नहीं जुड़ सके।';

  @override
  String get transitionsBlendingProgress => 'ट्रांज़िशन जोड़े जा रहे हैं…';

  @override
  String get trimTabLabel => 'ट्रिम';

  @override
  String get undoTooltip => 'पूर्ववत करें';

  @override
  String get useLabel => 'इस्तेमाल करें';

  @override
  String get useThisLabel => 'यह इस्तेमाल करें';

  @override
  String get videoControllerNotReady => 'वीडियो अभी तैयार नहीं है।';

  @override
  String videoLoadFailed(String error) {
    return 'वीडियो लोड नहीं हो सका: $error';
  }

  @override
  String get videoTrimExportFailed =>
      'ट्रिम किया वीडियो एक्सपोर्ट नहीं हो सका।';

  @override
  String videoTrimSaveFailed(String error) {
    return 'ट्रिम किया वीडियो सहेजा नहीं जा सका: $error';
  }

  @override
  String get focusHistoryTitle => 'फ़ोकस मोड — इतिहास';

  @override
  String get focusHistoryLoadFailed => 'इतिहास लोड नहीं हो पाया।';

  @override
  String get focusHistoryEmpty => 'अभी तक कोई फ़ोकस सेशन नहीं हुआ।';

  @override
  String focusHistoryMinutes(int minutes) {
    return '$minutes मिनट';
  }

  @override
  String focusHistoryHours(int hours) {
    return '$hours घंटे';
  }

  @override
  String focusHistoryHoursMinutes(int hours, int minutes) {
    return '$hours घंटे $minutes मिनट';
  }

  @override
  String get focusRuleNobody => 'पूरी शांति';

  @override
  String get focusRuleTeachersOnly => 'सिर्फ़ शिक्षक';

  @override
  String focusHistoryMeta(String duration, String rule) {
    return '$duration · $rule';
  }

  @override
  String focusHistoryMetaEndedEarly(String duration, String rule) {
    return '$duration · $rule · जल्दी समाप्त हुआ';
  }

  @override
  String get parentAccessTitle => 'पैरेंट/गार्जियन एक्सेस';

  @override
  String get parentAccessNewCode => 'नया कोड';

  @override
  String get parentAccessIntro =>
      'पैरेंट/गार्जियन को यहाँ से कोड दें — उन्हें सिर्फ़ उपस्थिति और असाइनमेंट की स्थिति दिखेगी, कोई चैट संदेश नहीं। किसी कोड पर टैप करके उसके अलग-अलग डिवाइस मैनेज करें।';

  @override
  String get parentAccessEmpty => 'अभी कोई सक्रिय कोड नहीं है।';

  @override
  String get parentAccessLoadFailed => 'कोड लोड नहीं हो पाए।';

  @override
  String get parentAccessLabelDialogTitle => 'यह कोड किसके लिए है?';

  @override
  String get parentAccessLabelHint => 'जैसे: मम्मी, पापा';

  @override
  String get parentAccessGenerate => 'बनाएं';

  @override
  String get parentAccessGenerateFailed =>
      'कोड नहीं बन पाया। कृपया फिर से प्रयास करें।';

  @override
  String get parentAccessShareCodeTitle =>
      'यह कोड अपने पैरेंट के साथ साझा करें';

  @override
  String get parentAccessCodeCopied => 'कॉपी हो गया';

  @override
  String get parentAccessRevokeTitle => 'एक्सेस हटाएं?';

  @override
  String parentAccessRevokeBodyMany(int count, String name) {
    return '\"$name\" से जुड़े सभी $count डिवाइस का एक्सेस तुरंत बंद हो जाएगा।';
  }

  @override
  String parentAccessRevokeBodyOne(String name) {
    return '\"$name\" का एक्सेस तुरंत बंद हो जाएगा।';
  }

  @override
  String get parentAccessRevoke => 'एक्सेस हटाएं';

  @override
  String get parentAccessRevokeFailed =>
      'एक्सेस नहीं हट पाया। कृपया फिर से प्रयास करें।';

  @override
  String get parentAccessRevealRateLimited =>
      'बहुत बार कोड दिखाया गया — थोड़ी देर बाद प्रयास करें।';

  @override
  String get parentAccessRevealFailed => 'कोड नहीं दिखाया जा सका।';

  @override
  String get parentAccessRenew => 'नवीनीकृत करें';

  @override
  String get parentAccessRenewed => 'एक्सेस नवीनीकृत हो गया।';

  @override
  String get parentAccessRenewFailed =>
      'नवीनीकरण नहीं हो पाया। कृपया फिर से प्रयास करें।';

  @override
  String get parentAccessUnnamed => 'बिना नाम';

  @override
  String parentAccessDevicesTap(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count डिवाइस · मैनेज करने के लिए टैप करें',
      one: '1 डिवाइस · मैनेज करने के लिए टैप करें',
    );
    return '$_temp0';
  }

  @override
  String get parentAccessRevokeAllTooltip => 'पूरा कोड हटाएं (सभी डिवाइस)';

  @override
  String get parentAccessExpiredToday => 'आज समाप्त हुआ';

  @override
  String parentAccessExpiredDaysAgo(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count दिन पहले समाप्त हुआ',
      one: '1 दिन पहले समाप्त हुआ',
    );
    return '$_temp0';
  }

  @override
  String get parentAccessExpiresToday => 'आज समाप्त हो रहा है';

  @override
  String parentAccessExpiresInDays(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count दिन में समाप्त होगा',
      one: '1 दिन में समाप्त होगा',
    );
    return '$_temp0';
  }

  @override
  String parentAccessExpiresOn(String date) {
    return '$date को समाप्त होगा';
  }

  @override
  String get parentAccessNeverUsed => 'कभी उपयोग नहीं हुआ';

  @override
  String get parentAccessDevicesTitle => 'डिवाइस';

  @override
  String parentAccessDevicesTitleNamed(String name) {
    return '$name — डिवाइस';
  }

  @override
  String get parentAccessDevicesHint =>
      'एक डिवाइस हटाने से बाकी डिवाइस का एक्सेस चालू रहता है।';

  @override
  String get parentAccessDevicesLoadFailed => 'डिवाइस लोड नहीं हो पाए।';

  @override
  String get parentAccessNoDevices =>
      'इस कोड से अभी कोई डिवाइस वेरिफ़ाई नहीं हुआ।';

  @override
  String parentAccessLastActive(String time) {
    return 'आखिरी बार सक्रिय: $time';
  }

  @override
  String parentAccessVerifiedAt(String time) {
    return 'वेरिफ़ाई हुआ: $time';
  }

  @override
  String get parentAccessRevokeDeviceTooltip => 'सिर्फ़ यह डिवाइस हटाएं';

  @override
  String get parentAccessRevokeDeviceTitle => 'यह डिवाइस हटाएं?';

  @override
  String get parentAccessRevokeDeviceBody =>
      'सिर्फ़ यही एक डिवाइस डिस्कनेक्ट होगा, बाकी चालू रहेंगे।';

  @override
  String get parentAccessRevokeDeviceFailed => 'डिवाइस नहीं हट पाया।';

  @override
  String get chatYou => 'आप';

  @override
  String get chatUnknown => 'अज्ञात';

  @override
  String get chatSomeone => 'कोई';

  @override
  String get chatGenericError => 'त्रुटि';

  @override
  String get chatScrollToMessageFailed =>
      'उस संदेश तक स्क्रोल नहीं हो पाया (वह बहुत पुराना हो सकता है)';

  @override
  String chatLoadMessagesFailed(String error) {
    return 'संदेश लोड नहीं हो पाए: $error';
  }

  @override
  String chatLoadOlderFailed(String error) {
    return 'पुराने संदेश लोड नहीं हो पाए: $error';
  }

  @override
  String chatLoadFailed(String error) {
    return 'लोड नहीं हो पाया: $error';
  }

  @override
  String chatPinnedBanner(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count पिन किए गए संदेश',
      one: 'पिन किया गया संदेश',
    );
    return '$_temp0';
  }

  @override
  String get chatAttachment => '📎 अटैचमेंट';

  @override
  String get chatPinnedMessagesTitle => 'पिन किए गए संदेश';

  @override
  String get chatNoPinned => 'कोई पिन किया गया संदेश नहीं है';

  @override
  String chatPinnedBy(String name) {
    return '$name ने पिन किया';
  }

  @override
  String get chatPin => 'पिन करें';

  @override
  String get chatUnpin => 'अनपिन करें';

  @override
  String chatWallpaperSetFailed(String error) {
    return 'वॉलपेपर सेट नहीं हो पाया: $error';
  }

  @override
  String get chatWallpaperRemoveTitle => 'वॉलपेपर हटाएं?';

  @override
  String get chatWallpaperRemoveBody =>
      'यह चैट डिफ़ॉल्ट बैकग्राउंड पर वापस चली जाएगी।';

  @override
  String get chatRemove => 'हटाएं';

  @override
  String chatWallpaperRemoveFailed(String error) {
    return 'वॉलपेपर हट नहीं पाया: $error';
  }

  @override
  String get chatChangeWallpaper => 'वॉलपेपर बदलें';

  @override
  String get chatSetWallpaper => 'वॉलपेपर सेट करें';

  @override
  String get chatRemoveWallpaper => 'वॉलपेपर हटाएं';

  @override
  String get chatWallpaperMenu => 'चैट वॉलपेपर';

  @override
  String get chatSettingWallpaper => 'वॉलपेपर सेट हो रहा है…';

  @override
  String get chatFilterText => 'टेक्स्ट';

  @override
  String get chatFilterMedia => 'मीडिया';

  @override
  String get chatFilterDocs => 'दस्तावेज़';

  @override
  String get chatFilterLinks => 'लिंक';

  @override
  String get chatFilterTitle => 'संदेश फ़िल्टर करें';

  @override
  String get chatFilterAllMessages => 'सभी संदेश';

  @override
  String get chatFilterImageVideo => 'फ़ोटो / वीडियो';

  @override
  String get chatFilterDocsFiles => 'दस्तावेज़ / फ़ाइलें';

  @override
  String get chatFilterUrlLinks => 'URL / लिंक';

  @override
  String chatFilterActive(String label) {
    return 'फ़िल्टर: $label';
  }

  @override
  String chatFilterResultCount(String label, int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count संदेश',
      one: '1 संदेश',
    );
    return '$label • $_temp0';
  }

  @override
  String chatNoFilteredMessages(String label) {
    return 'इस चैट में कोई $label संदेश नहीं है';
  }

  @override
  String get chatNotificationsMuted => 'सूचनाएं म्यूट की गईं';

  @override
  String get chatNotificationsUnmuted => 'सूचनाएं अनम्यूट की गईं';

  @override
  String chatUpdateFailed(String error) {
    return 'अपडेट नहीं हो पाया: $error';
  }

  @override
  String get chatMuteGroup => 'ग्रुप म्यूट करें';

  @override
  String get chatUnmuteGroup => 'ग्रुप अनम्यूट करें';

  @override
  String get chatMuteNotifications => 'सूचनाएं म्यूट करें';

  @override
  String get chatUnmuteNotifications => 'सूचनाएं अनम्यूट करें';

  @override
  String get chatThisUser => 'यह यूज़र';

  @override
  String get chatBlockTitle => 'यूज़र को ब्लॉक करें?';

  @override
  String chatBlockBody(String name) {
    return '$name आपको कॉल या संदेश नहीं कर पाएगा, और आपको भी उसके संदेश नहीं दिखेंगे।';
  }

  @override
  String get chatBlock => 'ब्लॉक करें';

  @override
  String get chatUserBlocked => 'यूज़र ब्लॉक हो गया।';

  @override
  String chatBlockFailed(String error) {
    return 'ब्लॉक नहीं हो पाया: $error';
  }

  @override
  String get chatUserUnblocked => 'यूज़र अनब्लॉक हो गया।';

  @override
  String chatUnblockFailed(String error) {
    return 'अनब्लॉक नहीं हो पाया: $error';
  }

  @override
  String get chatBlockUser => 'यूज़र को ब्लॉक करें';

  @override
  String get chatUnblockUser => 'यूज़र को अनब्लॉक करें';

  @override
  String get chatBlockedBanner =>
      'आपने इस यूज़र को ब्लॉक किया है। संदेश भेजने के लिए अनब्लॉक करें।';

  @override
  String get chatUnblock => 'अनब्लॉक करें';

  @override
  String get chatDisappearingOff => 'बंद';

  @override
  String get chatDisappear1Month => '1 महीना';

  @override
  String get chatDisappear6Months => '6 महीने';

  @override
  String get chatDisappear1Year => '1 साल';

  @override
  String get chatDisappearingTitle => 'गायब होने वाले संदेश';

  @override
  String get chatDisappearingSubtitle =>
      'नए संदेश चुने गए समय के बाद चैट से अपने आप गायब हो जाएंगे।';

  @override
  String get chatDisappearingTurnedOff => 'गायब होने वाले संदेश बंद किए गए';

  @override
  String chatDisappearingNewMessages(String duration) {
    return 'नए संदेश $duration के बाद गायब हो जाएंगे';
  }

  @override
  String chatDisappearingSetTo(String duration) {
    return 'गायब होने वाले संदेश $duration पर सेट किए गए';
  }

  @override
  String chatDisappearingMenu(String duration) {
    return 'गायब होना: $duration';
  }

  @override
  String get chatPermissionsTitle => 'संदेश की अनुमतियां';

  @override
  String get chatPermissionsSubtitle =>
      'तय करें कि इस ग्रुप में कौन संदेश भेज सकता है।';

  @override
  String get chatPermEveryone => 'सभी';

  @override
  String get chatPermEveryoneSub => 'सभी सदस्य चैट कर सकते हैं';

  @override
  String get chatPermAdminsOnly => 'सिर्फ़ एडमिन और मॉडरेटर';

  @override
  String get chatPermAdminsOnlySub =>
      'बाकी सब सिर्फ़ पढ़ सकते हैं, संदेश नहीं भेज सकते';

  @override
  String get chatDailyLimitTitle => 'रोज़ाना संदेश सीमा (सदस्य)';

  @override
  String get chatDailyLimitHelp =>
      'सामान्य सदस्य दिनभर में सिर्फ़ इतने संदेश भेज पाएंगे (एडमिन/मॉडरेटर हमेशा असीमित)। कोई सीमा न चाहिए तो खाली छोड़ें।';

  @override
  String get chatDailyLimitHint => 'जैसे 4';

  @override
  String get chatDailyLimitSuffix => 'संदेश / दिन';

  @override
  String get chatPermUpdatedAdminsOnly =>
      'अब सिर्फ़ एडमिन और मॉडरेटर संदेश भेज सकते हैं।';

  @override
  String get chatPermUpdatedEveryone => 'अब सभी सदस्य संदेश भेज सकते हैं।';

  @override
  String chatUpdateFailedDetail(String error) {
    return 'अपडेट नहीं हो पाया: $error';
  }

  @override
  String get chatAdminsOnlyBanner =>
      'इस ग्रुप में सिर्फ़ एडमिन और मॉडरेटर संदेश भेज सकते हैं।';

  @override
  String get chatDailyLimitReached => 'रोज़ाना की सीमा पूरी हो गई';

  @override
  String get chatNoLongerMember => 'आप अब सदस्य नहीं हैं';

  @override
  String get chatMessageNotAllowed => 'संदेश की अनुमति नहीं है';

  @override
  String get chatGroupInfo => 'ग्रुप की जानकारी';

  @override
  String get chatChangeGroupPhoto => 'ग्रुप फ़ोटो बदलें';

  @override
  String get chatUploadingPhoto => 'फ़ोटो अपलोड हो रही है...';

  @override
  String get chatGroupPhotoUpdated => 'ग्रुप फ़ोटो अपडेट हो गई ✅';

  @override
  String chatPhotoUpdateFailed(String error) {
    return 'फ़ोटो अपडेट नहीं हो पाई: $error';
  }

  @override
  String get chatJoinRequestsTitle => 'जुड़ने के अनुरोध';

  @override
  String chatJoinRequestsCount(int count) {
    return 'जुड़ने के अनुरोध ($count)';
  }

  @override
  String get chatNoPendingRequests => 'कोई लंबित अनुरोध नहीं है';

  @override
  String get chatApprove => 'स्वीकार करें';

  @override
  String chatApproveFailed(String error) {
    return 'स्वीकार नहीं हो पाया: $error';
  }

  @override
  String get chatReject => 'अस्वीकार करें';

  @override
  String chatRejectFailed(String error) {
    return 'अस्वीकार नहीं हो पाया: $error';
  }

  @override
  String get chatLeaveGroup => 'ग्रुप छोड़ें';

  @override
  String get chatLeaveGroupTitle => 'ग्रुप छोड़ें?';

  @override
  String chatLeaveGroupBody(String name) {
    return 'आपको \"$name\" के संदेश मिलना बंद हो जाएंगे।';
  }

  @override
  String get chatLeave => 'छोड़ें';

  @override
  String chatLeaveFailed(String error) {
    return 'ग्रुप छोड़ नहीं पाए: $error';
  }

  @override
  String get chatDeleteGroup => 'ग्रुप हटाएं';

  @override
  String get chatDeleteGroupTitle => 'ग्रुप हटाएं?';

  @override
  String chatDeleteGroupBody(String name) {
    return '\"$name\" हमेशा के लिए हट जाएगा — सभी सदस्यों के लिए, सभी संदेशों और मीडिया के साथ। इसे वापस नहीं किया जा सकता।';
  }

  @override
  String chatDeleteFailed(String error) {
    return 'हटाया नहीं जा सका: $error';
  }

  @override
  String get chatGroupDeletedByAdmin => 'इस ग्रुप को एडमिन ने हटा दिया है।';

  @override
  String get chatSearchInChat => 'चैट में खोजें';

  @override
  String get chatStudyRoom => 'स्टडी रूम';

  @override
  String get chatAudioCall => 'ऑडियो कॉल';

  @override
  String get chatVideoCall => 'वीडियो कॉल';

  @override
  String chatCallFailed(String error) {
    return 'कॉल नहीं हो पाई: $error';
  }

  @override
  String get chatStudyRoomCardSubtitle =>
      'व्हाइटबोर्ड, टाइमर और लाइव कॉल — सब एक जगह।';

  @override
  String get chatTapToJoin => 'जुड़ने के लिए टैप करें';

  @override
  String get chatCreatePoll => 'पोल बनाएं';

  @override
  String get chatPollQuestion => 'प्रश्न';

  @override
  String get chatAllowMultipleAnswers => 'एक से ज़्यादा उत्तर की अनुमति दें';

  @override
  String get chatPollNeedsQuestionAndTwo =>
      'एक प्रश्न और कम से कम 2 विकल्प ज़रूरी हैं';

  @override
  String get chatSendPoll => 'पोल भेजें';

  @override
  String chatPollVotes(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count वोट',
      one: '1 वोट',
    );
    return '$_temp0';
  }

  @override
  String chatPollVotesClosed(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count वोट',
      one: '1 वोट',
    );
    return '$_temp0 • बंद';
  }

  @override
  String get chatScheduleMessageTitle => 'संदेश शेड्यूल करें';

  @override
  String get chatMessageFieldLabel => 'संदेश';

  @override
  String get chatViewScheduled => 'शेड्यूल किए गए देखें';

  @override
  String get chatPickFutureTime => 'भविष्य का समय चुनें';

  @override
  String get chatMessageScheduled => 'संदेश शेड्यूल हो गया';

  @override
  String get chatScheduledMessagesTitle => 'शेड्यूल किए गए संदेश';

  @override
  String get chatNoScheduled => 'कोई शेड्यूल किया गया संदेश नहीं है';

  @override
  String chatLastSeen(String when) {
    return 'आखिरी बार देखा गया $when';
  }

  @override
  String chatLastSeenToday(String time) {
    return 'आज $time बजे';
  }

  @override
  String chatLastSeenYesterday(String time) {
    return 'कल $time बजे';
  }

  @override
  String chatLastSeenOn(String date) {
    return '$date को';
  }

  @override
  String chatUploadFailed(String error) {
    return 'अपलोड नहीं हो पाया: $error';
  }

  @override
  String chatStickerFailed(String error) {
    return 'स्टिकर नहीं भेजा जा सका: $error';
  }

  @override
  String get chatMicPermission =>
      'वॉइस नोट भेजने के लिए माइक्रोफ़ोन की अनुमति चाहिए';

  @override
  String get chatLocationPermissionDenied => 'लोकेशन की अनुमति नहीं मिली';

  @override
  String chatLocationShareFailed(String error) {
    return 'लोकेशन शेयर नहीं हो पाई: $error';
  }

  @override
  String get chatPhotoGallery => 'फ़ोटो गैलरी';

  @override
  String get chatVideoGallery => 'वीडियो गैलरी';

  @override
  String get chatAudio => 'ऑडियो';

  @override
  String get chatFile => 'फ़ाइल';

  @override
  String get chatPresentation => 'प्रेज़ेंटेशन';

  @override
  String get chatForward => 'फ़ॉरवर्ड करें';

  @override
  String get chatReact => 'रिएक्ट करें';

  @override
  String get chatInfo => 'जानकारी';

  @override
  String get chatSelect => 'चुनें';

  @override
  String get chatSaveToDevice => 'डिवाइस में सेव करें';

  @override
  String get chatDeleteForMe => 'मेरे लिए हटाएं';

  @override
  String get chatDeleteForEveryone => 'सभी के लिए हटाएं';

  @override
  String chatMessageForwarded(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count संदेश फ़ॉरवर्ड हो गए',
      one: 'संदेश फ़ॉरवर्ड हो गया',
    );
    return '$_temp0';
  }

  @override
  String chatSelectedCount(int count) {
    return '$count चुने गए';
  }

  @override
  String get chatEditMessageTitle => 'संदेश संपादित करें';

  @override
  String chatEditFailed(String error) {
    return 'संपादित नहीं हो पाया: $error';
  }

  @override
  String get chatMessageDeleted => 'यह संदेश हटा दिया गया';

  @override
  String get chatAlreadySaved => 'पहले से गैलरी में सेव है ✅';

  @override
  String get chatDownloading => 'डाउनलोड हो रहा है...';

  @override
  String get chatSavedToGallery => 'गैलरी में सेव हो गया ✅';

  @override
  String get chatDownloadedToFolder =>
      'डाउनलोड हो गया ✅ — Download/LearnScroll फ़ोल्डर में';

  @override
  String chatDownloadFailed(String error) {
    return 'डाउनलोड नहीं हो पाया: $error';
  }

  @override
  String get chatSayHi => 'हाय बोलें 👋';

  @override
  String get chatSendToStart => 'बातचीत शुरू करने के लिए संदेश भेजें';

  @override
  String get chatMessageHint => 'संदेश...';

  @override
  String get chatRecording => 'रिकॉर्ड हो रहा है...';

  @override
  String get chatPreviewPhoto => '📷 फ़ोटो';

  @override
  String get chatPreviewVideo => '🎥 वीडियो';

  @override
  String get chatPreviewAudio => '🎵 ऑडियो';

  @override
  String get chatPreviewFile => '📄 फ़ाइल';

  @override
  String get chatPreviewPresentation => '📊 प्रेज़ेंटेशन';

  @override
  String get chatPreviewLocation => '📍 लोकेशन';

  @override
  String get chatPreviewStudyRoom => '🧑‍🎓 स्टडी रूम';

  @override
  String get chatPreviewPoll => '📊 पोल';

  @override
  String get chatToday => 'आज';

  @override
  String get chatYesterday => 'कल';

  @override
  String get chatAnnouncement => 'घोषणा';

  @override
  String chatUploadingPercent(int percent) {
    return '$percent% अपलोड हो रहा है...';
  }

  @override
  String chatUploadingPhotosPercent(int percent, int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count फ़ोटो',
      one: '1 फ़ोटो',
    );
    return '$percent% • $_temp0';
  }

  @override
  String chatPhotosCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count फ़ोटो',
      one: '1 फ़ोटो',
    );
    return '$_temp0';
  }

  @override
  String get chatLocationShared => 'लोकेशन शेयर की गई';

  @override
  String chatItemsCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count आइटम',
      one: '1 आइटम',
    );
    return '$_temp0';
  }

  @override
  String get chatSendingAudio => 'ऑडियो भेजा जा रहा है...';

  @override
  String get chatAudioMessage => 'ऑडियो संदेश';

  @override
  String chatAudioPlayFailed(String error) {
    return 'ऑडियो नहीं चल पाया: $error';
  }

  @override
  String get chatTranscribe => 'ट्रांसक्राइब करें';

  @override
  String get chatTranscribing => 'ट्रांसक्राइब हो रहा है...';

  @override
  String get chatTranscript => 'ट्रांसक्रिप्ट';

  @override
  String chatTranscribeFailed(String error) {
    return 'ट्रांसक्राइब नहीं हो पाया: $error';
  }

  @override
  String chatVideoLoadFailed(String error) {
    return 'वीडियो लोड नहीं हो पाया: $error';
  }

  @override
  String get parentModeTitle => 'पैरेंट मोड';

  @override
  String get parentEntryHeading => 'अपने बच्चे की प्रगति देखें';

  @override
  String get parentEntryBody =>
      'आपको सिर्फ़ उपस्थिति और असाइनमेंट की स्थिति दिखेगी — कोई चैट संदेश नहीं। आपके बच्चे ने जो कोड दिया है, वह नीचे डालें।';

  @override
  String get parentEntryCodeHint => 'जैसे: 7F3K9QRT';

  @override
  String get parentEntryCodeRequired => 'कोड डालें';

  @override
  String get parentEntryViewProgress => 'प्रगति देखें';

  @override
  String get parentLoginLink => 'पैरेंट/गार्जियन? अपने बच्चे की प्रगति देखें';

  @override
  String get parentErrInvalidCode =>
      'कोड अमान्य है या समाप्त हो चुका है। कृपया दोबारा जाँचें।';

  @override
  String get parentErrTooManyAttempts =>
      'बहुत ज़्यादा प्रयास हो गए — थोड़ी देर बाद फिर कोशिश करें।';

  @override
  String get parentErrGeneric => 'कुछ गलत हो गया। कृपया फिर से प्रयास करें।';

  @override
  String get parentErrSessionExpired =>
      'आपका सेशन समाप्त हो गया है। कोड दोबारा डालें।';

  @override
  String get parentErrAccessRevoked =>
      'एक्सेस हटा दिया गया है। अपने बच्चे से नया कोड लें।';

  @override
  String get parentErrDashboardLoad =>
      'डैशबोर्ड लोड नहीं हो पाया। कृपया फिर से प्रयास करें।';

  @override
  String get parentDashNoClassrooms => 'अभी कोई क्लासरूम नहीं मिला।';

  @override
  String get parentDashSubtitle =>
      'उपस्थिति और असाइनमेंट की स्थिति — चैट की सामग्री यहाँ कभी नहीं दिखेगी।';

  @override
  String get parentDashStreak => 'मौजूदा स्ट्रीक';

  @override
  String parentDashStreakDays(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count दिन',
      one: '1 दिन',
    );
    return '$_temp0';
  }

  @override
  String get parentDashTotalClasses => 'कुल क्लास';

  @override
  String get parentDashAssignmentsPending => 'बाकी असाइनमेंट';

  @override
  String get parentDashSubmitted => 'जमा किए';

  @override
  String get parentDashSignOut => 'साइन आउट';

  @override
  String get parentDashEnterNewCode => 'नया कोड डालें';

  @override
  String get focusModeTitle => 'फ़ोकस मोड';

  @override
  String get focusModeHistoryTooltip => 'इतिहास';

  @override
  String get focusModeChangeDuration => 'अवधि बदलें';

  @override
  String get focusModeHowLong => 'कितनी देर के लिए?';

  @override
  String get focusModeCustomDuration => 'अपनी अवधि चुनें';

  @override
  String get focusModeWhoCanReach => 'अब भी कौन आपसे संपर्क कर सकता है?';

  @override
  String get focusModeRuleTeachersTitle => 'सिर्फ़ शिक्षक और स्टाफ़';

  @override
  String get focusModeRuleTeachersSub =>
      'ग्रुप एडमिन/मॉडरेटर के मैसेज और कॉल आएँगे, बाकी सब साइलेंट रहेंगे।';

  @override
  String get focusModeRuleNobodyTitle => 'कोई नहीं — पूरी शांति';

  @override
  String get focusModeRuleNobodySub =>
      'परीक्षा के समय के लिए — कोई भी नोटिफ़िकेशन नहीं आएगा, शिक्षक का भी नहीं।';

  @override
  String get focusModeUpdate => 'फ़ोकस मोड अपडेट करें';

  @override
  String get focusModeStart => 'फ़ोकस मोड शुरू करें';

  @override
  String get focusModeEndNow => 'फ़ोकस मोड अभी खत्म करें';

  @override
  String focusModeActiveLeft(String time) {
    return 'फ़ोकस मोड चालू है — $time बाकी';
  }

  @override
  String get focusModeFailed =>
      'फ़ोकस मोड अपडेट नहीं हो पाया। कृपया फिर से प्रयास करें।';

  @override
  String get focusModeSet => 'सेट करें';

  @override
  String get focusModeHours => 'घंटे';

  @override
  String get focusModeMinutes => 'मिनट';

  @override
  String get chatTyping => 'टाइप कर रहा है...';

  @override
  String get chatOnline => 'ऑनलाइन';

  @override
  String get enterClassCta => 'क्लास में जाएँ';

  @override
  String get requestPendingCta => 'अनुरोध लंबित है';

  @override
  String get endClassCta => 'क्लास समाप्त करें';

  @override
  String get endClassConfirm => 'क्या सभी के लिए यह क्लास समाप्त करें?';

  @override
  String get removedFromSession => 'आपको इस सेशन से हटा दिया गया है।';

  @override
  String get waitlistedNotice =>
      'क्लास भरी हुई है — आप वेटलिस्ट में हैं और अपने-आप जोड़ लिए जाएँगे।';

  @override
  String get waitingForTeacher => 'शिक्षक के वीडियो शुरू करने का इंतज़ार…';

  @override
  String get savedPostsTitle => 'सेव किए गए पोस्ट';

  @override
  String get explorePostsTitle => 'एक्सप्लोर';

  @override
  String get noPostsHere => 'यहाँ अभी कोई पोस्ट नहीं है';

  @override
  String get postsLoadFailed => 'पोस्ट लोड नहीं हो सके';

  @override
  String get deletePostCta => 'पोस्ट हटाएँ';

  @override
  String get deletePostConfirm =>
      'यह पोस्ट हटाएँ? इसे वापस नहीं लाया जा सकेगा।';

  @override
  String get postDeleted => 'पोस्ट हटा दिया गया';

  @override
  String get postDeleteFailed => 'पोस्ट नहीं हटाया जा सका';

  @override
  String get allCaughtUp => 'आप सब कुछ देख चुके हैं';

  @override
  String get back => 'वापस';

  @override
  String get bioLabel => 'बायो';

  @override
  String get changePhotoTooltip => 'फोटो बदलें';

  @override
  String get discardChangesTitle => 'बदलाव छोड़ें?';

  @override
  String get discardChangesMessage =>
      'आपके बदलाव सेव नहीं हुए हैं। क्या आप वाकई वापस जाना चाहते हैं?';

  @override
  String get downloading => 'डाउनलोड हो रहा है…';

  @override
  String get editProfileButton => 'प्रोफ़ाइल संपादित करें';

  @override
  String get follow => 'फ़ॉलो करें';

  @override
  String get followBack => 'वापस फ़ॉलो करें';

  @override
  String get followersStat => 'फ़ॉलोअर्स';

  @override
  String get messageButton => 'संदेश';

  @override
  String get moreOptions => 'और विकल्प';

  @override
  String mediaTabLabel(int count) {
    return 'मीडिया ($count)';
  }

  @override
  String documentsTabLabel(int count) {
    return 'दस्तावेज़ ($count)';
  }

  @override
  String get noDocumentsYetTitle => 'अभी कोई दस्तावेज़ नहीं';

  @override
  String get noDocumentsYetSubtitle => 'आपके साझा किए दस्तावेज़ यहाँ दिखेंगे।';

  @override
  String get noMediaYetTitle => 'अभी कोई मीडिया नहीं';

  @override
  String get noMediaYetSubtitle =>
      'आपके पोस्ट किए फोटो और वीडियो यहाँ दिखेंगे।';

  @override
  String get noNameYet => 'अभी कोई नाम नहीं';

  @override
  String get noProfileFound => 'प्रोफ़ाइल नहीं मिली';

  @override
  String get photoPostLabel => 'फोटो';

  @override
  String get videoPostLabel => 'वीडियो';

  @override
  String get postsLoadErrorTitle => 'पोस्ट लोड नहीं हो सके';

  @override
  String get postsLoadErrorSubtitle =>
      'अपना कनेक्शन जाँचें और दोबारा कोशिश के लिए नीचे खींचें।';

  @override
  String get postsStat => 'पोस्ट';

  @override
  String get privateAccountBadge => 'निजी';

  @override
  String get privateAccountMessage =>
      'यह खाता निजी है। पोस्ट देखने के लिए फ़ॉलो करें।';

  @override
  String get privateAccountPendingMessage =>
      'फ़ॉलो अनुरोध भेजा गया। पोस्ट देखने के लिए स्वीकृति का इंतज़ार करें।';

  @override
  String get profileLoadErrorTitle => 'प्रोफ़ाइल लोड नहीं हो सकी';

  @override
  String get profileUpdatedSuccess => 'प्रोफ़ाइल सफलतापूर्वक अपडेट हुई';

  @override
  String get requestedLabel => 'अनुरोध भेजा';

  @override
  String get shareProfileButton => 'प्रोफ़ाइल साझा करें';

  @override
  String shareProfileMessage(String username, String url) {
    return 'LearnScroll पर $username को देखें: $url';
  }

  @override
  String get showingSavedProfileData =>
      'सेव किया हुआ डेटा दिख रहा है — रीफ़्रेश के लिए नीचे खींचें';

  @override
  String get verifiedAccount => 'सत्यापित खाता';

  @override
  String coinsBalance(num coins) {
    return '$coins कॉइन';
  }

  @override
  String chatOpenFailed(String error) {
    return 'चैट नहीं खुल सकी: $error';
  }

  @override
  String get analyticsTitle => 'एनालिटिक्स';

  @override
  String get analyticsActiveEnrollments => 'सक्रिय नामांकन';

  @override
  String get analyticsAvgAttendance => 'औसत उपस्थिति';

  @override
  String get analyticsAvgMarks => 'औसत अंक';

  @override
  String get analyticsSyllabusCompletion => 'पाठ्यक्रम पूर्णता';

  @override
  String get analyticsNoSnapshot => 'अभी कोई एनालिटिक्स नहीं';

  @override
  String get analyticsNoSnapshotHint =>
      'एनालिटिक्स समय-समय पर तैयार होते हैं। बाद में देखें।';

  @override
  String analyticsComputedAt(String date) {
    return 'तैयार किया गया $date';
  }

  @override
  String get assignmentDueDateLabel => 'अंतिम तिथि';

  @override
  String get assignmentFeedbackLabel => 'फ़ीडबैक';

  @override
  String get assignmentGradeLabel => 'ग्रेड';

  @override
  String get assignmentNoSubmissions => 'अभी कोई सबमिशन नहीं';

  @override
  String get assignmentNotSubmitted => 'जमा नहीं किया';

  @override
  String get assignmentSubmitCta => 'जमा करें';

  @override
  String get assignmentsAdd => 'असाइनमेंट जोड़ें';

  @override
  String get assignmentsEmpty => 'अभी कोई असाइनमेंट नहीं';

  @override
  String get campusCreateTitle => 'कैंपस बनाएँ';

  @override
  String get campusCreateNameLabel => 'कैंपस का नाम';

  @override
  String get campusCreateNameRequired => 'कैंपस का नाम दर्ज करें';

  @override
  String get campusCreateTypeLabel => 'कैंपस का प्रकार';

  @override
  String get campusCreateTypeSchool => 'स्कूल';

  @override
  String get campusCreateTypeCollege => 'कॉलेज';

  @override
  String get campusCreateTypeCoaching => 'कोचिंग';

  @override
  String get campusCreateThresholdLabel => 'उपस्थिति सीमा';

  @override
  String get campusCreateThresholdHint =>
      'इस प्रतिशत से कम उपस्थिति वाले विद्यार्थियों को चिह्नित किया जाएगा।';

  @override
  String get campusCreateFeeModuleLabel => 'फ़ीस मॉड्यूल चालू करें';

  @override
  String get campusCreateFeeModuleHint =>
      'इस कैंपस की फ़ीस संरचना, इनवॉइस और भुगतान ट्रैक करें।';

  @override
  String get campusCreateSubmit => 'बनाएँ';

  @override
  String get campusCreateSuccess => 'कैंपस बन गया';

  @override
  String get campusManageSetupCta => 'सेटअप प्रबंधित करें';

  @override
  String get campusNoneCreateCta => 'कैंपस बनाएँ';

  @override
  String get campusSetupTitle => 'कैंपस सेटअप';

  @override
  String get classDepartmentLabel => 'विभाग';

  @override
  String get classDepartmentNone => 'कोई विभाग नहीं';

  @override
  String get classNameHint => 'जैसे कक्षा 10';

  @override
  String get classNameLabel => 'कक्षा का नाम';

  @override
  String get classNoSessionsError => 'पहले एक शैक्षणिक सत्र बनाएँ';

  @override
  String get classSectionsCta => 'सेक्शन';

  @override
  String get classSessionLabel => 'शैक्षणिक सत्र';

  @override
  String get classTeacherAssign => 'कक्षा शिक्षक नियुक्त करें';

  @override
  String get classTeacherAssignedSuccess => 'कक्षा शिक्षक नियुक्त हुआ';

  @override
  String get classTeacherNotAssigned => 'कोई कक्षा शिक्षक नियुक्त नहीं';

  @override
  String get classTeacherTitle => 'कक्षा शिक्षक';

  @override
  String get departmentNameLabel => 'विभाग का नाम';

  @override
  String get examTermNameLabel => 'परीक्षा अवधि का नाम';

  @override
  String get examTermPickLabel => 'परीक्षा अवधि';

  @override
  String sectionDetailTitle(String className, String sectionName) {
    return '$className · $sectionName';
  }

  @override
  String get sectionNameHint => 'जैसे A';

  @override
  String get sectionNameLabel => 'सेक्शन का नाम';

  @override
  String get sessionCurrentBadge => 'वर्तमान';

  @override
  String get sessionDateOrderError =>
      'समाप्ति तिथि आरंभ तिथि के बाद होनी चाहिए';

  @override
  String get sessionDatesRequiredError => 'आरंभ और समाप्ति तिथि चुनें';

  @override
  String get sessionEndDateLabel => 'समाप्ति तिथि';

  @override
  String get sessionNameHint => 'जैसे 2026–27';

  @override
  String get sessionNameLabel => 'सत्र का नाम';

  @override
  String get sessionSetCurrentLabel => 'वर्तमान बनाएँ';

  @override
  String sessionSetCurrentSuccess(String name) {
    return '$name अब वर्तमान सत्र है';
  }

  @override
  String get sessionStartDateLabel => 'आरंभ तिथि';

  @override
  String get setupClassesAdd => 'कक्षा जोड़ें';

  @override
  String get setupClassesEmpty => 'अभी कोई कक्षा नहीं';

  @override
  String get setupClassesTitle => 'कक्षाएँ';

  @override
  String get setupDepartmentsAdd => 'विभाग जोड़ें';

  @override
  String get setupDepartmentsEmpty => 'अभी कोई विभाग नहीं';

  @override
  String get setupDepartmentsTitle => 'विभाग';

  @override
  String get setupExamTermsAdd => 'परीक्षा अवधि जोड़ें';

  @override
  String get setupExamTermsEmpty => 'अभी कोई परीक्षा अवधि नहीं';

  @override
  String get setupExamTermsTitle => 'परीक्षा अवधियाँ';

  @override
  String get setupFeeStructuresAdd => 'फ़ीस संरचना जोड़ें';

  @override
  String get setupFeeStructuresEmpty => 'अभी कोई फ़ीस संरचना नहीं';

  @override
  String get setupFeeStructuresTitle => 'फ़ीस संरचनाएँ';

  @override
  String get setupLoadFailed => 'यह सूची लोड नहीं हो सकी';

  @override
  String get setupRoomsAdd => 'कमरा जोड़ें';

  @override
  String get setupRoomsEmpty => 'अभी कोई कमरा नहीं';

  @override
  String get setupRoomsTitle => 'कमरे';

  @override
  String get setupSectionsAdd => 'सेक्शन जोड़ें';

  @override
  String get setupSectionsEmpty => 'अभी कोई सेक्शन नहीं';

  @override
  String setupSectionsTitle(String className) {
    return 'सेक्शन · $className';
  }

  @override
  String get setupSessionsAdd => 'सत्र जोड़ें';

  @override
  String get setupSessionsEmpty => 'अभी कोई सत्र नहीं';

  @override
  String get setupSessionsTitle => 'शैक्षणिक सत्र';

  @override
  String get setupStaffAdd => 'स्टाफ़ जोड़ें';

  @override
  String get setupStaffEmpty => 'अभी कोई स्टाफ़ नहीं';

  @override
  String get setupStaffPendingApproval =>
      'आपके जोड़े स्टाफ़ को सक्रिय दिखने से पहले उपयोगकर्ता की स्वीकृति चाहिए हो सकती है।';

  @override
  String get setupStaffTitle => 'स्टाफ़';

  @override
  String get setupSubjectsAdd => 'विषय जोड़ें';

  @override
  String get setupSubjectsEmpty => 'अभी कोई विषय नहीं';

  @override
  String get setupSubjectsTitle => 'विषय';

  @override
  String get roomNameLabel => 'कमरे का नाम';

  @override
  String get roomVirtualHint =>
      'वर्चुअल कमरे ऑनलाइन कक्षाएँ हैं जिनका कोई भौतिक स्थान नहीं होता।';

  @override
  String get roomVirtualLabel => 'वर्चुअल';

  @override
  String get staffPickLabel => 'स्टाफ़ सदस्य';

  @override
  String get staffRoleLabel => 'भूमिका';

  @override
  String get staffUserIdHint => 'उपयोगकर्ता की ID दर्ज करें';

  @override
  String get staffUserIdLabel => 'उपयोगकर्ता ID';

  @override
  String get subjectCodeLabel => 'विषय कोड';

  @override
  String get subjectDepartmentLabel => 'विभाग';

  @override
  String get subjectNameLabel => 'विषय का नाम';

  @override
  String get subjectPickLabel => 'विषय';

  @override
  String get subjectTeacherApprove => 'स्वीकृत करें';

  @override
  String get subjectTeacherApproved => 'स्वीकृत';

  @override
  String get subjectTeacherAssign => 'विषय शिक्षक नियुक्त करें';

  @override
  String get subjectTeacherReject => 'अस्वीकार करें';

  @override
  String get subjectTeacherRejected => 'अस्वीकृत';

  @override
  String get subjectTeacherRequest => 'नियुक्ति का अनुरोध करें';

  @override
  String get subjectTeacherRequested => 'अनुरोध भेजा गया — स्वीकृति का इंतज़ार';

  @override
  String get subjectTeacherStatusApproved => 'स्वीकृत';

  @override
  String get subjectTeacherStatusPending => 'लंबित';

  @override
  String get subjectTeacherStatusRejected => 'अस्वीकृत';

  @override
  String get subjectTeachersEmpty => 'अभी कोई विषय शिक्षक नहीं';

  @override
  String get subjectTeachersTitle => 'विषय शिक्षक';

  @override
  String get enrollRollNumberLabel => 'रोल नंबर';

  @override
  String get enrollStatusActive => 'सक्रिय';

  @override
  String get enrollStatusGraduated => 'उत्तीर्ण';

  @override
  String get enrollStatusLabel => 'स्थिति';

  @override
  String get enrollStatusTransferred => 'स्थानांतरित';

  @override
  String get enrollStudentIdHint => 'विद्यार्थी की उपयोगकर्ता ID दर्ज करें';

  @override
  String get enrollStudentIdLabel => 'विद्यार्थी ID';

  @override
  String get enrollmentAddedSuccess => 'विद्यार्थी का नामांकन हो गया';

  @override
  String get enrollmentsAdd => 'विद्यार्थी नामांकित करें';

  @override
  String get enrollmentsEmpty => 'अभी कोई विद्यार्थी नामांकित नहीं';

  @override
  String get enrollmentsTitle => 'नामांकन';

  @override
  String get feeAmountDueLabel => 'बकाया';

  @override
  String get feeAmountLabel => 'राशि';

  @override
  String get feeAmountPaidLabel => 'भुगतान किया';

  @override
  String get feeCampusWide => 'पूरे कैंपस के लिए';

  @override
  String get feeGenerateInvoices => 'इनवॉइस बनाएँ';

  @override
  String feeInsufficientCoinsBody(num coins) {
    return 'यह फ़ीस भरने के लिए आपको $coins कॉइन चाहिए। वॉलेट में कॉइन जोड़ें और दोबारा कोशिश करें।';
  }

  @override
  String get feeInsufficientCoinsTitle => 'पर्याप्त कॉइन नहीं';

  @override
  String feeInvoicesGenerated(int created, int existing) {
    return '$created इनवॉइस बने, $existing पहले से मौजूद थे';
  }

  @override
  String get feeModuleDisabledNote => 'इस कैंपस के लिए फ़ीस मॉड्यूल बंद है।';

  @override
  String get feeNoPayments => 'कोई भुगतान दर्ज नहीं';

  @override
  String get feeNotesLabel => 'टिप्पणियाँ';

  @override
  String get feeOfficeTitle => 'फ़ीस कार्यालय';

  @override
  String get feePayCta => 'कॉइन से भुगतान करें';

  @override
  String get feePaySuccess => 'भुगतान सफल रहा';

  @override
  String get feePaymentHistory => 'भुगतान इतिहास';

  @override
  String get feePaymentModeLabel => 'भुगतान का तरीका';

  @override
  String get feeRecordPayment => 'भुगतान दर्ज करें';

  @override
  String get feeRefund => 'रिफ़ंड';

  @override
  String get feeStatusOverdue => 'अतिदेय';

  @override
  String get feeStatusPaid => 'भुगतान हो गया';

  @override
  String get feeStatusPartial => 'आंशिक भुगतान';

  @override
  String get feeStatusPending => 'लंबित';

  @override
  String get feeStatusWaived => 'माफ़';

  @override
  String get feeTitleLabel => 'फ़ीस का शीर्षक';

  @override
  String get feeWalletCoinsNote =>
      'फ़ीस आपके LearnScroll कॉइन वॉलेट से भरी जाती है।';

  @override
  String get myFeesEmpty => 'दिखाने के लिए कोई फ़ीस नहीं';

  @override
  String get myFeesTitle => 'मेरी फ़ीस';

  @override
  String get idCardTitle => 'डिजिटल आईडी कार्ड';

  @override
  String get idCardAllIssued => 'सभी के पास आईडी कार्ड है';

  @override
  String get idCardIssueForOthers => 'दूसरों के लिए जारी करें';

  @override
  String get idCardIssueOwn => 'मेरा आईडी कार्ड जारी करें';

  @override
  String get idCardIssuedOn => 'जारी होने की तिथि';

  @override
  String get idCardNotIssued => 'अभी कोई आईडी कार्ड जारी नहीं हुआ';

  @override
  String get idCardValidUntil => 'मान्य तिथि तक';

  @override
  String get parentLinkCampusIdHint => 'स्कूल द्वारा दी गई कैंपस ID';

  @override
  String get parentLinkCampusIdLabel => 'कैंपस ID';

  @override
  String get parentLinkLoadFailed => 'जुड़े हुए बच्चे लोड नहीं हो सके';

  @override
  String get parentLinkMyChildrenTitle => 'मेरे बच्चे';

  @override
  String get parentLinkNoChildren => 'अभी कोई बच्चा जुड़ा नहीं है';

  @override
  String get parentLinkScreenTitle => 'बच्चे को जोड़ें';

  @override
  String get parentLinkSubmit => 'जोड़ें';

  @override
  String get parentLinkSuccess => 'बच्चा सफलतापूर्वक जुड़ गया';

  @override
  String get parentLinkTokenHint => 'स्कूल से मिला सत्यापन कोड';

  @override
  String get parentLinkTokenLabel => 'सत्यापन कोड';

  @override
  String get parentLinkVerifyHint =>
      'अपने बच्चे को जोड़ने के लिए कैंपस ID और मिला हुआ कोड दर्ज करें।';

  @override
  String get parentLinkVerifyTitle => 'सत्यापित करें और जोड़ें';

  @override
  String parentLinkedOn(String date) {
    return '$date को जोड़ा गया';
  }

  @override
  String get parentOverviewNoEnrollment =>
      'यह बच्चा अभी इस कैंपस के किसी सक्रिय सेक्शन में नामांकित नहीं है।';

  @override
  String get parentOverviewReportCard => 'रिपोर्ट कार्ड';

  @override
  String get parentOverviewNoNotices =>
      'इस क्लास के लिए अभी कोई नोटिस नहीं है।';

  @override
  String get parentOverviewLoadFailed => 'बच्चे की जानकारी लोड नहीं हो पाई';

  @override
  String get reportCardPercentage => 'प्रतिशत';

  @override
  String get reportCardTitle => 'रिपोर्ट कार्ड';

  @override
  String get reportCardView => 'रिपोर्ट कार्ड';

  @override
  String get resultMarksInvalid => 'सही अंक दर्ज करें';

  @override
  String get resultMaxLabel => 'अधिकतम';

  @override
  String get resultObtainedLabel => 'प्राप्त';

  @override
  String get resultsManage => 'परिणाम दर्ज करें';

  @override
  String get resultsTitle => 'परिणाम';

  @override
  String get syllabusAdd => 'इकाई जोड़ें';

  @override
  String syllabusCoveredOn(String date) {
    return '$date को पूरा किया';
  }

  @override
  String get syllabusEmpty => 'अभी कोई पाठ्यक्रम इकाई नहीं';

  @override
  String get syllabusMarkCovered => 'पूरा हुआ चिह्नित करें';

  @override
  String get syllabusTitle => 'पाठ्यक्रम';

  @override
  String get syllabusUnitOrderLabel => 'क्रम';

  @override
  String get syllabusUnitTitleLabel => 'इकाई का शीर्षक';

  @override
  String get noUsersHere => 'यहाँ अभी कोई नहीं है';

  @override
  String get usersLoadFailed => 'सूची लोड नहीं हो सकी';

  @override
  String mutualFriendsCount(int count) {
    return '$count साझा';
  }
}
