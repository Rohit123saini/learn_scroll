import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_en.dart';
import 'app_localizations_hi.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of AppLocalizations
/// returned by `AppLocalizations.of(context)`.
///
/// Applications need to include `AppLocalizations.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'l10n/app_localizations.dart';
///
/// return MaterialApp(
///   localizationsDelegates: AppLocalizations.localizationsDelegates,
///   supportedLocales: AppLocalizations.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the AppLocalizations.supportedLocales
/// property.
abstract class AppLocalizations {
  AppLocalizations(String locale)
    : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static AppLocalizations of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations)!;
  }

  static const LocalizationsDelegate<AppLocalizations> delegate =
      _AppLocalizationsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
        delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
      ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[
    Locale('en'),
    Locale('hi'),
  ];

  /// App name, shown in OS task switcher etc.
  ///
  /// In en, this message translates to:
  /// **'LearnScroll'**
  String get appTitle;

  /// Home screen invite banner title
  ///
  /// In en, this message translates to:
  /// **'Invite & Earn'**
  String get inviteEarn;

  /// Home screen invite banner subtitle
  ///
  /// In en, this message translates to:
  /// **'Invite friends and earn rewards'**
  String get inviteEarnSub;

  /// Home screen invite banner subtitle with coins count
  ///
  /// In en, this message translates to:
  /// **'Invite friends and earn {coins} coins'**
  String inviteEarnSubCoins(int coins);

  /// Invite button text
  ///
  /// In en, this message translates to:
  /// **'Invite'**
  String get inviteCta;

  /// Placeholder text in the home screen search bar
  ///
  /// In en, this message translates to:
  /// **'Search courses, tests, notices...'**
  String get searchHint;

  /// Section header for live classes/streams
  ///
  /// In en, this message translates to:
  /// **'Live Now'**
  String get liveNow;

  /// Badge text for live stream
  ///
  /// In en, this message translates to:
  /// **'LIVE'**
  String get liveBadge;

  /// Subtitle for live class card
  ///
  /// In en, this message translates to:
  /// **'{teacher} · {viewers} watching'**
  String liveNowCardSubtitle(String teacher, int viewers);

  /// Link to view the full list of a section
  ///
  /// In en, this message translates to:
  /// **'See all'**
  String get seeAll;

  /// Button to join a live class/session
  ///
  /// In en, this message translates to:
  /// **'Join'**
  String get join;

  /// Home feed tab label
  ///
  /// In en, this message translates to:
  /// **'Your Feed'**
  String get yourFeed;

  /// Feed tab label for followed creators/content
  ///
  /// In en, this message translates to:
  /// **'Following'**
  String get following;

  /// Button/section to add friends
  ///
  /// In en, this message translates to:
  /// **'Add Friends'**
  String get addFriends;

  /// Button to start/host a class
  ///
  /// In en, this message translates to:
  /// **'Start Class'**
  String get startClass;

  /// Button to start a test series
  ///
  /// In en, this message translates to:
  /// **'Start Test Series'**
  String get startTestSeries;

  /// Section/nav label for assignments
  ///
  /// In en, this message translates to:
  /// **'Assignments'**
  String get assignments;

  /// Section/nav label for test series
  ///
  /// In en, this message translates to:
  /// **'Test Series'**
  String get testSeries;

  /// Section/nav label for notices
  ///
  /// In en, this message translates to:
  /// **'Notices'**
  String get notices;

  /// Section/nav label for wallet
  ///
  /// In en, this message translates to:
  /// **'Wallet'**
  String get wallet;

  /// Section label for user classrooms
  ///
  /// In en, this message translates to:
  /// **'Your Classrooms'**
  String get yourClassrooms;

  /// Chip label to join classrooms
  ///
  /// In en, this message translates to:
  /// **'+ Join'**
  String get joinClassroom;

  /// Title for interstitial widget
  ///
  /// In en, this message translates to:
  /// **'Jump Back In'**
  String get jumpBackIn;

  /// Tooltip for theme toggle
  ///
  /// In en, this message translates to:
  /// **'Toggle dark mode'**
  String get themeToggleTooltip;

  /// Tooltip for language toggle
  ///
  /// In en, this message translates to:
  /// **'Change language'**
  String get languageToggleTooltip;

  /// Tooltip for notifications icon
  ///
  /// In en, this message translates to:
  /// **'Notifications'**
  String get notificationsTooltip;

  /// Tooltip for compose icon
  ///
  /// In en, this message translates to:
  /// **'Create new post'**
  String get newPostTooltip;

  /// Bottom nav label
  ///
  /// In en, this message translates to:
  /// **'Home'**
  String get navHome;

  /// Bottom nav label
  ///
  /// In en, this message translates to:
  /// **'Discover'**
  String get navDiscover;

  /// Bottom nav label (center action button)
  ///
  /// In en, this message translates to:
  /// **'Create'**
  String get navCreate;

  /// Bottom nav label
  ///
  /// In en, this message translates to:
  /// **'Alerts'**
  String get navNotifications;

  /// Bottom nav label
  ///
  /// In en, this message translates to:
  /// **'Profile'**
  String get navProfile;

  /// No description provided for @save.
  ///
  /// In en, this message translates to:
  /// **'Save'**
  String get save;

  /// No description provided for @saveFailed.
  ///
  /// In en, this message translates to:
  /// **'Failed to update bookmark state'**
  String get saveFailed;

  /// No description provided for @comment.
  ///
  /// In en, this message translates to:
  /// **'Comment'**
  String get comment;

  /// No description provided for @share.
  ///
  /// In en, this message translates to:
  /// **'Share'**
  String get share;

  /// Number of comments
  ///
  /// In en, this message translates to:
  /// **'{count} comments'**
  String commentsCount(int count);

  /// No description provided for @sessionExpiredRedirecting.
  ///
  /// In en, this message translates to:
  /// **'Session expired. Redirecting to login...'**
  String get sessionExpiredRedirecting;

  /// No description provided for @feedErrorTitle.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load feed'**
  String get feedErrorTitle;

  /// No description provided for @feedErrorSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Check your internet connection and try again.'**
  String get feedErrorSubtitle;

  /// No description provided for @retry.
  ///
  /// In en, this message translates to:
  /// **'Retry'**
  String get retry;

  /// No description provided for @feedEmptyTitle.
  ///
  /// In en, this message translates to:
  /// **'No posts yet'**
  String get feedEmptyTitle;

  /// No description provided for @feedEmptySubtitle.
  ///
  /// In en, this message translates to:
  /// **'Follow creators or search subjects to fill your feed.'**
  String get feedEmptySubtitle;

  /// Fallback text for unhandled options
  ///
  /// In en, this message translates to:
  /// **'{feature} is coming soon!'**
  String featureComingSoon(String feature);

  /// No description provided for @authOr.
  ///
  /// In en, this message translates to:
  /// **'OR'**
  String get authOr;

  /// No description provided for @authGoogleCredentialsFailed.
  ///
  /// In en, this message translates to:
  /// **'Could not get Google credentials. Please try again.'**
  String get authGoogleCredentialsFailed;

  /// No description provided for @authGoogleSignedIn.
  ///
  /// In en, this message translates to:
  /// **'Signed in with Google'**
  String get authGoogleSignedIn;

  /// No description provided for @authGoogleSignInFailed.
  ///
  /// In en, this message translates to:
  /// **'Google sign-in failed. Please try again.'**
  String get authGoogleSignInFailed;

  /// No description provided for @loginSuccessful.
  ///
  /// In en, this message translates to:
  /// **'Login Successful!'**
  String get loginSuccessful;

  /// No description provided for @loginWelcomeBack.
  ///
  /// In en, this message translates to:
  /// **'Welcome Back'**
  String get loginWelcomeBack;

  /// No description provided for @loginSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Sign in to continue your journey'**
  String get loginSubtitle;

  /// No description provided for @loginUsernameOrEmail.
  ///
  /// In en, this message translates to:
  /// **'Username or Email'**
  String get loginUsernameOrEmail;

  /// No description provided for @loginUsernameRequired.
  ///
  /// In en, this message translates to:
  /// **'Username or Email is required'**
  String get loginUsernameRequired;

  /// No description provided for @loginPassword.
  ///
  /// In en, this message translates to:
  /// **'Password'**
  String get loginPassword;

  /// No description provided for @loginPasswordRequired.
  ///
  /// In en, this message translates to:
  /// **'Password is required'**
  String get loginPasswordRequired;

  /// No description provided for @loginForgotPassword.
  ///
  /// In en, this message translates to:
  /// **'Forgot Password?'**
  String get loginForgotPassword;

  /// No description provided for @loginSignIn.
  ///
  /// In en, this message translates to:
  /// **'Sign In'**
  String get loginSignIn;

  /// No description provided for @loginContinueWithGoogle.
  ///
  /// In en, this message translates to:
  /// **'Continue with Google'**
  String get loginContinueWithGoogle;

  /// No description provided for @loginNoAccount.
  ///
  /// In en, this message translates to:
  /// **'Don\'t have an account? '**
  String get loginNoAccount;

  /// No description provided for @loginSignUp.
  ///
  /// In en, this message translates to:
  /// **'Sign Up'**
  String get loginSignUp;

  /// No description provided for @signupSuccessful.
  ///
  /// In en, this message translates to:
  /// **'Signup Successful!'**
  String get signupSuccessful;

  /// No description provided for @signupUsernameExists.
  ///
  /// In en, this message translates to:
  /// **'User already exists with this username'**
  String get signupUsernameExists;

  /// No description provided for @signupEmailExists.
  ///
  /// In en, this message translates to:
  /// **'Account already exists with this email'**
  String get signupEmailExists;

  /// No description provided for @signupGoogleSuccessful.
  ///
  /// In en, this message translates to:
  /// **'Account created with Google'**
  String get signupGoogleSuccessful;

  /// No description provided for @signupVerifyEmail.
  ///
  /// In en, this message translates to:
  /// **'Verify Email Address'**
  String get signupVerifyEmail;

  /// No description provided for @signupOtpSentTo.
  ///
  /// In en, this message translates to:
  /// **'We have sent a verification code to'**
  String get signupOtpSentTo;

  /// No description provided for @signupOtpInvalid.
  ///
  /// In en, this message translates to:
  /// **'Please enter a valid OTP'**
  String get signupOtpInvalid;

  /// No description provided for @signupVerifyAndCreate.
  ///
  /// In en, this message translates to:
  /// **'Verify & Create Account'**
  String get signupVerifyAndCreate;

  /// No description provided for @signupCreateAccount.
  ///
  /// In en, this message translates to:
  /// **'Create Account'**
  String get signupCreateAccount;

  /// No description provided for @signupSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Sign up to get started with LearnScroll'**
  String get signupSubtitle;

  /// No description provided for @signupWithGoogle.
  ///
  /// In en, this message translates to:
  /// **'Sign up with Google'**
  String get signupWithGoogle;

  /// No description provided for @signupOrEmail.
  ///
  /// In en, this message translates to:
  /// **'OR SIGN UP WITH EMAIL'**
  String get signupOrEmail;

  /// No description provided for @signupUsername.
  ///
  /// In en, this message translates to:
  /// **'Username'**
  String get signupUsername;

  /// No description provided for @signupUsernameRequired.
  ///
  /// In en, this message translates to:
  /// **'Username is required'**
  String get signupUsernameRequired;

  /// No description provided for @signupEmail.
  ///
  /// In en, this message translates to:
  /// **'Email Address'**
  String get signupEmail;

  /// No description provided for @signupEmailRequired.
  ///
  /// In en, this message translates to:
  /// **'Email is required'**
  String get signupEmailRequired;

  /// No description provided for @signupEmailInvalid.
  ///
  /// In en, this message translates to:
  /// **'Enter a valid email address'**
  String get signupEmailInvalid;

  /// No description provided for @signupContact.
  ///
  /// In en, this message translates to:
  /// **'Contact Number'**
  String get signupContact;

  /// No description provided for @signupContactRequired.
  ///
  /// In en, this message translates to:
  /// **'Contact number is required'**
  String get signupContactRequired;

  /// No description provided for @signupContactInvalid.
  ///
  /// In en, this message translates to:
  /// **'Enter a valid mobile number'**
  String get signupContactInvalid;

  /// No description provided for @signupFirstName.
  ///
  /// In en, this message translates to:
  /// **'First Name'**
  String get signupFirstName;

  /// No description provided for @signupLastName.
  ///
  /// In en, this message translates to:
  /// **'Last Name'**
  String get signupLastName;

  /// No description provided for @signupFieldRequired.
  ///
  /// In en, this message translates to:
  /// **'Required'**
  String get signupFieldRequired;

  /// No description provided for @signupPassword.
  ///
  /// In en, this message translates to:
  /// **'Password'**
  String get signupPassword;

  /// No description provided for @signupPasswordRequired.
  ///
  /// In en, this message translates to:
  /// **'Password is required'**
  String get signupPasswordRequired;

  /// No description provided for @signupPasswordMinLength.
  ///
  /// In en, this message translates to:
  /// **'Password must be at least 8 characters'**
  String get signupPasswordMinLength;

  /// No description provided for @signupConfirmPassword.
  ///
  /// In en, this message translates to:
  /// **'Confirm Password'**
  String get signupConfirmPassword;

  /// No description provided for @signupConfirmPasswordRequired.
  ///
  /// In en, this message translates to:
  /// **'Confirm password is required'**
  String get signupConfirmPasswordRequired;

  /// No description provided for @signupPasswordsNoMatch.
  ///
  /// In en, this message translates to:
  /// **'Passwords do not match'**
  String get signupPasswordsNoMatch;

  /// No description provided for @signupButton.
  ///
  /// In en, this message translates to:
  /// **'Sign Up'**
  String get signupButton;

  /// No description provided for @forgotOtpSentSuccess.
  ///
  /// In en, this message translates to:
  /// **'OTP sent successfully!'**
  String get forgotOtpSentSuccess;

  /// No description provided for @forgotOtpRequired.
  ///
  /// In en, this message translates to:
  /// **'Please enter the OTP'**
  String get forgotOtpRequired;

  /// No description provided for @forgotPasswordUpdated.
  ///
  /// In en, this message translates to:
  /// **'Password updated successfully!'**
  String get forgotPasswordUpdated;

  /// No description provided for @forgotTitleStep1.
  ///
  /// In en, this message translates to:
  /// **'Forgot Password?'**
  String get forgotTitleStep1;

  /// No description provided for @forgotTitleStep2.
  ///
  /// In en, this message translates to:
  /// **'Verify OTP'**
  String get forgotTitleStep2;

  /// No description provided for @forgotTitleStep3.
  ///
  /// In en, this message translates to:
  /// **'Reset Password'**
  String get forgotTitleStep3;

  /// No description provided for @forgotSubtitleStep1.
  ///
  /// In en, this message translates to:
  /// **'Enter your registered email or phone number to receive a verification OTP.'**
  String get forgotSubtitleStep1;

  /// No description provided for @forgotSubtitleStep2.
  ///
  /// In en, this message translates to:
  /// **'Enter the 6-digit verification code sent to your registered contact.'**
  String get forgotSubtitleStep2;

  /// No description provided for @forgotSubtitleStep3.
  ///
  /// In en, this message translates to:
  /// **'Enter and confirm your new password to complete the reset.'**
  String get forgotSubtitleStep3;

  /// No description provided for @forgotIdentityLabel.
  ///
  /// In en, this message translates to:
  /// **'Email or Phone Number'**
  String get forgotIdentityLabel;

  /// No description provided for @forgotFieldRequired.
  ///
  /// In en, this message translates to:
  /// **'This field is required'**
  String get forgotFieldRequired;

  /// No description provided for @forgotOtpLabel.
  ///
  /// In en, this message translates to:
  /// **'Enter OTP Code'**
  String get forgotOtpLabel;

  /// No description provided for @forgotNewPassword.
  ///
  /// In en, this message translates to:
  /// **'New Password'**
  String get forgotNewPassword;

  /// No description provided for @forgotNewPasswordRequired.
  ///
  /// In en, this message translates to:
  /// **'New password is required'**
  String get forgotNewPasswordRequired;

  /// No description provided for @forgotConfirmNewPassword.
  ///
  /// In en, this message translates to:
  /// **'Confirm New Password'**
  String get forgotConfirmNewPassword;

  /// No description provided for @forgotConfirmPasswordRequired.
  ///
  /// In en, this message translates to:
  /// **'Confirm password is required'**
  String get forgotConfirmPasswordRequired;

  /// No description provided for @forgotPasswordsNoMatch.
  ///
  /// In en, this message translates to:
  /// **'Passwords do not match'**
  String get forgotPasswordsNoMatch;

  /// No description provided for @forgotSendOtp.
  ///
  /// In en, this message translates to:
  /// **'Send OTP'**
  String get forgotSendOtp;

  /// No description provided for @forgotVerifyOtp.
  ///
  /// In en, this message translates to:
  /// **'Verify OTP'**
  String get forgotVerifyOtp;

  /// No description provided for @forgotUpdatePassword.
  ///
  /// In en, this message translates to:
  /// **'Update Password'**
  String get forgotUpdatePassword;

  /// No description provided for @completeProfileTitle.
  ///
  /// In en, this message translates to:
  /// **'One Last Step'**
  String get completeProfileTitle;

  /// No description provided for @completeProfileSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Please add your phone number to finish setting up your account'**
  String get completeProfileSubtitle;

  /// No description provided for @completeProfilePhone.
  ///
  /// In en, this message translates to:
  /// **'Phone Number'**
  String get completeProfilePhone;

  /// No description provided for @completeProfilePhoneRequired.
  ///
  /// In en, this message translates to:
  /// **'Phone number is required'**
  String get completeProfilePhoneRequired;

  /// No description provided for @completeProfilePhoneInvalid.
  ///
  /// In en, this message translates to:
  /// **'Enter a valid mobile number'**
  String get completeProfilePhoneInvalid;

  /// No description provided for @completeProfileContinue.
  ///
  /// In en, this message translates to:
  /// **'Continue'**
  String get completeProfileContinue;

  /// Generic cancel button
  ///
  /// In en, this message translates to:
  /// **'Cancel'**
  String get cancel;

  /// Generic error fallback message
  ///
  /// In en, this message translates to:
  /// **'Something went wrong'**
  String get somethingWentWrong;

  /// Button to submit a test attempt
  ///
  /// In en, this message translates to:
  /// **'Submit'**
  String get testSubmit;

  /// Shown when test submission fails
  ///
  /// In en, this message translates to:
  /// **'Failed to submit test'**
  String get testSubmitFailed;

  /// Title of the exit-test confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'Exit Test?'**
  String get testExitTitle;

  /// Body of the exit-test confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'Your progress will be lost if you exit now.'**
  String get testExitBody;

  /// Confirm button on the exit-test dialog
  ///
  /// In en, this message translates to:
  /// **'Exit'**
  String get testExitConfirm;

  /// Title for the question-navigator palette
  ///
  /// In en, this message translates to:
  /// **'Question Palette'**
  String get testPaletteTitle;

  /// Count of answered questions out of total
  ///
  /// In en, this message translates to:
  /// **'{answered}/{total} answered'**
  String testAnsweredOf(int answered, int total);

  /// Error title when a test/test-series fails to load
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load test'**
  String get testSeriesErrorTitle;

  /// Empty state when a test has no questions
  ///
  /// In en, this message translates to:
  /// **'No questions found'**
  String get testNoQuestions;

  /// Current question position out of total questions
  ///
  /// In en, this message translates to:
  /// **'Question {number} of {total}'**
  String questionOf(int number, int total);

  /// Short question number label, used in palette/breakdown lists
  ///
  /// In en, this message translates to:
  /// **'Q{number}'**
  String questionShort(int number);

  /// Marks value for a question
  ///
  /// In en, this message translates to:
  /// **'{marks} marks'**
  String marksShort(num marks);

  /// Instruction for single-choice questions
  ///
  /// In en, this message translates to:
  /// **'Select one option'**
  String get answerHintSelectOne;

  /// Instruction for multiple-choice questions
  ///
  /// In en, this message translates to:
  /// **'Select all that apply'**
  String get answerHintSelectMultiple;

  /// Instruction for match-the-following questions
  ///
  /// In en, this message translates to:
  /// **'Match the following'**
  String get answerHintMatch;

  /// Instruction for arrange/ordering questions
  ///
  /// In en, this message translates to:
  /// **'Arrange in the correct order'**
  String get answerHintArrange;

  /// Instruction for free-text answer questions
  ///
  /// In en, this message translates to:
  /// **'Type your answer'**
  String get answerHintText;

  /// Placeholder text inside the free-text answer field
  ///
  /// In en, this message translates to:
  /// **'Type your answer here...'**
  String get testTypeAnswerHint;

  /// Button to attach a photo answer
  ///
  /// In en, this message translates to:
  /// **'Attach Photo'**
  String get testAttachPhoto;

  /// Placeholder/hint for a match-the-following dropdown
  ///
  /// In en, this message translates to:
  /// **'Select a match'**
  String get testMatchSelect;

  /// Button to go to the previous question
  ///
  /// In en, this message translates to:
  /// **'Previous'**
  String get testPrevious;

  /// Button to go to the next question
  ///
  /// In en, this message translates to:
  /// **'Next'**
  String get testNext;

  /// Title of the rate-test-series card
  ///
  /// In en, this message translates to:
  /// **'Rate this Test Series'**
  String get testRateTitle;

  /// Subtitle of the rate-test-series card
  ///
  /// In en, this message translates to:
  /// **'Let us know how it was'**
  String get testRateSubtitle;

  /// Placeholder for the review text field
  ///
  /// In en, this message translates to:
  /// **'Write a review (optional)'**
  String get testReviewHint;

  /// Button to submit a rating/review
  ///
  /// In en, this message translates to:
  /// **'Submit Review'**
  String get testSubmitReview;

  /// Shown after a review is submitted successfully
  ///
  /// In en, this message translates to:
  /// **'Thanks for your feedback!'**
  String get testReviewThanks;

  /// Title of the ask-a-query card
  ///
  /// In en, this message translates to:
  /// **'Have a Question?'**
  String get testAskQueryTitle;

  /// Subtitle of the ask-a-query card
  ///
  /// In en, this message translates to:
  /// **'Ask about this test series'**
  String get testAskQuerySubtitle;

  /// Placeholder for the query text field
  ///
  /// In en, this message translates to:
  /// **'Type your question here...'**
  String get testQueryHint;

  /// Checkbox label to send the query anonymously
  ///
  /// In en, this message translates to:
  /// **'Ask anonymously'**
  String get testQueryAnonymous;

  /// Button to send a query
  ///
  /// In en, this message translates to:
  /// **'Send'**
  String get testSendQuery;

  /// Validation message when the query field is empty
  ///
  /// In en, this message translates to:
  /// **'Please enter your question'**
  String get testQueryEmpty;

  /// Shown after a query is sent successfully
  ///
  /// In en, this message translates to:
  /// **'Your question has been sent'**
  String get testQuerySent;

  /// Title of the test result screen
  ///
  /// In en, this message translates to:
  /// **'Test Result'**
  String get testResultTitle;

  /// Label for a manually finalized score
  ///
  /// In en, this message translates to:
  /// **'Final Score'**
  String get testFinalScore;

  /// Label for an automatically calculated score
  ///
  /// In en, this message translates to:
  /// **'Auto Score'**
  String get testAutoScore;

  /// Label for the total marks of a test
  ///
  /// In en, this message translates to:
  /// **'Total Marks'**
  String get testTotalMarks;

  /// Label for the score percentage
  ///
  /// In en, this message translates to:
  /// **'Percentage'**
  String get testPercentage;

  /// Note shown when part of the test still needs manual grading
  ///
  /// In en, this message translates to:
  /// **'Some answers are awaiting manual checking'**
  String get testAwaitingCheckNote;

  /// Label for the submission date/time
  ///
  /// In en, this message translates to:
  /// **'Submitted On'**
  String get testSubmittedOn;

  /// Label for the date/time a test was checked
  ///
  /// In en, this message translates to:
  /// **'Checked On'**
  String get testCheckedOn;

  /// Label for the attempt number
  ///
  /// In en, this message translates to:
  /// **'Attempt Number'**
  String get testAttemptNumber;

  /// Button to open the rate-test-series card
  ///
  /// In en, this message translates to:
  /// **'Rate this Series'**
  String get testRateSeries;

  /// Button to open the ask-a-query card
  ///
  /// In en, this message translates to:
  /// **'Ask a Question'**
  String get testAskQuery;

  /// Title for the per-question result breakdown section
  ///
  /// In en, this message translates to:
  /// **'Question Breakdown'**
  String get testBreakdown;

  /// Status label for a question awaiting manual review
  ///
  /// In en, this message translates to:
  /// **'Awaiting Review'**
  String get testAwaitingReview;

  /// Status label for a correctly answered question
  ///
  /// In en, this message translates to:
  /// **'Correct'**
  String get answerCorrect;

  /// Status label for an incorrectly answered question
  ///
  /// In en, this message translates to:
  /// **'Incorrect'**
  String get answerIncorrect;

  /// Status label for a reviewed assignment answer
  ///
  /// In en, this message translates to:
  /// **'Reviewed'**
  String get assignmentReviewed;

  /// Marks awarded out of the maximum for a question
  ///
  /// In en, this message translates to:
  /// **'{awarded}/{max} marks'**
  String assignmentMarksOf(num awarded, num max);

  /// Label for the user's submitted answer
  ///
  /// In en, this message translates to:
  /// **'Your Answer'**
  String get testYourAnswer;

  /// Shown when a question was left unanswered
  ///
  /// In en, this message translates to:
  /// **'Not answered'**
  String get answerNotAnswered;

  /// Bottom nav label for the home tab
  ///
  /// In en, this message translates to:
  /// **'Home'**
  String get homeTab;

  /// Bottom nav label for the campus tab
  ///
  /// In en, this message translates to:
  /// **'Campus'**
  String get campusTab;

  /// Bottom nav label for the classes tab
  ///
  /// In en, this message translates to:
  /// **'Classes'**
  String get classesTab;

  /// Bottom nav label for the chat tab
  ///
  /// In en, this message translates to:
  /// **'Chat'**
  String get chatTab;

  /// Bottom nav label for the profile tab
  ///
  /// In en, this message translates to:
  /// **'Profile'**
  String get profileTab;

  /// Generic confirm button
  ///
  /// In en, this message translates to:
  /// **'Confirm'**
  String get confirm;

  /// Assignments tab label for pending assignments
  ///
  /// In en, this message translates to:
  /// **'Pending'**
  String get assignmentsTabPending;

  /// Assignments tab label for submitted assignments
  ///
  /// In en, this message translates to:
  /// **'Submitted'**
  String get assignmentsTabSubmitted;

  /// Assignments tab label for checked assignments
  ///
  /// In en, this message translates to:
  /// **'Checked'**
  String get assignmentsTabChecked;

  /// Error title when assignments fail to load
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load assignments'**
  String get assignmentsErrorTitle;

  /// Empty state title for the assignments list
  ///
  /// In en, this message translates to:
  /// **'No assignments yet'**
  String get assignmentsEmptyTitle;

  /// Empty state subtitle for the assignments list
  ///
  /// In en, this message translates to:
  /// **'Assignments from your classrooms will show up here.'**
  String get assignmentsEmptySubtitle;

  /// Who posted an assignment
  ///
  /// In en, this message translates to:
  /// **'Posted by {postedBy}'**
  String assignmentPostedBy(String postedBy);

  /// Displayed grade value for an assignment submission
  ///
  /// In en, this message translates to:
  /// **'Grade: {grade}'**
  String assignmentGradeValue(String grade);

  /// Total marks value for an assignment
  ///
  /// In en, this message translates to:
  /// **'Total Marks: {totalMarks}'**
  String assignmentTotalMarks(num totalMarks);

  /// Shown when an assignment has no due date
  ///
  /// In en, this message translates to:
  /// **'No due date'**
  String get assignmentNoDueDate;

  /// Due date label for an assignment
  ///
  /// In en, this message translates to:
  /// **'Due on {date}'**
  String assignmentDueOn(String date);

  /// Status label for an overdue assignment
  ///
  /// In en, this message translates to:
  /// **'Overdue'**
  String get assignmentOverdue;

  /// Status label for an assignment due today
  ///
  /// In en, this message translates to:
  /// **'Due today'**
  String get assignmentDueToday;

  /// Status label for an assignment due tomorrow
  ///
  /// In en, this message translates to:
  /// **'Due tomorrow'**
  String get assignmentDueTomorrow;

  /// Number of days left before an assignment is due
  ///
  /// In en, this message translates to:
  /// **'{days} days left'**
  String assignmentDaysLeft(int days);

  /// Status label for a fully checked assignment
  ///
  /// In en, this message translates to:
  /// **'Checked'**
  String get assignmentStatusChecked;

  /// Status label for a partially checked assignment
  ///
  /// In en, this message translates to:
  /// **'Partially Checked'**
  String get assignmentStatusPartiallyChecked;

  /// Status label for a submitted assignment
  ///
  /// In en, this message translates to:
  /// **'Submitted'**
  String get assignmentStatusSubmitted;

  /// Status label for a late assignment submission
  ///
  /// In en, this message translates to:
  /// **'Late'**
  String get assignmentStatusLate;

  /// Status label for a missing assignment
  ///
  /// In en, this message translates to:
  /// **'Missing'**
  String get assignmentStatusMissing;

  /// Shown when trying to submit an assignment with no answers/attachments
  ///
  /// In en, this message translates to:
  /// **'There\'s nothing to submit'**
  String get assignmentNothingToSubmit;

  /// Title of the submit-assignment confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'Submit Assignment?'**
  String get assignmentSubmitConfirmTitle;

  /// Confirmation body when some assignment questions are unanswered
  ///
  /// In en, this message translates to:
  /// **'{unanswered} questions are unanswered. Submit anyway?'**
  String assignmentSubmitConfirmUnanswered(int unanswered);

  /// Confirmation body when all assignment questions are answered
  ///
  /// In en, this message translates to:
  /// **'Are you sure you want to submit this assignment?'**
  String get assignmentSubmitConfirmBody;

  /// Shown after an assignment is submitted successfully
  ///
  /// In en, this message translates to:
  /// **'Assignment submitted successfully'**
  String get assignmentSubmitSuccess;

  /// Shown when submitting an assignment fails
  ///
  /// In en, this message translates to:
  /// **'Failed to submit assignment'**
  String get assignmentSubmitFailed;

  /// Label for the due-date field on an assignment
  ///
  /// In en, this message translates to:
  /// **'Due'**
  String get assignmentDueLabel;

  /// Label for the total-marks field on an assignment/test series
  ///
  /// In en, this message translates to:
  /// **'Total Marks'**
  String get assignmentTotalMarksLabel;

  /// Label for the questions-count field on an assignment
  ///
  /// In en, this message translates to:
  /// **'Questions'**
  String get assignmentQuestionsLabel;

  /// Button to open an assignment's attachment
  ///
  /// In en, this message translates to:
  /// **'Open Attachment'**
  String get assignmentOpenAttachment;

  /// Notice shown when an assignment was submitted after the due date
  ///
  /// In en, this message translates to:
  /// **'This assignment was submitted late'**
  String get assignmentLateNotice;

  /// Label for the student's submitted answer on an assignment
  ///
  /// In en, this message translates to:
  /// **'Your Answer'**
  String get assignmentYourAnswer;

  /// Placeholder text inside the assignment answer field
  ///
  /// In en, this message translates to:
  /// **'Type your answer here...'**
  String get assignmentAnswerHint;

  /// Empty state when an assignment has no questions
  ///
  /// In en, this message translates to:
  /// **'No questions found'**
  String get assignmentNoQuestions;

  /// Button to attach a file to an assignment answer
  ///
  /// In en, this message translates to:
  /// **'Attach File'**
  String get assignmentAttachFile;

  /// Tooltip/button to remove an attached file
  ///
  /// In en, this message translates to:
  /// **'Remove File'**
  String get assignmentRemoveFile;

  /// Count of answered assignment questions out of total
  ///
  /// In en, this message translates to:
  /// **'{answered}/{total} answered'**
  String assignmentAnsweredOf(int answered, int total);

  /// Button to submit an assignment
  ///
  /// In en, this message translates to:
  /// **'Submit'**
  String get assignmentSubmit;

  /// Section title for assignment result
  ///
  /// In en, this message translates to:
  /// **'Result'**
  String get assignmentResult;

  /// Label for marks awarded on an assignment
  ///
  /// In en, this message translates to:
  /// **'Marks Awarded'**
  String get assignmentMarksAwarded;

  /// Label for the grade of an assignment submission
  ///
  /// In en, this message translates to:
  /// **'Grade'**
  String get assignmentGrade;

  /// Label for the submission date/time of an assignment
  ///
  /// In en, this message translates to:
  /// **'Submitted On'**
  String get assignmentSubmittedOn;

  /// Label for the date/time an assignment was checked
  ///
  /// In en, this message translates to:
  /// **'Checked On'**
  String get assignmentCheckedOn;

  /// Note shown when part of an assignment still needs manual grading
  ///
  /// In en, this message translates to:
  /// **'Some answers are awaiting manual checking'**
  String get assignmentPartiallyCheckedNote;

  /// Section title for teacher feedback on an assignment
  ///
  /// In en, this message translates to:
  /// **'Teacher\'s Feedback'**
  String get assignmentTeacherFeedback;

  /// Shown when a student did not provide a written answer
  ///
  /// In en, this message translates to:
  /// **'No written answer provided'**
  String get assignmentNoWrittenAnswer;

  /// Button to open a file the student submitted
  ///
  /// In en, this message translates to:
  /// **'Open Submitted File'**
  String get assignmentOpenSubmittedFile;

  /// Status label for an assignment answer awaiting manual review
  ///
  /// In en, this message translates to:
  /// **'Awaiting Review'**
  String get assignmentAwaitingReview;

  /// Button to attach a photo to an assignment answer
  ///
  /// In en, this message translates to:
  /// **'Attach Photo'**
  String get assignmentAttachPhoto;

  /// Option to take a new photo with the camera when attaching an answer photo
  ///
  /// In en, this message translates to:
  /// **'Take a photo'**
  String get assignmentPickFromCamera;

  /// Option to pick an existing photo from the gallery when attaching an answer photo
  ///
  /// In en, this message translates to:
  /// **'Choose from gallery'**
  String get assignmentPickFromGallery;

  /// Shown when picking a file/photo for an assignment answer fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t pick that file. Please try again.'**
  String get assignmentPickFailed;

  /// Shown when a requested assignment attachment returns a 404
  ///
  /// In en, this message translates to:
  /// **'This file is no longer available.'**
  String get assignmentFileNotFound;

  /// Shown when opening an assignment attachment fails due to a network error
  ///
  /// In en, this message translates to:
  /// **'Check your internet connection and try again.'**
  String get assignmentNoInternet;

  /// Title of the create-assignment screen, and its submit button label
  ///
  /// In en, this message translates to:
  /// **'Create Assignment'**
  String get assignmentCreateTitle;

  /// Label for the assignment title field on the create screen
  ///
  /// In en, this message translates to:
  /// **'Title'**
  String get assignmentTitleLabel;

  /// Label for the assignment description field on the create screen
  ///
  /// In en, this message translates to:
  /// **'Description'**
  String get assignmentDescriptionLabel;

  /// Placeholder text for the assignment description field
  ///
  /// In en, this message translates to:
  /// **'What should students do? Add instructions here…'**
  String get assignmentDescriptionHint;

  /// Button to open the due-date picker on the create screen
  ///
  /// In en, this message translates to:
  /// **'Pick a due date'**
  String get assignmentPickDueDate;

  /// Button to clear a chosen due date on the create screen
  ///
  /// In en, this message translates to:
  /// **'Clear'**
  String get assignmentClearDueDate;

  /// Label for the switch that turns on question-based (auto-graded) mode
  ///
  /// In en, this message translates to:
  /// **'Structured questions'**
  String get assignmentStructuredToggleLabel;

  /// Explanation shown under the structured-questions switch
  ///
  /// In en, this message translates to:
  /// **'Turn this on for auto-graded multiple-choice or ordered-list questions instead of one written answer.'**
  String get assignmentStructuredToggleHint;

  /// Placeholder for the optional total-marks field on a free-form assignment
  ///
  /// In en, this message translates to:
  /// **'Optional — leave blank for no fixed scale'**
  String get assignmentTotalMarksHint;

  /// Button to add a new question while creating a structured assignment
  ///
  /// In en, this message translates to:
  /// **'Add question'**
  String get assignmentAddQuestion;

  /// Label for the question-type dropdown in the question editor
  ///
  /// In en, this message translates to:
  /// **'Question type'**
  String get assignmentQuestionTypeLabel;

  /// Question type option: a free-text written answer
  ///
  /// In en, this message translates to:
  /// **'Written answer'**
  String get assignmentQuestionTypeText;

  /// Question type option: single-select multiple choice
  ///
  /// In en, this message translates to:
  /// **'Multiple choice (one answer)'**
  String get assignmentQuestionTypeMcq;

  /// Question type option: multi-select multiple choice
  ///
  /// In en, this message translates to:
  /// **'Multiple choice (multiple answers)'**
  String get assignmentQuestionTypeMsq;

  /// Question type option: arrange options in the correct order
  ///
  /// In en, this message translates to:
  /// **'Ordered list'**
  String get assignmentQuestionTypeList;

  /// Placeholder for a question's text field in the question editor
  ///
  /// In en, this message translates to:
  /// **'Type your question…'**
  String get assignmentQuestionTextHint;

  /// Label for a question's marks field in the question editor
  ///
  /// In en, this message translates to:
  /// **'Marks'**
  String get assignmentQuestionMarksLabel;

  /// Button to add an answer option to a question being created
  ///
  /// In en, this message translates to:
  /// **'Add option'**
  String get assignmentAddOption;

  /// Placeholder for an option's text field in the question editor
  ///
  /// In en, this message translates to:
  /// **'Option text'**
  String get assignmentOptionHint;

  /// Tooltip for the button that removes an option in the question editor
  ///
  /// In en, this message translates to:
  /// **'Remove option'**
  String get assignmentRemoveOption;

  /// Tooltip for the button that removes a whole question in the question editor
  ///
  /// In en, this message translates to:
  /// **'Remove question'**
  String get assignmentRemoveQuestion;

  /// Validation message when the title field is empty on submit
  ///
  /// In en, this message translates to:
  /// **'Please enter a title'**
  String get assignmentCreateValidationTitle;

  /// Validation message for an incomplete question while creating a structured assignment
  ///
  /// In en, this message translates to:
  /// **'Every question needs question text, marks, and (if applicable) a marked correct answer'**
  String get assignmentCreateValidationQuestion;

  /// Validation message when a choice/ordered-list question has fewer than 2 options
  ///
  /// In en, this message translates to:
  /// **'Add at least 2 options to this question'**
  String get assignmentCreateValidationOptions;

  /// Validation message when no correct option is marked on an MCQ/MSQ question
  ///
  /// In en, this message translates to:
  /// **'Mark at least one correct option for this question'**
  String get assignmentCreateValidationCorrect;

  /// Snackbar shown after successfully creating an assignment
  ///
  /// In en, this message translates to:
  /// **'Assignment created'**
  String get assignmentCreateSuccess;

  /// Snackbar shown when creating an assignment fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t create the assignment. Please try again.'**
  String get assignmentCreateFailed;

  /// Heading for the free-form self-grading panel on a submitted assignment
  ///
  /// In en, this message translates to:
  /// **'Grade this submission'**
  String get assignmentGradeSectionTitle;

  /// Placeholder for a feedback text field, used both when grading and when reviewing a single answer
  ///
  /// In en, this message translates to:
  /// **'Feedback (optional)'**
  String get assignmentFeedbackHint;

  /// Snackbar shown after successfully saving a free-form grade
  ///
  /// In en, this message translates to:
  /// **'Grade saved'**
  String get assignmentGradeSaved;

  /// Snackbar shown when saving a free-form grade fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t save the grade. Please try again.'**
  String get assignmentGradeFailed;

  /// Heading for the inline marks/feedback form on an awaiting-review text answer
  ///
  /// In en, this message translates to:
  /// **'Review this answer'**
  String get assignmentReviewAnswerTitle;

  /// Snackbar shown after successfully saving a single answer's review
  ///
  /// In en, this message translates to:
  /// **'Review saved'**
  String get assignmentReviewSaved;

  /// Snackbar shown when saving a single answer's review fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t save the review. Please try again.'**
  String get assignmentReviewFailed;

  /// Status text shown when a submission has not been published to a public link
  ///
  /// In en, this message translates to:
  /// **'Not shared — only you can see this result'**
  String get assignmentNotShared;

  /// Status text shown when a submission has been published to a public link
  ///
  /// In en, this message translates to:
  /// **'Anyone with the link can view this result'**
  String get assignmentSharePublicNote;

  /// Button to publish a submission and copy its public link
  ///
  /// In en, this message translates to:
  /// **'Share'**
  String get assignmentShareResult;

  /// Button to unpublish a previously-shared submission
  ///
  /// In en, this message translates to:
  /// **'Stop sharing'**
  String get assignmentUnshareResult;

  /// Snackbar shown after publishing a submission and copying its public link to the clipboard
  ///
  /// In en, this message translates to:
  /// **'Link copied — anyone with it can view your result'**
  String get assignmentLinkCopied;

  /// Snackbar shown when publishing a submission fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t share this result. Please try again.'**
  String get assignmentPublishFailed;

  /// Snackbar shown when unpublishing a submission fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t stop sharing this result. Please try again.'**
  String get assignmentUnpublishFailed;

  /// Test series tab label for all series
  ///
  /// In en, this message translates to:
  /// **'All'**
  String get testSeriesTabAll;

  /// Test series tab label for free series
  ///
  /// In en, this message translates to:
  /// **'Free'**
  String get testSeriesTabFree;

  /// Test series tab label for paid series
  ///
  /// In en, this message translates to:
  /// **'Paid'**
  String get testSeriesTabPaid;

  /// Empty state title for the test series list
  ///
  /// In en, this message translates to:
  /// **'No test series yet'**
  String get testSeriesEmptyTitle;

  /// Empty state subtitle for the test series list
  ///
  /// In en, this message translates to:
  /// **'Test series from your classrooms will show up here.'**
  String get testSeriesEmptySubtitle;

  /// Price of a test series in coins
  ///
  /// In en, this message translates to:
  /// **'{coins} coins'**
  String testSeriesCoins(int coins);

  /// Chip label for a free test series
  ///
  /// In en, this message translates to:
  /// **'Free'**
  String get testSeriesFree;

  /// Creator of a test series
  ///
  /// In en, this message translates to:
  /// **'By {creator}'**
  String testSeriesBy(String creator);

  /// Total marks of a test series
  ///
  /// In en, this message translates to:
  /// **'{marks} marks'**
  String testSeriesMarks(num marks);

  /// Duration of a test series in minutes
  ///
  /// In en, this message translates to:
  /// **'{minutes} min'**
  String testSeriesDuration(int minutes);

  /// Number of attempts allowed for a test series
  ///
  /// In en, this message translates to:
  /// **'{attempts} attempts'**
  String testSeriesAttempts(int attempts);

  /// Button to start a test series attempt
  ///
  /// In en, this message translates to:
  /// **'Start'**
  String get testSeriesStart;

  /// Button to resume an in-progress test series attempt
  ///
  /// In en, this message translates to:
  /// **'Resume'**
  String get testSeriesResume;

  /// Button to view a completed test series attempt's result
  ///
  /// In en, this message translates to:
  /// **'View Result'**
  String get testSeriesViewResult;

  /// Shown when a test series has no ratings
  ///
  /// In en, this message translates to:
  /// **'No ratings yet'**
  String get testSeriesNoRatings;

  /// Average rating and review count for a test series
  ///
  /// In en, this message translates to:
  /// **'{rating} ({reviewCount} reviews)'**
  String testSeriesRating(String rating, int reviewCount);

  /// Status label for a fully checked test attempt
  ///
  /// In en, this message translates to:
  /// **'Checked'**
  String get testStatusChecked;

  /// Status label for a partially checked test attempt
  ///
  /// In en, this message translates to:
  /// **'Partially Checked'**
  String get testStatusPartiallyChecked;

  /// Status label for a submitted test attempt
  ///
  /// In en, this message translates to:
  /// **'Submitted'**
  String get testStatusSubmitted;

  /// Status label for a test attempt in progress
  ///
  /// In en, this message translates to:
  /// **'In Progress'**
  String get testStatusInProgress;

  /// Title of the pay-to-unlock confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'Unlock Test Series'**
  String get testSeriesPayTitle;

  /// Body of the pay-to-unlock confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'This test series costs {coins} coins. Continue?'**
  String testSeriesPayBody(int coins);

  /// Shown when a user tries to buy a test series without enough coins
  ///
  /// In en, this message translates to:
  /// **'You don\'t have enough coins for this test series'**
  String get testSeriesNotEnoughCoins;

  /// Label for the questions-count field on a test series
  ///
  /// In en, this message translates to:
  /// **'Questions'**
  String get testSeriesQuestionsLabel;

  /// Label for the duration field on a test series
  ///
  /// In en, this message translates to:
  /// **'Duration'**
  String get testSeriesDurationLabel;

  /// Shown when a test series has no time limit
  ///
  /// In en, this message translates to:
  /// **'No time limit'**
  String get testSeriesNoTimeLimit;

  /// Label for the attempts-allowed field on a test series
  ///
  /// In en, this message translates to:
  /// **'Attempts'**
  String get testSeriesAttemptsLabel;

  /// Label for the user's own attempt on a test series
  ///
  /// In en, this message translates to:
  /// **'Your Attempt'**
  String get testSeriesYourAttempt;

  /// Notice shown before starting a timed test
  ///
  /// In en, this message translates to:
  /// **'This test has a time limit of {minutes} minutes'**
  String testSeriesTimerNotice(int minutes);

  /// Section title for test series reviews
  ///
  /// In en, this message translates to:
  /// **'Reviews'**
  String get testSeriesReviews;

  /// Shown when a test series has no reviews
  ///
  /// In en, this message translates to:
  /// **'No reviews yet'**
  String get testSeriesNoReviews;

  /// Shown when a timed test's duration runs out
  ///
  /// In en, this message translates to:
  /// **'Time\'s up!'**
  String get testTimeUp;

  /// Title of the submit-test confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'Submit Test?'**
  String get testSubmitConfirmTitle;

  /// Confirmation body when some test questions are unanswered
  ///
  /// In en, this message translates to:
  /// **'{unanswered} questions are unanswered. Submit anyway?'**
  String testSubmitConfirmUnanswered(int unanswered);

  /// Confirmation body when all test questions are answered
  ///
  /// In en, this message translates to:
  /// **'Are you sure you want to submit this test?'**
  String get testSubmitConfirmBody;

  /// No description provided for @viewAll.
  ///
  /// In en, this message translates to:
  /// **'View all'**
  String get viewAll;

  /// No description provided for @settingsTitle.
  ///
  /// In en, this message translates to:
  /// **'Settings'**
  String get settingsTitle;

  /// No description provided for @settingsLanguageSection.
  ///
  /// In en, this message translates to:
  /// **'Language'**
  String get settingsLanguageSection;

  /// No description provided for @settingsThemeSection.
  ///
  /// In en, this message translates to:
  /// **'Appearance'**
  String get settingsThemeSection;

  /// No description provided for @settingsThemeLight.
  ///
  /// In en, this message translates to:
  /// **'Light'**
  String get settingsThemeLight;

  /// No description provided for @settingsThemeDark.
  ///
  /// In en, this message translates to:
  /// **'Dark'**
  String get settingsThemeDark;

  /// No description provided for @settingsThemeSystem.
  ///
  /// In en, this message translates to:
  /// **'Match device'**
  String get settingsThemeSystem;

  /// No description provided for @settingsAccountSection.
  ///
  /// In en, this message translates to:
  /// **'Account'**
  String get settingsAccountSection;

  /// No description provided for @settingsLogout.
  ///
  /// In en, this message translates to:
  /// **'Log out'**
  String get settingsLogout;

  /// No description provided for @settingsLogoutConfirm.
  ///
  /// In en, this message translates to:
  /// **'You\'ll need to sign in again to use the app.'**
  String get settingsLogoutConfirm;

  /// No description provided for @roleAdmin.
  ///
  /// In en, this message translates to:
  /// **'Campus admin'**
  String get roleAdmin;

  /// No description provided for @rolePrincipalHod.
  ///
  /// In en, this message translates to:
  /// **'Principal / HOD'**
  String get rolePrincipalHod;

  /// No description provided for @roleClassTeacher.
  ///
  /// In en, this message translates to:
  /// **'Class teacher'**
  String get roleClassTeacher;

  /// No description provided for @roleSubjectTeacher.
  ///
  /// In en, this message translates to:
  /// **'Subject teacher'**
  String get roleSubjectTeacher;

  /// No description provided for @roleNonTeaching.
  ///
  /// In en, this message translates to:
  /// **'Office staff'**
  String get roleNonTeaching;

  /// No description provided for @roleStudent.
  ///
  /// In en, this message translates to:
  /// **'Student'**
  String get roleStudent;

  /// No description provided for @roleParent.
  ///
  /// In en, this message translates to:
  /// **'Parent'**
  String get roleParent;

  /// No description provided for @roleManagement.
  ///
  /// In en, this message translates to:
  /// **'Management'**
  String get roleManagement;

  /// No description provided for @roleNone.
  ///
  /// In en, this message translates to:
  /// **'Member'**
  String get roleNone;

  /// No description provided for @campusSwitchTooltip.
  ///
  /// In en, this message translates to:
  /// **'Switch campus'**
  String get campusSwitchTooltip;

  /// No description provided for @campusLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load your campus'**
  String get campusLoadFailed;

  /// No description provided for @campusNoneTitle.
  ///
  /// In en, this message translates to:
  /// **'You\'re not part of a campus yet'**
  String get campusNoneTitle;

  /// No description provided for @campusNoneSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Ask your college or school to add you, or create a campus of your own.'**
  String get campusNoneSubtitle;

  /// No description provided for @campusPendingTitle.
  ///
  /// In en, this message translates to:
  /// **'Waiting for approval'**
  String get campusPendingTitle;

  /// No description provided for @campusPendingBody.
  ///
  /// In en, this message translates to:
  /// **'This campus is under review. Once it\'s approved you can add staff and students.'**
  String get campusPendingBody;

  /// No description provided for @campusRejectedTitle.
  ///
  /// In en, this message translates to:
  /// **'Campus not approved'**
  String get campusRejectedTitle;

  /// No description provided for @campusRejectedBody.
  ///
  /// In en, this message translates to:
  /// **'This campus was not approved. Contact support if you think this is a mistake.'**
  String get campusRejectedBody;

  /// No description provided for @campusMyClassTitle.
  ///
  /// In en, this message translates to:
  /// **'My class'**
  String get campusMyClassTitle;

  /// No description provided for @campusMySectionsTitle.
  ///
  /// In en, this message translates to:
  /// **'My sections'**
  String get campusMySectionsTitle;

  /// No description provided for @campusAllSectionsTitle.
  ///
  /// In en, this message translates to:
  /// **'All sections'**
  String get campusAllSectionsTitle;

  /// No description provided for @campusManagementTitle.
  ///
  /// In en, this message translates to:
  /// **'Campus overview'**
  String get campusManagementTitle;

  /// No description provided for @campusNoSectionsAssigned.
  ///
  /// In en, this message translates to:
  /// **'No sections assigned to you yet. Your campus admin sets this up.'**
  String get campusNoSectionsAssigned;

  /// No description provided for @campusSectionsCount.
  ///
  /// In en, this message translates to:
  /// **'Sections'**
  String get campusSectionsCount;

  /// No description provided for @campusSubjectsCount.
  ///
  /// In en, this message translates to:
  /// **'Subjects'**
  String get campusSubjectsCount;

  /// No description provided for @campusAttendanceLabel.
  ///
  /// In en, this message translates to:
  /// **'Attendance'**
  String get campusAttendanceLabel;

  /// No description provided for @campusTapToView.
  ///
  /// In en, this message translates to:
  /// **'Tap to view'**
  String get campusTapToView;

  /// No description provided for @campusMarkAttendance.
  ///
  /// In en, this message translates to:
  /// **'Mark attendance'**
  String get campusMarkAttendance;

  /// No description provided for @campusStudents.
  ///
  /// In en, this message translates to:
  /// **'Students'**
  String get campusStudents;

  /// No description provided for @campusTimetableTitle.
  ///
  /// In en, this message translates to:
  /// **'Timetable'**
  String get campusTimetableTitle;

  /// No description provided for @campusTimetableLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load the timetable'**
  String get campusTimetableLoadFailed;

  /// No description provided for @campusNoClassesToday.
  ///
  /// In en, this message translates to:
  /// **'No periods scheduled for this day'**
  String get campusNoClassesToday;

  /// No description provided for @campusUnknownSubject.
  ///
  /// In en, this message translates to:
  /// **'Subject'**
  String get campusUnknownSubject;

  /// No description provided for @campusSearchStudents.
  ///
  /// In en, this message translates to:
  /// **'Search by name or roll number'**
  String get campusSearchStudents;

  /// No description provided for @campusNoMatches.
  ///
  /// In en, this message translates to:
  /// **'No students match that search'**
  String get campusNoMatches;

  /// No description provided for @campusStudentCount.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 student} other{{count} students}}'**
  String campusStudentCount(int count);

  /// No description provided for @campusRosterLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load the student list'**
  String get campusRosterLoadFailed;

  /// No description provided for @campusRosterEmptyTitle.
  ///
  /// In en, this message translates to:
  /// **'No students in this section'**
  String get campusRosterEmptyTitle;

  /// No description provided for @campusRosterEmptySubtitle.
  ///
  /// In en, this message translates to:
  /// **'Students appear here once they\'re enrolled for the current session.'**
  String get campusRosterEmptySubtitle;

  /// No description provided for @campusNoAttendancePermissionTitle.
  ///
  /// In en, this message translates to:
  /// **'You can\'t mark this attendance'**
  String get campusNoAttendancePermissionTitle;

  /// No description provided for @campusNoAttendancePermissionBody.
  ///
  /// In en, this message translates to:
  /// **'Only the class teacher or the assigned subject teacher can mark attendance for this section.'**
  String get campusNoAttendancePermissionBody;

  /// No description provided for @campusNoticesTitle.
  ///
  /// In en, this message translates to:
  /// **'Notices'**
  String get campusNoticesTitle;

  /// No description provided for @campusNoticesLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load notices'**
  String get campusNoticesLoadFailed;

  /// No description provided for @campusNoNotices.
  ///
  /// In en, this message translates to:
  /// **'No notices yet'**
  String get campusNoNotices;

  /// No description provided for @campusPostNotice.
  ///
  /// In en, this message translates to:
  /// **'Post a notice'**
  String get campusPostNotice;

  /// No description provided for @noticeAudienceLabel.
  ///
  /// In en, this message translates to:
  /// **'Who sees this'**
  String get noticeAudienceLabel;

  /// No description provided for @noticeTitleLabel.
  ///
  /// In en, this message translates to:
  /// **'Title'**
  String get noticeTitleLabel;

  /// No description provided for @noticeBodyLabel.
  ///
  /// In en, this message translates to:
  /// **'Message'**
  String get noticeBodyLabel;

  /// No description provided for @noticePinLabel.
  ///
  /// In en, this message translates to:
  /// **'Pin to the top'**
  String get noticePinLabel;

  /// No description provided for @noticePinHint.
  ///
  /// In en, this message translates to:
  /// **'Stays pinned for 7 days'**
  String get noticePinHint;

  /// No description provided for @noticeSendLabel.
  ///
  /// In en, this message translates to:
  /// **'Post notice'**
  String get noticeSendLabel;

  /// No description provided for @noticeEmptyError.
  ///
  /// In en, this message translates to:
  /// **'Add a title and a message before posting.'**
  String get noticeEmptyError;

  /// No description provided for @noticeNoSessionError.
  ///
  /// In en, this message translates to:
  /// **'No active academic session for this campus.'**
  String get noticeNoSessionError;

  /// No description provided for @noticeSelectDepartmentError.
  ///
  /// In en, this message translates to:
  /// **'Pick which department this notice is for.'**
  String get noticeSelectDepartmentError;

  /// No description provided for @noticeScopeWholeCampus.
  ///
  /// In en, this message translates to:
  /// **'Whole campus'**
  String get noticeScopeWholeCampus;

  /// No description provided for @noticeScopeCampus.
  ///
  /// In en, this message translates to:
  /// **'Campus'**
  String get noticeScopeCampus;

  /// No description provided for @noticeScopeClass.
  ///
  /// In en, this message translates to:
  /// **'Class'**
  String get noticeScopeClass;

  /// No description provided for @noticeScopeDepartment.
  ///
  /// In en, this message translates to:
  /// **'Department'**
  String get noticeScopeDepartment;

  /// No description provided for @noticeScopeSection.
  ///
  /// In en, this message translates to:
  /// **'Section'**
  String get noticeScopeSection;

  /// No description provided for @attendanceWholeDay.
  ///
  /// In en, this message translates to:
  /// **'Whole day'**
  String get attendanceWholeDay;

  /// No description provided for @attendanceSubjectLabel.
  ///
  /// In en, this message translates to:
  /// **'Subject'**
  String get attendanceSubjectLabel;

  /// No description provided for @attendancePresent.
  ///
  /// In en, this message translates to:
  /// **'Present'**
  String get attendancePresent;

  /// No description provided for @attendanceAbsent.
  ///
  /// In en, this message translates to:
  /// **'Absent'**
  String get attendanceAbsent;

  /// No description provided for @attendanceTotal.
  ///
  /// In en, this message translates to:
  /// **'Total'**
  String get attendanceTotal;

  /// No description provided for @attendanceOverall.
  ///
  /// In en, this message translates to:
  /// **'Overall attendance'**
  String get attendanceOverall;

  /// No description provided for @attendanceBySubject.
  ///
  /// In en, this message translates to:
  /// **'By subject'**
  String get attendanceBySubject;

  /// No description provided for @attendanceNoSubjectData.
  ///
  /// In en, this message translates to:
  /// **'No subject-wise records yet'**
  String get attendanceNoSubjectData;

  /// No description provided for @attendanceSummaryFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load attendance'**
  String get attendanceSummaryFailed;

  /// No description provided for @attendanceAlreadyMarked.
  ///
  /// In en, this message translates to:
  /// **'Attendance for this day is already marked. Pick another date to make changes.'**
  String get attendanceAlreadyMarked;

  /// No description provided for @attendanceSaveLabel.
  ///
  /// In en, this message translates to:
  /// **'Save for {count} students'**
  String attendanceSaveLabel(int count);

  /// No description provided for @attendanceSaving.
  ///
  /// In en, this message translates to:
  /// **'Saving {done} of {total}'**
  String attendanceSaving(int done, int total);

  /// No description provided for @attendanceSavedAll.
  ///
  /// In en, this message translates to:
  /// **'Attendance saved for {count} students'**
  String attendanceSavedAll(int count);

  /// No description provided for @attendanceSavedPartial.
  ///
  /// In en, this message translates to:
  /// **'Saved {saved}, {failed} didn\'t go through'**
  String attendanceSavedPartial(int saved, int failed);

  /// No description provided for @attendanceFailedCount.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 student didn\'t save} other{{count} students didn\'t save}}'**
  String attendanceFailedCount(int count);

  /// No description provided for @attendanceBelowThreshold.
  ///
  /// In en, this message translates to:
  /// **'Attendance is below the {percent}% your campus expects.'**
  String attendanceBelowThreshold(int percent);

  /// No description provided for @timeMinutesAgo.
  ///
  /// In en, this message translates to:
  /// **'{count}m ago'**
  String timeMinutesAgo(int count);

  /// No description provided for @timeHoursAgo.
  ///
  /// In en, this message translates to:
  /// **'{count}h ago'**
  String timeHoursAgo(int count);

  /// No description provided for @timeDaysAgo.
  ///
  /// In en, this message translates to:
  /// **'{count}d ago'**
  String timeDaysAgo(int count);

  /// Search screen filter chip — show all result types
  ///
  /// In en, this message translates to:
  /// **'All'**
  String get searchFilterAll;

  /// Search screen filter chip — people/friends only
  ///
  /// In en, this message translates to:
  /// **'People'**
  String get searchFilterPeople;

  /// Search screen filter chip — campus notices only
  ///
  /// In en, this message translates to:
  /// **'Notices'**
  String get searchFilterNotices;

  /// Search screen filter chip — assignments only
  ///
  /// In en, this message translates to:
  /// **'Assignments'**
  String get searchFilterAssignments;

  /// Search screen filter chip — test series only
  ///
  /// In en, this message translates to:
  /// **'Test Series'**
  String get searchFilterTests;

  /// Search screen filter chip — messages only
  ///
  /// In en, this message translates to:
  /// **'Messages'**
  String get searchFilterMessages;

  /// Search results section header — people
  ///
  /// In en, this message translates to:
  /// **'People'**
  String get searchSectionPeople;

  /// Search results section header — campus notices
  ///
  /// In en, this message translates to:
  /// **'Campus Notices'**
  String get searchSectionNotices;

  /// Search results section header — assignments
  ///
  /// In en, this message translates to:
  /// **'Assignments'**
  String get searchSectionAssignments;

  /// Search results section header — test series
  ///
  /// In en, this message translates to:
  /// **'Test Series'**
  String get searchSectionTests;

  /// Search results section header — messages
  ///
  /// In en, this message translates to:
  /// **'Messages'**
  String get searchSectionMessages;

  /// Header above the list of recent searches
  ///
  /// In en, this message translates to:
  /// **'Recent'**
  String get searchRecentTitle;

  /// Button to clear all recent searches
  ///
  /// In en, this message translates to:
  /// **'Clear all'**
  String get searchRecentClear;

  /// Prompt shown on the search screen before the user types anything
  ///
  /// In en, this message translates to:
  /// **'Search for people, classes, notices, assignments and tests'**
  String get searchEmptyPrompt;

  /// Shown when a search returns nothing
  ///
  /// In en, this message translates to:
  /// **'No results for \"{query}\"'**
  String searchNoResultsFor(String query);

  /// Shown in a result's detail sheet for sources without a deep-link screen yet
  ///
  /// In en, this message translates to:
  /// **'Full detail view for this result isn\'t wired up yet'**
  String get searchDetailUnavailable;

  /// Shown when tapping a classroom row fails to open it
  ///
  /// In en, this message translates to:
  /// **'Could not open this classroom.'**
  String get couldNotOpenClassroom;

  /// Shown when tapping a live class card fails to open it
  ///
  /// In en, this message translates to:
  /// **'Could not open this class.'**
  String get couldNotOpenClass;

  /// Option to take a photo with the camera, for a story upload
  ///
  /// In en, this message translates to:
  /// **'Camera'**
  String get camera;

  /// Option to record a video with the camera, for a story upload
  ///
  /// In en, this message translates to:
  /// **'Record video'**
  String get recordVideo;

  /// Option to pick media from the photo gallery, for a story upload
  ///
  /// In en, this message translates to:
  /// **'Choose from gallery'**
  String get chooseFromGallery;

  /// Label on the current user's own story ring
  ///
  /// In en, this message translates to:
  /// **'Your Story'**
  String get yourStory;

  /// Shown when a story upload fails
  ///
  /// In en, this message translates to:
  /// **'Could not upload your story.'**
  String get couldNotUploadStory;

  /// Shown when the user has no classroom eligible for a referral link
  ///
  /// In en, this message translates to:
  /// **'None of your classrooms have referrals turned on yet.'**
  String get noClassroomsWithReferrals;

  /// Shown when generating a classroom referral link fails
  ///
  /// In en, this message translates to:
  /// **'Could not create your referral link.'**
  String get couldNotCreateReferralLink;

  /// Button to open a downloaded file (e.g. a PDF)
  ///
  /// In en, this message translates to:
  /// **'Open'**
  String get open;

  /// Invite & earn strip subtitle showing commission earned and pending
  ///
  /// In en, this message translates to:
  /// **'You\'ve earned ₹{earned} so far — ₹{pending} on the way'**
  String inviteEarnedSoFar(int earned, int pending);

  /// Default invite & earn strip subtitle, shown before the user has earned anything
  ///
  /// In en, this message translates to:
  /// **'Share a classroom\'s link — earn a daily % commission for as long as they stay enrolled'**
  String get inviteShareClassroomLink;

  /// Confirmation shown after generating a referral link for a classroom
  ///
  /// In en, this message translates to:
  /// **'You\'ll earn {percent}% of the daily fee for every student you refer to \"{name}\".'**
  String referralCommissionEarned(String percent, String name);

  /// Shown when opening a downloaded file fails
  ///
  /// In en, this message translates to:
  /// **'Open failed: {error}'**
  String openFailed(String error);

  /// Generic failure message with the underlying error appended
  ///
  /// In en, this message translates to:
  /// **'Failed: {error}'**
  String failedWithError(String error);

  /// Count of files currently selected for upload
  ///
  /// In en, this message translates to:
  /// **'{count} selected'**
  String filesSelectedCount(int count);

  /// Comment sheet empty state title
  ///
  /// In en, this message translates to:
  /// **'No comments yet'**
  String get noCommentsYet;

  /// Comment sheet empty state subtitle
  ///
  /// In en, this message translates to:
  /// **'Be the first to share what you think'**
  String get beFirstToComment;

  /// Comment input field placeholder
  ///
  /// In en, this message translates to:
  /// **'Add a comment…'**
  String get addCommentHint;

  /// Shown above the comment input while replying to someone
  ///
  /// In en, this message translates to:
  /// **'Replying to @{name}'**
  String replyingTo(String name);

  /// Like action label on a comment
  ///
  /// In en, this message translates to:
  /// **'Like'**
  String get like;

  /// Reply action label on a comment
  ///
  /// In en, this message translates to:
  /// **'Reply'**
  String get reply;

  /// Button to expand a comment's replies
  ///
  /// In en, this message translates to:
  /// **'{count} replies'**
  String viewReplies(int count);

  /// Button to collapse a comment's replies
  ///
  /// In en, this message translates to:
  /// **'Hide replies'**
  String get hideReplies;

  /// Shown while a comment's replies are loading
  ///
  /// In en, this message translates to:
  /// **'Loading…'**
  String get loadingReplies;

  /// Marker next to an edited comment
  ///
  /// In en, this message translates to:
  /// **'edited'**
  String get edited;

  /// Marker next to the current user's own comment
  ///
  /// In en, this message translates to:
  /// **'you'**
  String get you;

  /// Title/hint for the edit-comment dialog
  ///
  /// In en, this message translates to:
  /// **'Edit comment'**
  String get editComment;

  /// Snackbar shown after a comment is edited
  ///
  /// In en, this message translates to:
  /// **'Comment updated'**
  String get commentEdited;

  /// Snackbar shown when editing a comment fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t edit: {error}'**
  String editFailed(String error);

  /// Title of the delete-comment confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'Delete comment?'**
  String get deleteComment;

  /// Body of the delete-comment confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'This can\'t be undone.'**
  String get deleteCommentBody;

  /// Generic delete action label
  ///
  /// In en, this message translates to:
  /// **'Delete'**
  String get delete;

  /// Snackbar shown when deleting a comment fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t delete: {error}'**
  String deleteFailed(String error);

  /// Action to hide someone else's comment on your own post
  ///
  /// In en, this message translates to:
  /// **'Hide comment'**
  String get hideComment;

  /// Snackbar shown after hiding a comment
  ///
  /// In en, this message translates to:
  /// **'Comment hidden'**
  String get commentHidden;

  /// Snackbar shown when hiding a comment fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t hide: {error}'**
  String hideFailed(String error);

  /// Attach-menu option to take a photo
  ///
  /// In en, this message translates to:
  /// **'Camera photo'**
  String get attachCameraPhoto;

  /// Attach-menu option to record a video
  ///
  /// In en, this message translates to:
  /// **'Camera video'**
  String get attachCameraVideo;

  /// Attach-menu option to pick a photo from the gallery
  ///
  /// In en, this message translates to:
  /// **'Gallery photo'**
  String get attachGalleryPhoto;

  /// Attach-menu option to pick a video from the gallery
  ///
  /// In en, this message translates to:
  /// **'Gallery video'**
  String get attachGalleryVideo;

  /// Attach-menu option to pick a document file
  ///
  /// In en, this message translates to:
  /// **'Document'**
  String get attachDocument;

  /// Title of the file-too-large dialog
  ///
  /// In en, this message translates to:
  /// **'File too large'**
  String get fileTooLargeTitle;

  /// Body of the file-too-large dialog/snackbar
  ///
  /// In en, this message translates to:
  /// **'This file is {size}MB — the limit is 200MB. Please choose a smaller file.'**
  String fileTooLargeBody(String size);

  /// Upload progress label
  ///
  /// In en, this message translates to:
  /// **'Uploading {percent}%'**
  String uploadingPercent(String percent);

  /// Shown before upload progress is known
  ///
  /// In en, this message translates to:
  /// **'Compressing…'**
  String get compressing;

  /// Snackbar shown when opening a downloaded comment attachment fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t open file: {error}'**
  String openFileFailed(String error);

  /// Generic OK button label
  ///
  /// In en, this message translates to:
  /// **'OK'**
  String get ok;

  /// Hint text on the story caption input
  ///
  /// In en, this message translates to:
  /// **'Add a caption…'**
  String get addCaptionHint;

  /// Generic download button label
  ///
  /// In en, this message translates to:
  /// **'Download'**
  String get download;

  /// Snackbar shown after a file finishes downloading
  ///
  /// In en, this message translates to:
  /// **'Downloaded: {fileName}'**
  String downloadedFile(String fileName);

  /// Snackbar shown when a file download fails
  ///
  /// In en, this message translates to:
  /// **'Download failed: {error}'**
  String downloadFailed(String error);

  /// Snackbar shown when a post reaction fails to sync
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t update your reaction — check your connection'**
  String get reactionUpdateFailed;

  /// Shown while a document is being prepared for viewing
  ///
  /// In en, this message translates to:
  /// **'Loading document…'**
  String get loadingDocument;

  /// Shown when a document type has no inline preview
  ///
  /// In en, this message translates to:
  /// **'Preview not supported for this file'**
  String get previewNotSupported;

  /// Button to download and open a document with no inline preview
  ///
  /// In en, this message translates to:
  /// **'Direct download & open'**
  String get directDownloadOpen;

  /// Button to open a document already downloaded locally
  ///
  /// In en, this message translates to:
  /// **'Open downloaded file'**
  String get openDownloadedFile;

  /// Tappable viewer count under your own story
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 viewer} other{{count} viewers}}'**
  String viewersCount(int count);

  /// Title of the story viewers list sheet
  ///
  /// In en, this message translates to:
  /// **'Viewers'**
  String get viewersTitle;

  /// Shown when the story viewers list fails to load
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load viewers.'**
  String get couldntLoadViewers;

  /// Shown when a story has no viewers yet
  ///
  /// In en, this message translates to:
  /// **'No views yet.'**
  String get noViewsYet;

  /// Source filter chip / badge: a test series created by an individual creator
  ///
  /// In en, this message translates to:
  /// **'Individual'**
  String get tsSourceIndividual;

  /// Source filter chip / badge: a test series published by a campus
  ///
  /// In en, this message translates to:
  /// **'Campus'**
  String get tsSourceCampus;

  /// Source filter chip / badge: a test series attached to a live class
  ///
  /// In en, this message translates to:
  /// **'Live Class'**
  String get tsSourceLiveClass;

  /// Hint text of the search field on the test series list
  ///
  /// In en, this message translates to:
  /// **'Search test series'**
  String get tsSearchHint;

  /// Empty state when a search on the test series list has no matches
  ///
  /// In en, this message translates to:
  /// **'No test series match your search'**
  String get tsNoSearchResults;

  /// Footer button shown when loading the next page of test series fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load more. Tap to retry.'**
  String get tsLoadMoreFailed;

  /// Test series error: device is offline / network unreachable
  ///
  /// In en, this message translates to:
  /// **'No internet connection. Check your network and try again.'**
  String get tsErrOffline;

  /// Test series error: request timed out
  ///
  /// In en, this message translates to:
  /// **'The server took too long to respond. Please try again.'**
  String get tsErrTimeout;

  /// Test series error: 401 / expired login session
  ///
  /// In en, this message translates to:
  /// **'Your session has expired. Please log in again.'**
  String get tsErrUnauthorized;

  /// Test series error: 403 forbidden
  ///
  /// In en, this message translates to:
  /// **'You don\'t have access to this test.'**
  String get tsErrForbidden;

  /// Test series error: 404 not found
  ///
  /// In en, this message translates to:
  /// **'This test is no longer available.'**
  String get tsErrNotFound;

  /// Test series error: 429 too many requests
  ///
  /// In en, this message translates to:
  /// **'Too many requests. Please wait a moment and try again.'**
  String get tsErrRateLimited;

  /// Test series error: 5xx server error
  ///
  /// In en, this message translates to:
  /// **'Something went wrong on our side. Please try again shortly.'**
  String get tsErrServer;

  /// Primary button on test series detail when the student can start another attempt
  ///
  /// In en, this message translates to:
  /// **'Attempt Again'**
  String get tsAttemptAgain;

  /// Attempts value when a test series allows unlimited attempts
  ///
  /// In en, this message translates to:
  /// **'Unlimited'**
  String get tsUnlimited;

  /// Attempts row on test series detail, e.g. '1 of 2 used'
  ///
  /// In en, this message translates to:
  /// **'{used} of {allowed} used'**
  String tsAttemptsUsedOf(int used, int allowed);

  /// Section title listing all of the student's attempts on a test series
  ///
  /// In en, this message translates to:
  /// **'Your Attempts'**
  String get tsAttemptHistory;

  /// Row title in the attempt history list
  ///
  /// In en, this message translates to:
  /// **'Attempt {n}'**
  String tsAttemptRow(int n);

  /// Banner shown on an archived test series the student has not attempted
  ///
  /// In en, this message translates to:
  /// **'This test series is archived and can\'t be started.'**
  String get tsSeriesArchived;

  /// Button under the review preview that opens the full reviews screen
  ///
  /// In en, this message translates to:
  /// **'View All Reviews ({count})'**
  String tsViewAllReviews(int count);

  /// App bar title of the full reviews screen
  ///
  /// In en, this message translates to:
  /// **'Reviews'**
  String get tsReviewsTitle;

  /// Snack shown when a test that could not be submitted earlier was sent successfully
  ///
  /// In en, this message translates to:
  /// **'Your pending test was submitted.'**
  String get tsPendingSynced;

  /// Shown in place of an input for a question type this app version does not know
  ///
  /// In en, this message translates to:
  /// **'This question type isn\'t supported in your app version. Please update the app.'**
  String get tsQuestionUnsupported;

  /// Accessibility label of an image attached to a question
  ///
  /// In en, this message translates to:
  /// **'Question image'**
  String get tsQuestionImage;

  /// Tooltip of the flag button in the test player
  ///
  /// In en, this message translates to:
  /// **'Mark for Review'**
  String get tsMarkForReview;

  /// Tooltip of the flag button when the question is already marked
  ///
  /// In en, this message translates to:
  /// **'Remove Review Mark'**
  String get tsUnmarkReview;

  /// Question palette legend: answered question
  ///
  /// In en, this message translates to:
  /// **'Answered'**
  String get tsLegendAnswered;

  /// Question palette legend: unanswered question
  ///
  /// In en, this message translates to:
  /// **'Not answered'**
  String get tsLegendNotAnswered;

  /// Question palette legend: question marked for review
  ///
  /// In en, this message translates to:
  /// **'Marked'**
  String get tsLegendMarked;

  /// Note above an ordering question until the student rearranges it at least once
  ///
  /// In en, this message translates to:
  /// **'Not arranged yet — drag to set the order'**
  String get tsOrderNotArranged;

  /// Extra line in the submit confirmation dialog when questions are marked for review
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 question is marked for review.} other{{count} questions are marked for review.}}'**
  String tsSubmitConfirmMarked(int count);

  /// Snack shown when 10, 5 and 1 minutes remain in a timed test
  ///
  /// In en, this message translates to:
  /// **'{minutes, plural, =1{1 min left} other{{minutes} min left}}'**
  String tsTimeLeftWarning(int minutes);

  /// Banner shown while answers are auto-submitted after the timer ends
  ///
  /// In en, this message translates to:
  /// **'Time\'s up. Submitting your answers…'**
  String get tsTimeUpLocked;

  /// Title of the banner shown when auto-submit failed and the submission is queued
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t submit yet'**
  String get tsSubmitPendingTitle;

  /// Body of the queued-submission banner
  ///
  /// In en, this message translates to:
  /// **'Your answers are saved safely on this device. Check your connection and retry.'**
  String get tsSubmitPendingBody;

  /// Button on the queued-submission banner
  ///
  /// In en, this message translates to:
  /// **'Retry'**
  String get tsRetrySubmit;

  /// Exit confirmation body for an untimed test (answers are autosaved)
  ///
  /// In en, this message translates to:
  /// **'Your answers are saved. You can resume this test later.'**
  String get tsExitBody;

  /// Exit confirmation body for a timed test (timer does not pause)
  ///
  /// In en, this message translates to:
  /// **'Your answers are saved, but the timer keeps running. You can resume from the test page.'**
  String get tsExitBodyTimerRunning;

  /// Snack shown when locally saved answers are restored on resume
  ///
  /// In en, this message translates to:
  /// **'Your previous answers were restored.'**
  String get tsDraftRestored;

  /// Screen reader label of the countdown timer
  ///
  /// In en, this message translates to:
  /// **'Time remaining {time}'**
  String tsSemanticsTimer(String time);

  /// Photo source option in the attach-photo sheet
  ///
  /// In en, this message translates to:
  /// **'Camera'**
  String get tsFromCamera;

  /// Photo source option in the attach-photo sheet
  ///
  /// In en, this message translates to:
  /// **'Gallery'**
  String get tsFromGallery;

  /// Tooltip to remove an attached photo from a text answer
  ///
  /// In en, this message translates to:
  /// **'Remove Photo'**
  String get tsRemovePhoto;

  /// Error when the chosen photo exceeds the size limit
  ///
  /// In en, this message translates to:
  /// **'Photo is larger than {mb} MB. Please choose a smaller one.'**
  String tsPhotoTooLarge(int mb);

  /// Error when picking a photo fails (usually a denied permission)
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t open the camera or gallery. Check app permissions in Settings.'**
  String get tsPhotoError;

  /// Shown when a photo saved in a restored draft no longer exists on the device
  ///
  /// In en, this message translates to:
  /// **'An attached photo is no longer available. Please attach it again.'**
  String get tsPhotoMissing;

  /// Label of the correct answer block in the result breakdown
  ///
  /// In en, this message translates to:
  /// **'Correct Answer'**
  String get tsCorrectAnswer;

  /// Label for the student's uploaded photo in the result breakdown
  ///
  /// In en, this message translates to:
  /// **'Your Attached Photo'**
  String get tsYourPhoto;

  /// Result summary chip: number of correct answers
  ///
  /// In en, this message translates to:
  /// **'Correct: {count}'**
  String tsSummaryCorrect(int count);

  /// Result summary chip: number of incorrect answers
  ///
  /// In en, this message translates to:
  /// **'Incorrect: {count}'**
  String tsSummaryIncorrect(int count);

  /// Result summary chip: number of unanswered questions
  ///
  /// In en, this message translates to:
  /// **'Skipped: {count}'**
  String tsSummarySkipped(int count);

  /// Result summary chip: answers waiting for manual checking
  ///
  /// In en, this message translates to:
  /// **'Awaiting review: {count}'**
  String tsSummaryAwaiting(int count);

  /// Language section header in settings
  ///
  /// In en, this message translates to:
  /// **'Language'**
  String get languageSectionTitle;

  /// Theme section header in settings
  ///
  /// In en, this message translates to:
  /// **'Theme'**
  String get themeSectionTitle;

  /// No description provided for @notificationSettingsTitle.
  ///
  /// In en, this message translates to:
  /// **'Notification settings'**
  String get notificationSettingsTitle;

  /// No description provided for @notificationSettingsLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load notification settings'**
  String get notificationSettingsLoadFailed;

  /// No description provided for @notifChannelsSectionTitle.
  ///
  /// In en, this message translates to:
  /// **'Channels'**
  String get notifChannelsSectionTitle;

  /// No description provided for @notifChannelPush.
  ///
  /// In en, this message translates to:
  /// **'Push notifications'**
  String get notifChannelPush;

  /// No description provided for @notifChannelEmail.
  ///
  /// In en, this message translates to:
  /// **'Email'**
  String get notifChannelEmail;

  /// No description provided for @notifChannelSms.
  ///
  /// In en, this message translates to:
  /// **'SMS'**
  String get notifChannelSms;

  /// No description provided for @notifChannelWhatsapp.
  ///
  /// In en, this message translates to:
  /// **'WhatsApp'**
  String get notifChannelWhatsapp;

  /// No description provided for @notifDigestSectionTitle.
  ///
  /// In en, this message translates to:
  /// **'Email digest'**
  String get notifDigestSectionTitle;

  /// No description provided for @notifDigestOff.
  ///
  /// In en, this message translates to:
  /// **'Off'**
  String get notifDigestOff;

  /// No description provided for @notifDigestDaily.
  ///
  /// In en, this message translates to:
  /// **'Daily'**
  String get notifDigestDaily;

  /// No description provided for @notifDigestWeekly.
  ///
  /// In en, this message translates to:
  /// **'Weekly'**
  String get notifDigestWeekly;

  /// No description provided for @notifCategoriesSectionTitle.
  ///
  /// In en, this message translates to:
  /// **'Notify me about'**
  String get notifCategoriesSectionTitle;

  /// No description provided for @notifCategoriesHint.
  ///
  /// In en, this message translates to:
  /// **'Turning a category off still shows it in your notification list — it just won\'t send a push, email, SMS or WhatsApp alert.'**
  String get notifCategoriesHint;

  /// No description provided for @notifCategoryLiveClasses.
  ///
  /// In en, this message translates to:
  /// **'Live classes & sessions'**
  String get notifCategoryLiveClasses;

  /// No description provided for @notifCategoryAssignmentsTests.
  ///
  /// In en, this message translates to:
  /// **'Assignments & tests'**
  String get notifCategoryAssignmentsTests;

  /// No description provided for @notifCategoryMessagesCalls.
  ///
  /// In en, this message translates to:
  /// **'Messages & calls'**
  String get notifCategoryMessagesCalls;

  /// No description provided for @notifCategorySocial.
  ///
  /// In en, this message translates to:
  /// **'Posts & reviews'**
  String get notifCategorySocial;

  /// No description provided for @notifCategoryPayments.
  ///
  /// In en, this message translates to:
  /// **'Payments & wallet'**
  String get notifCategoryPayments;

  /// No description provided for @notifCategoryCampus.
  ///
  /// In en, this message translates to:
  /// **'Campus notices'**
  String get notifCategoryCampus;

  /// No description provided for @languageEnglish.
  ///
  /// In en, this message translates to:
  /// **'English'**
  String get languageEnglish;

  /// No description provided for @languageHindi.
  ///
  /// In en, this message translates to:
  /// **'Hindi'**
  String get languageHindi;

  /// No description provided for @languageSystemDefault.
  ///
  /// In en, this message translates to:
  /// **'System default'**
  String get languageSystemDefault;

  /// No description provided for @themeLight.
  ///
  /// In en, this message translates to:
  /// **'Light'**
  String get themeLight;

  /// No description provided for @themeDark.
  ///
  /// In en, this message translates to:
  /// **'Dark'**
  String get themeDark;

  /// No description provided for @themeSystem.
  ///
  /// In en, this message translates to:
  /// **'System default'**
  String get themeSystem;

  /// Classrooms browse screen title
  ///
  /// In en, this message translates to:
  /// **'Live Classes'**
  String get liveClassesTitle;

  /// No description provided for @searchClassroomsHint.
  ///
  /// In en, this message translates to:
  /// **'Search classrooms'**
  String get searchClassroomsHint;

  /// No description provided for @filterAll.
  ///
  /// In en, this message translates to:
  /// **'All'**
  String get filterAll;

  /// No description provided for @filterMine.
  ///
  /// In en, this message translates to:
  /// **'Mine'**
  String get filterMine;

  /// No description provided for @couldNotLoadClassrooms.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load classrooms'**
  String get couldNotLoadClassrooms;

  /// No description provided for @checkConnectionRetry.
  ///
  /// In en, this message translates to:
  /// **'Check your connection and try again'**
  String get checkConnectionRetry;

  /// No description provided for @noClassroomsFound.
  ///
  /// In en, this message translates to:
  /// **'No classrooms found'**
  String get noClassroomsFound;

  /// No description provided for @tryDifferentSearch.
  ///
  /// In en, this message translates to:
  /// **'Try a different search or filter'**
  String get tryDifferentSearch;

  /// Enrolled student count on a classroom card
  ///
  /// In en, this message translates to:
  /// **'{count} enrolled'**
  String enrolledCountLabel(int count);

  /// No description provided for @classroomDetailTitle.
  ///
  /// In en, this message translates to:
  /// **'Classroom'**
  String get classroomDetailTitle;

  /// No description provided for @upcomingSessionsTitle.
  ///
  /// In en, this message translates to:
  /// **'Upcoming Sessions'**
  String get upcomingSessionsTitle;

  /// No description provided for @noUpcomingSessions.
  ///
  /// In en, this message translates to:
  /// **'No upcoming sessions'**
  String get noUpcomingSessions;

  /// No description provided for @materialsTitle.
  ///
  /// In en, this message translates to:
  /// **'Materials'**
  String get materialsTitle;

  /// No description provided for @noMaterialsYet.
  ///
  /// In en, this message translates to:
  /// **'No materials yet'**
  String get noMaterialsYet;

  /// No description provided for @viewScheduleCta.
  ///
  /// In en, this message translates to:
  /// **'View schedule'**
  String get viewScheduleCta;

  /// No description provided for @requestToJoinCta.
  ///
  /// In en, this message translates to:
  /// **'Request to join'**
  String get requestToJoinCta;

  /// No description provided for @choosePassTitle.
  ///
  /// In en, this message translates to:
  /// **'Choose a pass'**
  String get choosePassTitle;

  /// Pass option subtitle: price in coins and validity in days
  ///
  /// In en, this message translates to:
  /// **'{price} coins - valid {days} days'**
  String passSubtitle(String price, int days);

  /// No description provided for @couponCodeOptional.
  ///
  /// In en, this message translates to:
  /// **'Coupon code (optional)'**
  String get couponCodeOptional;

  /// No description provided for @messageToTeacherOptional.
  ///
  /// In en, this message translates to:
  /// **'Message to teacher (optional)'**
  String get messageToTeacherOptional;

  /// No description provided for @sendRequestCta.
  ///
  /// In en, this message translates to:
  /// **'Send request'**
  String get sendRequestCta;

  /// No description provided for @liveSessionTitle.
  ///
  /// In en, this message translates to:
  /// **'Live Session'**
  String get liveSessionTitle;

  /// No description provided for @couldNotJoinSession.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t join the session'**
  String get couldNotJoinSession;

  /// Short badge shown while a session recording is in progress
  ///
  /// In en, this message translates to:
  /// **'REC'**
  String get recordingBadge;

  /// No description provided for @raiseHandCta.
  ///
  /// In en, this message translates to:
  /// **'Raise hand'**
  String get raiseHandCta;

  /// No description provided for @typeMessageHint.
  ///
  /// In en, this message translates to:
  /// **'Type a message'**
  String get typeMessageHint;

  /// No description provided for @dashboardTitle.
  ///
  /// In en, this message translates to:
  /// **'Home'**
  String get dashboardTitle;

  /// No description provided for @couldNotLoadDashboard.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load your dashboard'**
  String get couldNotLoadDashboard;

  /// No description provided for @viewerCountLabel.
  ///
  /// In en, this message translates to:
  /// **'{count} watching'**
  String viewerCountLabel(int count);

  /// No description provided for @myProgressTitle.
  ///
  /// In en, this message translates to:
  /// **'My Progress'**
  String get myProgressTitle;

  /// No description provided for @attendanceLabel.
  ///
  /// In en, this message translates to:
  /// **'Attendance'**
  String get attendanceLabel;

  /// No description provided for @streakLabel.
  ///
  /// In en, this message translates to:
  /// **'Day streak'**
  String get streakLabel;

  /// No description provided for @certificatesLabel.
  ///
  /// In en, this message translates to:
  /// **'Certificates'**
  String get certificatesLabel;

  /// No description provided for @myEarningsTitle.
  ///
  /// In en, this message translates to:
  /// **'My Earnings'**
  String get myEarningsTitle;

  /// No description provided for @totalEarnedLabel.
  ///
  /// In en, this message translates to:
  /// **'Total earned'**
  String get totalEarnedLabel;

  /// No description provided for @walletTitle.
  ///
  /// In en, this message translates to:
  /// **'Wallet'**
  String get walletTitle;

  /// No description provided for @couldNotLoadWallet.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load your wallet'**
  String get couldNotLoadWallet;

  /// No description provided for @coinBalanceLabel.
  ///
  /// In en, this message translates to:
  /// **'Coin balance'**
  String get coinBalanceLabel;

  /// No description provided for @buyCoinsCta.
  ///
  /// In en, this message translates to:
  /// **'Buy coins'**
  String get buyCoinsCta;

  /// No description provided for @buyCoinsTitle.
  ///
  /// In en, this message translates to:
  /// **'Buy coins'**
  String get buyCoinsTitle;

  /// No description provided for @withdrawCta.
  ///
  /// In en, this message translates to:
  /// **'Withdraw'**
  String get withdrawCta;

  /// No description provided for @cancelCta.
  ///
  /// In en, this message translates to:
  /// **'Cancel'**
  String get cancelCta;

  /// No description provided for @continueCta.
  ///
  /// In en, this message translates to:
  /// **'Continue'**
  String get continueCta;

  /// No description provided for @orderStartedMessage.
  ///
  /// In en, this message translates to:
  /// **'Order started - complete payment to add coins'**
  String get orderStartedMessage;

  /// No description provided for @transactionsTitle.
  ///
  /// In en, this message translates to:
  /// **'Transactions'**
  String get transactionsTitle;

  /// No description provided for @noTransactionsYet.
  ///
  /// In en, this message translates to:
  /// **'No transactions yet'**
  String get noTransactionsYet;

  /// No description provided for @withdrawalsTitle.
  ///
  /// In en, this message translates to:
  /// **'Withdrawals'**
  String get withdrawalsTitle;

  /// No description provided for @coinsUnit.
  ///
  /// In en, this message translates to:
  /// **'coins'**
  String get coinsUnit;

  /// No description provided for @actionCta.
  ///
  /// In en, this message translates to:
  /// **'Take action'**
  String get actionCta;

  /// No description provided for @answerCta.
  ///
  /// In en, this message translates to:
  /// **'Answer'**
  String get answerCta;

  /// No description provided for @answerDoubtTitle.
  ///
  /// In en, this message translates to:
  /// **'Answer doubt'**
  String get answerDoubtTitle;

  /// No description provided for @askCta.
  ///
  /// In en, this message translates to:
  /// **'Ask'**
  String get askCta;

  /// No description provided for @askDoubtTitle.
  ///
  /// In en, this message translates to:
  /// **'Ask a doubt'**
  String get askDoubtTitle;

  /// No description provided for @assignRoomHint.
  ///
  /// In en, this message translates to:
  /// **'Room'**
  String get assignRoomHint;

  /// No description provided for @assignmentsTitle.
  ///
  /// In en, this message translates to:
  /// **'Assignments'**
  String get assignmentsTitle;

  /// No description provided for @breakoutRoomsTitle.
  ///
  /// In en, this message translates to:
  /// **'Breakout Rooms'**
  String get breakoutRoomsTitle;

  /// No description provided for @certificatesReportCardsTitle.
  ///
  /// In en, this message translates to:
  /// **'Certificates & Report Cards'**
  String get certificatesReportCardsTitle;

  /// No description provided for @certificatesTab.
  ///
  /// In en, this message translates to:
  /// **'Certificates'**
  String get certificatesTab;

  /// No description provided for @chatReportsTab.
  ///
  /// In en, this message translates to:
  /// **'Chat Reports'**
  String get chatReportsTab;

  /// No description provided for @classroomInfoTitle.
  ///
  /// In en, this message translates to:
  /// **'Classroom Info'**
  String get classroomInfoTitle;

  /// No description provided for @classroomReferralSummaryTitle.
  ///
  /// In en, this message translates to:
  /// **'Classroom Referral Summary'**
  String get classroomReferralSummaryTitle;

  /// No description provided for @classroomReportsTab.
  ///
  /// In en, this message translates to:
  /// **'Classroom Reports'**
  String get classroomReportsTab;

  /// No description provided for @closeBreakoutCta.
  ///
  /// In en, this message translates to:
  /// **'Close rooms'**
  String get closeBreakoutCta;

  /// No description provided for @commissionEarnedLabel.
  ///
  /// In en, this message translates to:
  /// **'Commission earned'**
  String get commissionEarnedLabel;

  /// No description provided for @couldNotLoadAssignments.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load assignments'**
  String get couldNotLoadAssignments;

  /// No description provided for @couldNotLoadModerationData.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load moderation data'**
  String get couldNotLoadModerationData;

  /// No description provided for @couldNotLoadPasses.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load passes'**
  String get couldNotLoadPasses;

  /// No description provided for @couldNotLoadRecordings.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load recordings'**
  String get couldNotLoadRecordings;

  /// No description provided for @couldNotLoadReferrals.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load referrals'**
  String get couldNotLoadReferrals;

  /// No description provided for @createCta.
  ///
  /// In en, this message translates to:
  /// **'Create'**
  String get createCta;

  /// Abbreviation for days, e.g. '30d'
  ///
  /// In en, this message translates to:
  /// **'d'**
  String get daysAbbrev;

  /// No description provided for @dismissCta.
  ///
  /// In en, this message translates to:
  /// **'Dismiss'**
  String get dismissCta;

  /// No description provided for @doneCta.
  ///
  /// In en, this message translates to:
  /// **'Done'**
  String get doneCta;

  /// No description provided for @doubtsTab.
  ///
  /// In en, this message translates to:
  /// **'Doubts'**
  String get doubtsTab;

  /// No description provided for @enterReferralCodeHint.
  ///
  /// In en, this message translates to:
  /// **'Enter referral code'**
  String get enterReferralCodeHint;

  /// No description provided for @generateParentCodeCta.
  ///
  /// In en, this message translates to:
  /// **'Generate parent code'**
  String get generateParentCodeCta;

  /// No description provided for @giftsTitle.
  ///
  /// In en, this message translates to:
  /// **'Gifts'**
  String get giftsTitle;

  /// No description provided for @gradeCta.
  ///
  /// In en, this message translates to:
  /// **'Grade'**
  String get gradeCta;

  /// No description provided for @gradeSubmissionTitle.
  ///
  /// In en, this message translates to:
  /// **'Grade submission'**
  String get gradeSubmissionTitle;

  /// No description provided for @holidaysTab.
  ///
  /// In en, this message translates to:
  /// **'Holidays'**
  String get holidaysTab;

  /// No description provided for @homeworkLabel.
  ///
  /// In en, this message translates to:
  /// **'Homework'**
  String get homeworkLabel;

  /// No description provided for @issueCertificateCta.
  ///
  /// In en, this message translates to:
  /// **'Issue certificate'**
  String get issueCertificateCta;

  /// No description provided for @mainRoomLabel.
  ///
  /// In en, this message translates to:
  /// **'Main room'**
  String get mainRoomLabel;

  /// No description provided for @managePassesTitle.
  ///
  /// In en, this message translates to:
  /// **'Manage Passes'**
  String get managePassesTitle;

  /// No description provided for @marksLabel.
  ///
  /// In en, this message translates to:
  /// **'Marks'**
  String get marksLabel;

  /// No description provided for @moderationTitle.
  ///
  /// In en, this message translates to:
  /// **'Moderation'**
  String get moderationTitle;

  /// No description provided for @newPassTitle.
  ///
  /// In en, this message translates to:
  /// **'New pass'**
  String get newPassTitle;

  /// No description provided for @noAssignmentsYet.
  ///
  /// In en, this message translates to:
  /// **'No assignments yet'**
  String get noAssignmentsYet;

  /// No description provided for @noCertificatesYet.
  ///
  /// In en, this message translates to:
  /// **'No certificates yet'**
  String get noCertificatesYet;

  /// No description provided for @noChatReportsPending.
  ///
  /// In en, this message translates to:
  /// **'No chat reports pending'**
  String get noChatReportsPending;

  /// No description provided for @noClassroomReportsPending.
  ///
  /// In en, this message translates to:
  /// **'No classroom reports pending'**
  String get noClassroomReportsPending;

  /// No description provided for @noDoubtsYet.
  ///
  /// In en, this message translates to:
  /// **'No doubts asked yet'**
  String get noDoubtsYet;

  /// No description provided for @noGiftsYet.
  ///
  /// In en, this message translates to:
  /// **'No gifts yet'**
  String get noGiftsYet;

  /// No description provided for @noHolidaysListed.
  ///
  /// In en, this message translates to:
  /// **'No holidays listed'**
  String get noHolidaysListed;

  /// No description provided for @noNoticesYet.
  ///
  /// In en, this message translates to:
  /// **'No notices yet'**
  String get noNoticesYet;

  /// No description provided for @noParentQueriesYet.
  ///
  /// In en, this message translates to:
  /// **'No parent queries yet'**
  String get noParentQueriesYet;

  /// No description provided for @noParticipantsYet.
  ///
  /// In en, this message translates to:
  /// **'No participants yet'**
  String get noParticipantsYet;

  /// No description provided for @noPassesYet.
  ///
  /// In en, this message translates to:
  /// **'No passes yet'**
  String get noPassesYet;

  /// No description provided for @noRecordingsYet.
  ///
  /// In en, this message translates to:
  /// **'No recordings yet'**
  String get noRecordingsYet;

  /// No description provided for @noReferralsYet.
  ///
  /// In en, this message translates to:
  /// **'No referrals yet'**
  String get noReferralsYet;

  /// No description provided for @noReportCardsYet.
  ///
  /// In en, this message translates to:
  /// **'No report cards yet'**
  String get noReportCardsYet;

  /// No description provided for @noSubmissionsYet.
  ///
  /// In en, this message translates to:
  /// **'No submissions yet'**
  String get noSubmissionsYet;

  /// No description provided for @noticesTab.
  ///
  /// In en, this message translates to:
  /// **'Notices'**
  String get noticesTab;

  /// No description provided for @parentCodeGeneratedTitle.
  ///
  /// In en, this message translates to:
  /// **'Parent access code'**
  String get parentCodeGeneratedTitle;

  /// No description provided for @parentObserverConnectedMessage.
  ///
  /// In en, this message translates to:
  /// **'Connected — you can now watch the class'**
  String get parentObserverConnectedMessage;

  /// No description provided for @parentQueriesTitle.
  ///
  /// In en, this message translates to:
  /// **'Parent Queries'**
  String get parentQueriesTitle;

  /// No description provided for @participantsTab.
  ///
  /// In en, this message translates to:
  /// **'Participants'**
  String get participantsTab;

  /// No description provided for @passTitleLabel.
  ///
  /// In en, this message translates to:
  /// **'Pass title'**
  String get passTitleLabel;

  /// No description provided for @passesTitle.
  ///
  /// In en, this message translates to:
  /// **'Passes'**
  String get passesTitle;

  /// No description provided for @peopleYouReferredTitle.
  ///
  /// In en, this message translates to:
  /// **'People you referred'**
  String get peopleYouReferredTitle;

  /// No description provided for @priceInCoinsLabel.
  ///
  /// In en, this message translates to:
  /// **'Price (coins)'**
  String get priceInCoinsLabel;

  /// No description provided for @recordingsPendingNote.
  ///
  /// In en, this message translates to:
  /// **'{count} recording(s) still processing'**
  String recordingsPendingNote(int count);

  /// No description provided for @recordingsTitle.
  ///
  /// In en, this message translates to:
  /// **'Recordings'**
  String get recordingsTitle;

  /// No description provided for @redeemCta.
  ///
  /// In en, this message translates to:
  /// **'Redeem'**
  String get redeemCta;

  /// No description provided for @referAndEarnTitle.
  ///
  /// In en, this message translates to:
  /// **'Refer & Earn'**
  String get referAndEarnTitle;

  /// No description provided for @referralRedeemedMessage.
  ///
  /// In en, this message translates to:
  /// **'Referral code redeemed'**
  String get referralRedeemedMessage;

  /// No description provided for @replyCta.
  ///
  /// In en, this message translates to:
  /// **'Reply'**
  String get replyCta;

  /// No description provided for @replyToParentTitle.
  ///
  /// In en, this message translates to:
  /// **'Reply to parent'**
  String get replyToParentTitle;

  /// No description provided for @reportCardsTab.
  ///
  /// In en, this message translates to:
  /// **'Report Cards'**
  String get reportCardsTab;

  /// No description provided for @roomNumberLabel.
  ///
  /// In en, this message translates to:
  /// **'Room {n}'**
  String roomNumberLabel(int n);

  /// No description provided for @roomsActiveLabel.
  ///
  /// In en, this message translates to:
  /// **'{count} rooms active'**
  String roomsActiveLabel(int count);

  /// No description provided for @roomsCountLabel.
  ///
  /// In en, this message translates to:
  /// **'{n} rooms'**
  String roomsCountLabel(int n);

  /// No description provided for @saveCta.
  ///
  /// In en, this message translates to:
  /// **'Save'**
  String get saveCta;

  /// No description provided for @sendCta.
  ///
  /// In en, this message translates to:
  /// **'Send'**
  String get sendCta;

  /// No description provided for @timesRedeemedLabel.
  ///
  /// In en, this message translates to:
  /// **'Redeemed {count} times'**
  String timesRedeemedLabel(int count);

  /// No description provided for @validityDaysLabel.
  ///
  /// In en, this message translates to:
  /// **'Validity (days)'**
  String get validityDaysLabel;

  /// No description provided for @yourReferralCodeLabel.
  ///
  /// In en, this message translates to:
  /// **'Your referral code'**
  String get yourReferralCodeLabel;

  /// No description provided for @submitAnswerTitle.
  ///
  /// In en, this message translates to:
  /// **'Submit your answer'**
  String get submitAnswerTitle;

  /// No description provided for @submitCta.
  ///
  /// In en, this message translates to:
  /// **'Submit'**
  String get submitCta;

  /// No description provided for @newCouponTitle.
  ///
  /// In en, this message translates to:
  /// **'New coupon'**
  String get newCouponTitle;

  /// No description provided for @couponCodeLabel.
  ///
  /// In en, this message translates to:
  /// **'Coupon code'**
  String get couponCodeLabel;

  /// No description provided for @discountPercentLabel.
  ///
  /// In en, this message translates to:
  /// **'Discount %'**
  String get discountPercentLabel;

  /// No description provided for @validForDaysLabel.
  ///
  /// In en, this message translates to:
  /// **'Valid for (days)'**
  String get validForDaysLabel;

  /// No description provided for @maxUsesOptionalLabel.
  ///
  /// In en, this message translates to:
  /// **'Max uses (optional)'**
  String get maxUsesOptionalLabel;

  /// No description provided for @couponsTab.
  ///
  /// In en, this message translates to:
  /// **'Coupons'**
  String get couponsTab;

  /// No description provided for @noCouponsYet.
  ///
  /// In en, this message translates to:
  /// **'No coupons yet'**
  String get noCouponsYet;

  /// No description provided for @discountPercentValueLabel.
  ///
  /// In en, this message translates to:
  /// **'{percent}% off'**
  String discountPercentValueLabel(int percent);

  /// No description provided for @discountAmountValueLabel.
  ///
  /// In en, this message translates to:
  /// **'{amount} coins off'**
  String discountAmountValueLabel(String amount);

  /// No description provided for @couponUsageLabel.
  ///
  /// In en, this message translates to:
  /// **'Used {used} / {max}'**
  String couponUsageLabel(int used, String max);

  /// No description provided for @wishlistTitle.
  ///
  /// In en, this message translates to:
  /// **'Wishlist'**
  String get wishlistTitle;

  /// No description provided for @couldNotLoadWishlist.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load your wishlist'**
  String get couldNotLoadWishlist;

  /// No description provided for @wishlistEmptyTitle.
  ///
  /// In en, this message translates to:
  /// **'Your wishlist is empty'**
  String get wishlistEmptyTitle;

  /// No description provided for @wishlistEmptySubtitle.
  ///
  /// In en, this message translates to:
  /// **'Tap the heart on a classroom to save it here'**
  String get wishlistEmptySubtitle;

  /// No description provided for @attachFileOptionalCta.
  ///
  /// In en, this message translates to:
  /// **'Attach file (optional)'**
  String get attachFileOptionalCta;

  /// Snackbar shown when a saved Quick Post draft is restored
  ///
  /// In en, this message translates to:
  /// **'Draft restored'**
  String get draftRestored;

  /// Shown when trying to add a photo while a video is already attached
  ///
  /// In en, this message translates to:
  /// **'Remove the video first'**
  String get removeVideoFirst;

  /// Shown when the media attachment limit is reached
  ///
  /// In en, this message translates to:
  /// **'Max {count} media allowed'**
  String maxMediaAllowed(int count);

  /// Shown when a multi-select picked more images than the remaining slots
  ///
  /// In en, this message translates to:
  /// **'Only {count} more could be added'**
  String onlyNMoreCouldBeAdded(int count);

  /// Shown when picking from the gallery fails
  ///
  /// In en, this message translates to:
  /// **'Gallery error: {error}'**
  String galleryError(String error);

  /// Shown when taking a camera photo fails
  ///
  /// In en, this message translates to:
  /// **'Camera error: {error}'**
  String cameraError(String error);

  /// Shown when trying to add a video while photos are already attached
  ///
  /// In en, this message translates to:
  /// **'Remove images first for video'**
  String get removeImagesFirst;

  /// Shown when picking/recording a video fails
  ///
  /// In en, this message translates to:
  /// **'Video error: {error}'**
  String videoError(String error);

  /// Title of the Quick Post attach-media sheet
  ///
  /// In en, this message translates to:
  /// **'Add to post'**
  String get addToPost;

  /// Option to pick a photo from the gallery
  ///
  /// In en, this message translates to:
  /// **'Gallery'**
  String get gallery;

  /// Option to pick a video from the gallery
  ///
  /// In en, this message translates to:
  /// **'Video from gallery'**
  String get videoFromGallery;

  /// Option to attach a GIF or sticker
  ///
  /// In en, this message translates to:
  /// **'GIF / Sticker'**
  String get gifSticker;

  /// Shown when tapping the GIF/Sticker option
  ///
  /// In en, this message translates to:
  /// **'GIF integration coming soon!'**
  String get gifComingSoon;

  /// Shown after toggling off the simulated voice recorder
  ///
  /// In en, this message translates to:
  /// **'Voice note attached (simulated)'**
  String get voiceNoteAttached;

  /// Title of the discard-draft confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'Discard post?'**
  String get discardPostTitle;

  /// Body of the discard-draft confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'Your draft is saved. You can continue later.'**
  String get discardPostBody;

  /// Button to dismiss the discard-draft dialog and keep writing
  ///
  /// In en, this message translates to:
  /// **'Keep editing'**
  String get keepEditing;

  /// Button to discard the current draft
  ///
  /// In en, this message translates to:
  /// **'Discard'**
  String get discard;

  /// Shown when trying to post with no text and no media
  ///
  /// In en, this message translates to:
  /// **'Write something or add media'**
  String get writeSomethingOrAddMedia;

  /// Shown when the post text exceeds the character limit
  ///
  /// In en, this message translates to:
  /// **'Post can\'t be longer than {count} characters'**
  String postTooLong(int count);

  /// Shown when trying to post before categories have loaded
  ///
  /// In en, this message translates to:
  /// **'Categories are loading…'**
  String get categoriesLoading;

  /// Shown when trying to post and category loading failed
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load a category'**
  String get categoryLoadFailed;

  /// Post button label while media is uploading
  ///
  /// In en, this message translates to:
  /// **'Uploading media…'**
  String get uploadingMedia;

  /// Post button label while a text-only post is submitting
  ///
  /// In en, this message translates to:
  /// **'Posting…'**
  String get posting;

  /// Fallback success message after creating a post
  ///
  /// In en, this message translates to:
  /// **'Post created successfully!'**
  String get postCreatedSuccess;

  /// Hint text on the Quick Post text field
  ///
  /// In en, this message translates to:
  /// **'What\'s on your mind?'**
  String get whatsOnYourMind;

  /// Tooltip on the attach-media button
  ///
  /// In en, this message translates to:
  /// **'Add photo or video'**
  String get addPhotoOrVideoTooltip;

  /// Tooltip on the emoji-grid toggle button
  ///
  /// In en, this message translates to:
  /// **'Emojis'**
  String get emojisTooltip;

  /// Tooltip on the voice-note record button
  ///
  /// In en, this message translates to:
  /// **'Voice note'**
  String get voiceNoteTooltip;

  /// Post visibility option: everyone
  ///
  /// In en, this message translates to:
  /// **'Public'**
  String get visibilityPublic;

  /// Post visibility option: connections only
  ///
  /// In en, this message translates to:
  /// **'Network'**
  String get visibilityNetwork;

  /// Post visibility option: only me
  ///
  /// In en, this message translates to:
  /// **'Private'**
  String get visibilityPrivate;

  /// Title of the Quick Post composer screen
  ///
  /// In en, this message translates to:
  /// **'Quick Post'**
  String get quickPostTitle;

  /// Draft autosave indicator, e.g. 'Saved 2m ago'
  ///
  /// In en, this message translates to:
  /// **'Saved {time}'**
  String savedAgo(String time);

  /// Button label to publish a post
  ///
  /// In en, this message translates to:
  /// **'Post'**
  String get postButton;

  /// Success overlay text right after publishing
  ///
  /// In en, this message translates to:
  /// **'Posted!'**
  String get postedExclaim;

  /// Relative time: less than a minute ago
  ///
  /// In en, this message translates to:
  /// **'just now'**
  String get timeAgoJustNow;

  /// Relative time in minutes
  ///
  /// In en, this message translates to:
  /// **'{count}m ago'**
  String timeAgoMinutes(int count);

  /// Relative time in hours
  ///
  /// In en, this message translates to:
  /// **'{count}h ago'**
  String timeAgoHours(int count);

  /// Relative time in days
  ///
  /// In en, this message translates to:
  /// **'{count}d ago'**
  String timeAgoDays(int count);

  /// No description provided for @addLocationSheetTitle.
  ///
  /// In en, this message translates to:
  /// **'Add Location'**
  String get addLocationSheetTitle;

  /// No description provided for @addLocationTagPlaceholder.
  ///
  /// In en, this message translates to:
  /// **'Add location tag'**
  String get addLocationTagPlaceholder;

  /// No description provided for @addOptionLabel.
  ///
  /// In en, this message translates to:
  /// **'Add option'**
  String get addOptionLabel;

  /// No description provided for @addSomeContentFirst.
  ///
  /// In en, this message translates to:
  /// **'Add some content first'**
  String get addSomeContentFirst;

  /// No description provided for @addTitleOptional.
  ///
  /// In en, this message translates to:
  /// **'Add a title (optional)'**
  String get addTitleOptional;

  /// No description provided for @attachmentRemoved.
  ///
  /// In en, this message translates to:
  /// **'Attachment removed'**
  String get attachmentRemoved;

  /// No description provided for @attachmentsCount.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 Attachment} other{{count} Attachments}}'**
  String attachmentsCount(int count);

  /// No description provided for @autoPublishSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Auto-publish at scheduled time'**
  String get autoPublishSubtitle;

  /// No description provided for @captionLabel.
  ///
  /// In en, this message translates to:
  /// **'Caption'**
  String get captionLabel;

  /// No description provided for @capturePhotoNow.
  ///
  /// In en, this message translates to:
  /// **'Capture a photo right now'**
  String get capturePhotoNow;

  /// No description provided for @categoryNoun.
  ///
  /// In en, this message translates to:
  /// **'category'**
  String get categoryNoun;

  /// No description provided for @categoryRequiredError.
  ///
  /// In en, this message translates to:
  /// **'Selecting a category is required'**
  String get categoryRequiredError;

  /// No description provided for @categoryRequiredLabel.
  ///
  /// In en, this message translates to:
  /// **'CATEGORY *'**
  String get categoryRequiredLabel;

  /// No description provided for @clearAllButton.
  ///
  /// In en, this message translates to:
  /// **'Clear all'**
  String get clearAllButton;

  /// No description provided for @clearButton.
  ///
  /// In en, this message translates to:
  /// **'Clear'**
  String get clearButton;

  /// No description provided for @clearEverythingBody.
  ///
  /// In en, this message translates to:
  /// **'Title, content, media, poll and the saved draft will all be cleared together. This cannot be undone.'**
  String get clearEverythingBody;

  /// No description provided for @clearEverythingTitle.
  ///
  /// In en, this message translates to:
  /// **'Clear everything?'**
  String get clearEverythingTitle;

  /// No description provided for @clearLocationLabel.
  ///
  /// In en, this message translates to:
  /// **'Clear location'**
  String get clearLocationLabel;

  /// No description provided for @clearSearchTooltip.
  ///
  /// In en, this message translates to:
  /// **'Clear search'**
  String get clearSearchTooltip;

  /// No description provided for @clearTitleTooltip.
  ///
  /// In en, this message translates to:
  /// **'Clear title'**
  String get clearTitleTooltip;

  /// No description provided for @closeLabel.
  ///
  /// In en, this message translates to:
  /// **'Close'**
  String get closeLabel;

  /// No description provided for @contentMediaPollRequired.
  ///
  /// In en, this message translates to:
  /// **'Content, media or poll is required'**
  String get contentMediaPollRequired;

  /// No description provided for @couldNotSelectDocuments.
  ///
  /// In en, this message translates to:
  /// **'Could not select documents: {error}'**
  String couldNotSelectDocuments(String error);

  /// No description provided for @couldNotSelectImages.
  ///
  /// In en, this message translates to:
  /// **'Could not select images from gallery: {error}'**
  String couldNotSelectImages(String error);

  /// No description provided for @couldNotSelectVideo.
  ///
  /// In en, this message translates to:
  /// **'Could not select video from gallery: {error}'**
  String couldNotSelectVideo(String error);

  /// No description provided for @dismissLabel.
  ///
  /// In en, this message translates to:
  /// **'DISMISS'**
  String get dismissLabel;

  /// No description provided for @draftCachedOnDevice.
  ///
  /// In en, this message translates to:
  /// **'{label} · cached on this device'**
  String draftCachedOnDevice(String label);

  /// No description provided for @draftSavedAt.
  ///
  /// In en, this message translates to:
  /// **'Draft saved {time}'**
  String draftSavedAt(String time);

  /// No description provided for @draftSavedJustNow.
  ///
  /// In en, this message translates to:
  /// **'Draft saved just now'**
  String get draftSavedJustNow;

  /// No description provided for @draftSavedMinutesAgo.
  ///
  /// In en, this message translates to:
  /// **'Draft saved {count}m ago'**
  String draftSavedMinutesAgo(int count);

  /// No description provided for @draftSavedSecondsAgo.
  ///
  /// In en, this message translates to:
  /// **'Draft saved {count}s ago'**
  String draftSavedSecondsAgo(int count);

  /// No description provided for @editImageLabel.
  ///
  /// In en, this message translates to:
  /// **'Edit image'**
  String get editImageLabel;

  /// No description provided for @editVideoLabel.
  ///
  /// In en, this message translates to:
  /// **'Edit video'**
  String get editVideoLabel;

  /// No description provided for @everythingCleared.
  ///
  /// In en, this message translates to:
  /// **'Everything cleared'**
  String get everythingCleared;

  /// No description provided for @failedToLoadCategories.
  ///
  /// In en, this message translates to:
  /// **'Failed to load categories: {error}'**
  String failedToLoadCategories(String error);

  /// No description provided for @filesLabel.
  ///
  /// In en, this message translates to:
  /// **'Files'**
  String get filesLabel;

  /// No description provided for @fillAllPollOptions.
  ///
  /// In en, this message translates to:
  /// **'Fill in all poll options'**
  String get fillAllPollOptions;

  /// No description provided for @locationLabel.
  ///
  /// In en, this message translates to:
  /// **'Location'**
  String get locationLabel;

  /// No description provided for @maxFilesAllowed.
  ///
  /// In en, this message translates to:
  /// **'Max {count} files allowed'**
  String maxFilesAllowed(int count);

  /// No description provided for @pdfPagesCount.
  ///
  /// In en, this message translates to:
  /// **'{count} pages'**
  String pdfPagesCount(int count);

  /// No description provided for @fileSizeKb.
  ///
  /// In en, this message translates to:
  /// **'{count} KB'**
  String fileSizeKb(int count);

  /// No description provided for @newPostTitle.
  ///
  /// In en, this message translates to:
  /// **'New post'**
  String get newPostTitle;

  /// No description provided for @noMatchForQuery.
  ///
  /// In en, this message translates to:
  /// **'No match for \"{query}\"'**
  String noMatchForQuery(String query);

  /// No description provided for @onlyNMoreFilesCouldBeAdded.
  ///
  /// In en, this message translates to:
  /// **'Only {count} more file(s) could be added'**
  String onlyNMoreFilesCouldBeAdded(int count);

  /// No description provided for @photosLabel.
  ///
  /// In en, this message translates to:
  /// **'Photos'**
  String get photosLabel;

  /// No description provided for @pollLabel.
  ///
  /// In en, this message translates to:
  /// **'Poll'**
  String get pollLabel;

  /// No description provided for @pollNeedsTwoOptions.
  ///
  /// In en, this message translates to:
  /// **'Poll needs at least 2 options'**
  String get pollNeedsTwoOptions;

  /// No description provided for @pollOptionNumber.
  ///
  /// In en, this message translates to:
  /// **'Option {number}'**
  String pollOptionNumber(int number);

  /// No description provided for @pollOptionRemoved.
  ///
  /// In en, this message translates to:
  /// **'Poll option removed'**
  String get pollOptionRemoved;

  /// No description provided for @pollOptionsMustDiffer.
  ///
  /// In en, this message translates to:
  /// **'Poll options cannot be the same'**
  String get pollOptionsMustDiffer;

  /// No description provided for @postDetailsLabel.
  ///
  /// In en, this message translates to:
  /// **'Post Details'**
  String get postDetailsLabel;

  /// No description provided for @postScheduledFor.
  ///
  /// In en, this message translates to:
  /// **'Post scheduled for {time}!'**
  String postScheduledFor(String time);

  /// No description provided for @quickPostBannerSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Just want to write text? Fast compose here'**
  String get quickPostBannerSubtitle;

  /// No description provided for @removeAttachmentLabel.
  ///
  /// In en, this message translates to:
  /// **'Remove attachment'**
  String get removeAttachmentLabel;

  /// No description provided for @removeOptionTooltip.
  ///
  /// In en, this message translates to:
  /// **'Remove option'**
  String get removeOptionTooltip;

  /// No description provided for @removePollTooltip.
  ///
  /// In en, this message translates to:
  /// **'Remove poll'**
  String get removePollTooltip;

  /// No description provided for @scheduleInOneHour.
  ///
  /// In en, this message translates to:
  /// **'In 1 hour'**
  String get scheduleInOneHour;

  /// No description provided for @scheduleLabel.
  ///
  /// In en, this message translates to:
  /// **'Schedule'**
  String get scheduleLabel;

  /// No description provided for @scheduleThisEvening.
  ///
  /// In en, this message translates to:
  /// **'This evening'**
  String get scheduleThisEvening;

  /// No description provided for @scheduleTomorrowEvening.
  ///
  /// In en, this message translates to:
  /// **'Tomorrow evening'**
  String get scheduleTomorrowEvening;

  /// No description provided for @scheduleTomorrowMorning.
  ///
  /// In en, this message translates to:
  /// **'Tomorrow morning'**
  String get scheduleTomorrowMorning;

  /// No description provided for @searchWithin.
  ///
  /// In en, this message translates to:
  /// **'Search {title}'**
  String searchWithin(String title);

  /// No description provided for @searchingHashtags.
  ///
  /// In en, this message translates to:
  /// **'Searching hashtags…'**
  String get searchingHashtags;

  /// No description provided for @searchingPeople.
  ///
  /// In en, this message translates to:
  /// **'Searching people…'**
  String get searchingPeople;

  /// No description provided for @selectCategoryError.
  ///
  /// In en, this message translates to:
  /// **'Select a category'**
  String get selectCategoryError;

  /// No description provided for @selectCategoryTitle.
  ///
  /// In en, this message translates to:
  /// **'Select Category'**
  String get selectCategoryTitle;

  /// No description provided for @selectFutureTime.
  ///
  /// In en, this message translates to:
  /// **'Select a future time'**
  String get selectFutureTime;

  /// No description provided for @selectSubcategoryError.
  ///
  /// In en, this message translates to:
  /// **'Select a subcategory'**
  String get selectSubcategoryError;

  /// No description provided for @selectSubcategoryTitle.
  ///
  /// In en, this message translates to:
  /// **'Select Subcategory'**
  String get selectSubcategoryTitle;

  /// No description provided for @shootQuickVideoClip.
  ///
  /// In en, this message translates to:
  /// **'Shoot a quick video clip'**
  String get shootQuickVideoClip;

  /// No description provided for @subcategoryNoun.
  ///
  /// In en, this message translates to:
  /// **'subcategory'**
  String get subcategoryNoun;

  /// No description provided for @subcategoryRequiredLabel.
  ///
  /// In en, this message translates to:
  /// **'SUBCATEGORY'**
  String get subcategoryRequiredLabel;

  /// No description provided for @takePhotoLabel.
  ///
  /// In en, this message translates to:
  /// **'Take Photo'**
  String get takePhotoLabel;

  /// No description provided for @undoLabel.
  ///
  /// In en, this message translates to:
  /// **'UNDO'**
  String get undoLabel;

  /// No description provided for @unsavedCloseWarning.
  ///
  /// In en, this message translates to:
  /// **'What you\'ve written hasn\'t been saved yet. If you close now, it will be lost.'**
  String get unsavedCloseWarning;

  /// No description provided for @useCameraLabel.
  ///
  /// In en, this message translates to:
  /// **'Use Camera'**
  String get useCameraLabel;

  /// No description provided for @videosLabel.
  ///
  /// In en, this message translates to:
  /// **'Videos'**
  String get videosLabel;

  /// No description provided for @visibilityAnyoneCanSee.
  ///
  /// In en, this message translates to:
  /// **'Anyone can see'**
  String get visibilityAnyoneCanSee;

  /// No description provided for @visibilityConnections.
  ///
  /// In en, this message translates to:
  /// **'Connections'**
  String get visibilityConnections;

  /// No description provided for @visibilityJustYou.
  ///
  /// In en, this message translates to:
  /// **'Just you'**
  String get visibilityJustYou;

  /// No description provided for @visibilityOnlyMe.
  ///
  /// In en, this message translates to:
  /// **'Only me'**
  String get visibilityOnlyMe;

  /// No description provided for @visibilityOnlyYourNetwork.
  ///
  /// In en, this message translates to:
  /// **'Only your network'**
  String get visibilityOnlyYourNetwork;

  /// No description provided for @whatsOnYourMindHashtags.
  ///
  /// In en, this message translates to:
  /// **'What\'s on your mind? Use #hashtags to boost reach'**
  String get whatsOnYourMindHashtags;

  /// No description provided for @writeSomethingAboutMedia.
  ///
  /// In en, this message translates to:
  /// **'Write something about this media...'**
  String get writeSomethingAboutMedia;

  /// No description provided for @addMediaLabel.
  ///
  /// In en, this message translates to:
  /// **'Add Media'**
  String get addMediaLabel;

  /// No description provided for @addPhotosVideosMinTwo.
  ///
  /// In en, this message translates to:
  /// **'Add photos/videos — at least 2'**
  String get addPhotosVideosMinTwo;

  /// No description provided for @addTextLabel.
  ///
  /// In en, this message translates to:
  /// **'Add Text'**
  String get addTextLabel;

  /// No description provided for @addingMusicProgress.
  ///
  /// In en, this message translates to:
  /// **'Adding music…'**
  String get addingMusicProgress;

  /// No description provided for @aspectPortrait.
  ///
  /// In en, this message translates to:
  /// **'4:5 Portrait'**
  String get aspectPortrait;

  /// No description provided for @aspectReel.
  ///
  /// In en, this message translates to:
  /// **'9:16 Reel'**
  String get aspectReel;

  /// No description provided for @aspectSquare.
  ///
  /// In en, this message translates to:
  /// **'1:1 Square'**
  String get aspectSquare;

  /// No description provided for @aspectWide.
  ///
  /// In en, this message translates to:
  /// **'16:9 Wide'**
  String get aspectWide;

  /// No description provided for @autoBeatSyncLabel.
  ///
  /// In en, this message translates to:
  /// **'Auto (Beat Sync)'**
  String get autoBeatSyncLabel;

  /// No description provided for @autoEditTitle.
  ///
  /// In en, this message translates to:
  /// **'Auto Edit'**
  String get autoEditTitle;

  /// No description provided for @autoModeDescription.
  ///
  /// In en, this message translates to:
  /// **'Auto: energy-aware cuts, transitions & Ken Burns, BPM detected.'**
  String get autoModeDescription;

  /// No description provided for @backgroundMusicPlaceholder.
  ///
  /// In en, this message translates to:
  /// **'Choose background music'**
  String get backgroundMusicPlaceholder;

  /// No description provided for @beatSyncSetupFailed.
  ///
  /// In en, this message translates to:
  /// **'Setting up the beat-synced cut failed.'**
  String get beatSyncSetupFailed;

  /// No description provided for @blendingTransitionsProgress.
  ///
  /// In en, this message translates to:
  /// **'Blending transitions…'**
  String get blendingTransitionsProgress;

  /// No description provided for @canvasColorGradeTooltip.
  ///
  /// In en, this message translates to:
  /// **'Canvas & color grade'**
  String get canvasColorGradeTooltip;

  /// No description provided for @canvasGradeSummary.
  ///
  /// In en, this message translates to:
  /// **'{aspect} · {grade} grade — tap the tune icon to change'**
  String canvasGradeSummary(String aspect, String grade);

  /// No description provided for @canvasLabel.
  ///
  /// In en, this message translates to:
  /// **'Canvas'**
  String get canvasLabel;

  /// No description provided for @captionFontNotReady.
  ///
  /// In en, this message translates to:
  /// **'Still preparing the caption font — try again in a moment, or this text will be skipped when rendering.'**
  String get captionFontNotReady;

  /// No description provided for @captionForClipHint.
  ///
  /// In en, this message translates to:
  /// **'Caption for this clip'**
  String get captionForClipHint;

  /// No description provided for @cc0OnlyNotice.
  ///
  /// In en, this message translates to:
  /// **'Only copyright-free (CC0) music is shown'**
  String get cc0OnlyNotice;

  /// No description provided for @changeMusicButton.
  ///
  /// In en, this message translates to:
  /// **'Change Music'**
  String get changeMusicButton;

  /// No description provided for @chooseMusicFromDevice.
  ///
  /// In en, this message translates to:
  /// **'Choose from device'**
  String get chooseMusicFromDevice;

  /// No description provided for @clipLengthLabel.
  ///
  /// In en, this message translates to:
  /// **'Clip length'**
  String get clipLengthLabel;

  /// No description provided for @clipNumberDuration.
  ///
  /// In en, this message translates to:
  /// **'Clip {number} · {duration}s'**
  String clipNumberDuration(int number, String duration);

  /// No description provided for @clipSetupFailed.
  ///
  /// In en, this message translates to:
  /// **'Setting up the clips failed.'**
  String get clipSetupFailed;

  /// No description provided for @clipTextDialogTitle.
  ///
  /// In en, this message translates to:
  /// **'Clip text'**
  String get clipTextDialogTitle;

  /// No description provided for @colorGradeLabel.
  ///
  /// In en, this message translates to:
  /// **'Color grade'**
  String get colorGradeLabel;

  /// No description provided for @couldntAddMusicTrack.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t add the music track to the montage.'**
  String get couldntAddMusicTrack;

  /// No description provided for @couldntAddTrack.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t add that track — try again.'**
  String get couldntAddTrack;

  /// No description provided for @couldntBlendTransitions.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t blend the transitions between clips.'**
  String get couldntBlendTransitions;

  /// No description provided for @couldntDownloadTrack.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t download that track (server said {code}). Try again.'**
  String couldntDownloadTrack(int code);

  /// No description provided for @couldntProcessClip.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t process clip {number} — it may be corrupted or an unsupported format. Try removing or replacing it.'**
  String couldntProcessClip(int number);

  /// No description provided for @couldntTrimTrack.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t trim that track — try a different clip length.'**
  String get couldntTrimTrack;

  /// No description provided for @doneLabel.
  ///
  /// In en, this message translates to:
  /// **'Done'**
  String get doneLabel;

  /// No description provided for @emptySearchPrompt.
  ///
  /// In en, this message translates to:
  /// **'Search for something...'**
  String get emptySearchPrompt;

  /// No description provided for @exportButton.
  ///
  /// In en, this message translates to:
  /// **'Export'**
  String get exportButton;

  /// No description provided for @gradeBw.
  ///
  /// In en, this message translates to:
  /// **'B&W'**
  String get gradeBw;

  /// No description provided for @gradeMoody.
  ///
  /// In en, this message translates to:
  /// **'Moody'**
  String get gradeMoody;

  /// No description provided for @gradeNone.
  ///
  /// In en, this message translates to:
  /// **'None'**
  String get gradeNone;

  /// No description provided for @gradeVibrant.
  ///
  /// In en, this message translates to:
  /// **'Vibrant'**
  String get gradeVibrant;

  /// No description provided for @gradeVintage.
  ///
  /// In en, this message translates to:
  /// **'Vintage'**
  String get gradeVintage;

  /// No description provided for @gradeWarm.
  ///
  /// In en, this message translates to:
  /// **'Warm'**
  String get gradeWarm;

  /// No description provided for @hitRegenerateHint.
  ///
  /// In en, this message translates to:
  /// **'Hit Regenerate to re-render with the new canvas/grade.'**
  String get hitRegenerateHint;

  /// No description provided for @inPointSeconds.
  ///
  /// In en, this message translates to:
  /// **'In-point: {seconds}s'**
  String inPointSeconds(String seconds);

  /// No description provided for @kenBurnsLabel.
  ///
  /// In en, this message translates to:
  /// **'Ken Burns'**
  String get kenBurnsLabel;

  /// No description provided for @lastClipLabel.
  ///
  /// In en, this message translates to:
  /// **'Last clip'**
  String get lastClipLabel;

  /// No description provided for @manualModeDescription.
  ///
  /// In en, this message translates to:
  /// **'Manual: clips evenly split, you set everything yourself.'**
  String get manualModeDescription;

  /// No description provided for @manualSetupLabel.
  ///
  /// In en, this message translates to:
  /// **'Manual Setup'**
  String get manualSetupLabel;

  /// No description provided for @nextMusicButton.
  ///
  /// In en, this message translates to:
  /// **'Next: Music'**
  String get nextMusicButton;

  /// No description provided for @noInternetConnectionRetry.
  ///
  /// In en, this message translates to:
  /// **'No internet connection — check your connection and try again.'**
  String get noInternetConnectionRetry;

  /// No description provided for @placingCutsProgress.
  ///
  /// In en, this message translates to:
  /// **'Placing cuts on the beat…'**
  String get placingCutsProgress;

  /// No description provided for @preparingClipOfTotal.
  ///
  /// In en, this message translates to:
  /// **'Preparing clip {current} of {total}…'**
  String preparingClipOfTotal(int current, int total);

  /// No description provided for @preparingOfTotal.
  ///
  /// In en, this message translates to:
  /// **'Preparing {current} of {total}…'**
  String preparingOfTotal(int current, int total);

  /// No description provided for @readingMusicProgress.
  ///
  /// In en, this message translates to:
  /// **'Reading music…'**
  String get readingMusicProgress;

  /// No description provided for @regenerateButton.
  ///
  /// In en, this message translates to:
  /// **'Regenerate'**
  String get regenerateButton;

  /// No description provided for @renderingMontageFailed.
  ///
  /// In en, this message translates to:
  /// **'Rendering the montage failed.'**
  String get renderingMontageFailed;

  /// No description provided for @retryButton.
  ///
  /// In en, this message translates to:
  /// **'Retry'**
  String get retryButton;

  /// No description provided for @scoringClipProgress.
  ///
  /// In en, this message translates to:
  /// **'Scoring clip {current} of {total}…'**
  String scoringClipProgress(int current, int total);

  /// No description provided for @searchDidntGoThrough.
  ///
  /// In en, this message translates to:
  /// **'Search didn\'t go through — try again.'**
  String get searchDidntGoThrough;

  /// No description provided for @searchFreesoundHint.
  ///
  /// In en, this message translates to:
  /// **'Search Freesound (e.g. lofi, guitar)'**
  String get searchFreesoundHint;

  /// No description provided for @settingUpClipsProgress.
  ///
  /// In en, this message translates to:
  /// **'Setting up clips…'**
  String get settingUpClipsProgress;

  /// No description provided for @someMediaNotOptimized.
  ///
  /// In en, this message translates to:
  /// **'Some media couldn\'t be optimized, but was added as-is.'**
  String get someMediaNotOptimized;

  /// No description provided for @sourceLengthUnknown.
  ///
  /// In en, this message translates to:
  /// **'Source length unknown — in-point disabled'**
  String get sourceLengthUnknown;

  /// No description provided for @speedLabel.
  ///
  /// In en, this message translates to:
  /// **'Speed: {value}x'**
  String speedLabel(String value);

  /// No description provided for @startPointLabel.
  ///
  /// In en, this message translates to:
  /// **'Start point'**
  String get startPointLabel;

  /// No description provided for @textAddedCheckLabel.
  ///
  /// In en, this message translates to:
  /// **'Text ✓'**
  String get textAddedCheckLabel;

  /// No description provided for @transCircle.
  ///
  /// In en, this message translates to:
  /// **'Circle'**
  String get transCircle;

  /// No description provided for @transDissolve.
  ///
  /// In en, this message translates to:
  /// **'Dissolve'**
  String get transDissolve;

  /// No description provided for @transFlash.
  ///
  /// In en, this message translates to:
  /// **'Flash'**
  String get transFlash;

  /// No description provided for @transGlitch.
  ///
  /// In en, this message translates to:
  /// **'Glitch'**
  String get transGlitch;

  /// No description provided for @transRadial.
  ///
  /// In en, this message translates to:
  /// **'Radial'**
  String get transRadial;

  /// No description provided for @transRgbSplit.
  ///
  /// In en, this message translates to:
  /// **'RGB Split'**
  String get transRgbSplit;

  /// No description provided for @transShake.
  ///
  /// In en, this message translates to:
  /// **'Shake'**
  String get transShake;

  /// No description provided for @transSlide.
  ///
  /// In en, this message translates to:
  /// **'Slide'**
  String get transSlide;

  /// No description provided for @transSmoothSlide.
  ///
  /// In en, this message translates to:
  /// **'Smooth Slide'**
  String get transSmoothSlide;

  /// No description provided for @transSqueeze.
  ///
  /// In en, this message translates to:
  /// **'Squeeze'**
  String get transSqueeze;

  /// No description provided for @transWipe.
  ///
  /// In en, this message translates to:
  /// **'Wipe'**
  String get transWipe;

  /// No description provided for @transZoom.
  ///
  /// In en, this message translates to:
  /// **'Zoom'**
  String get transZoom;

  /// No description provided for @trimSpeedLabel.
  ///
  /// In en, this message translates to:
  /// **'Trim & Speed'**
  String get trimSpeedLabel;

  /// No description provided for @useThisSoundButton.
  ///
  /// In en, this message translates to:
  /// **'Use this sound'**
  String get useThisSoundButton;

  /// No description provided for @addClipLabel.
  ///
  /// In en, this message translates to:
  /// **'Add Clip'**
  String get addClipLabel;

  /// No description provided for @addMorePhotosVideosHint.
  ///
  /// In en, this message translates to:
  /// **'Add more photos/videos — they\'ll all be joined into one video'**
  String get addMorePhotosVideosHint;

  /// No description provided for @addPhotoLabel.
  ///
  /// In en, this message translates to:
  /// **'Add Photo'**
  String get addPhotoLabel;

  /// No description provided for @addPhotosTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Add Photos'**
  String get addPhotosTabLabel;

  /// No description provided for @addStickerTitle.
  ///
  /// In en, this message translates to:
  /// **'Add Sticker'**
  String get addStickerTitle;

  /// No description provided for @adjustTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Adjust'**
  String get adjustTabLabel;

  /// No description provided for @analyzingLabel.
  ///
  /// In en, this message translates to:
  /// **'Analyzing…'**
  String get analyzingLabel;

  /// No description provided for @applyLabel.
  ///
  /// In en, this message translates to:
  /// **'Apply'**
  String get applyLabel;

  /// No description provided for @autoEnhanceFailed.
  ///
  /// In en, this message translates to:
  /// **'Auto-enhance failed: {error}'**
  String autoEnhanceFailed(String error);

  /// No description provided for @autoEnhanceLabel.
  ///
  /// In en, this message translates to:
  /// **'Auto-Enhance'**
  String get autoEnhanceLabel;

  /// No description provided for @autoPlacedOnFaceHint.
  ///
  /// In en, this message translates to:
  /// **'Auto-placed on the detected face — drag/pinch/rotate after.'**
  String get autoPlacedOnFaceHint;

  /// No description provided for @batteryLabel.
  ///
  /// In en, this message translates to:
  /// **'Battery'**
  String get batteryLabel;

  /// No description provided for @boomerangBakeFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t create the Boomerang effect.'**
  String get boomerangBakeFailed;

  /// No description provided for @boomerangOnHint.
  ///
  /// In en, this message translates to:
  /// **'Boomerang ON — forward + reverse loop'**
  String get boomerangOnHint;

  /// No description provided for @boomerangTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Boomerang'**
  String get boomerangTabLabel;

  /// No description provided for @brightnessLabel.
  ///
  /// In en, this message translates to:
  /// **'Brightness'**
  String get brightnessLabel;

  /// No description provided for @brushLabel.
  ///
  /// In en, this message translates to:
  /// **'Brush'**
  String get brushLabel;

  /// No description provided for @chooseCoverFrame.
  ///
  /// In en, this message translates to:
  /// **'Choose cover frame'**
  String get chooseCoverFrame;

  /// No description provided for @clearDrawingTooltip.
  ///
  /// In en, this message translates to:
  /// **'Clear drawing'**
  String get clearDrawingTooltip;

  /// No description provided for @clip1ThisMediaLabel.
  ///
  /// In en, this message translates to:
  /// **'Clip 1 (this photo/video)'**
  String get clip1ThisMediaLabel;

  /// No description provided for @clip2TransitionLabel.
  ///
  /// In en, this message translates to:
  /// **'Transition into clip 2'**
  String get clip2TransitionLabel;

  /// No description provided for @clipNormalizeFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t process clip {number}.'**
  String clipNormalizeFailed(int number);

  /// No description provided for @clipPinchZoomPan.
  ///
  /// In en, this message translates to:
  /// **'Clip {number} — pinch to zoom, drag to pan'**
  String clipPinchZoomPan(int number);

  /// No description provided for @clipPreparingOfTotal.
  ///
  /// In en, this message translates to:
  /// **'Preparing clip {current}/{total}…'**
  String clipPreparingOfTotal(int current, int total);

  /// No description provided for @clipSelectFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t select that clip: {error}'**
  String clipSelectFailed(String error);

  /// No description provided for @clipsJoinFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t join the clips together.'**
  String get clipsJoinFailed;

  /// No description provided for @clipsTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Clips'**
  String get clipsTabLabel;

  /// No description provided for @clipsWillMakeVideoWith.
  ///
  /// In en, this message translates to:
  /// **'{count} clips will make up the video (with this photo/video)'**
  String clipsWillMakeVideoWith(int count);

  /// No description provided for @color2GradientHint.
  ///
  /// In en, this message translates to:
  /// **'Color 2 (gradient) — tap a swatch above'**
  String get color2GradientHint;

  /// No description provided for @contrastLabel.
  ///
  /// In en, this message translates to:
  /// **'Contrast'**
  String get contrastLabel;

  /// No description provided for @coverPreviewUnavailable.
  ///
  /// In en, this message translates to:
  /// **'Cover preview isn\'t available'**
  String get coverPreviewUnavailable;

  /// No description provided for @coverTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Cover'**
  String get coverTabLabel;

  /// No description provided for @cropFailed.
  ///
  /// In en, this message translates to:
  /// **'Crop failed.'**
  String get cropFailed;

  /// No description provided for @dateLabel.
  ///
  /// In en, this message translates to:
  /// **'Date'**
  String get dateLabel;

  /// No description provided for @deleteLabel.
  ///
  /// In en, this message translates to:
  /// **'Delete'**
  String get deleteLabel;

  /// No description provided for @downloadFailedCode.
  ///
  /// In en, this message translates to:
  /// **'Download failed ({code})'**
  String downloadFailedCode(int code);

  /// No description provided for @drawTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Draw'**
  String get drawTabLabel;

  /// No description provided for @editLabel.
  ///
  /// In en, this message translates to:
  /// **'Edit'**
  String get editLabel;

  /// No description provided for @editSaveFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t save the edit: {error}'**
  String editSaveFailed(String error);

  /// No description provided for @eraserApplyFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t apply the eraser: {error}'**
  String eraserApplyFailed(String error);

  /// No description provided for @eraserHint.
  ///
  /// In en, this message translates to:
  /// **'Paint over a blemish/watermark, then tap Apply. Best for small spots on plain backgrounds.'**
  String get eraserHint;

  /// No description provided for @eraserTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Eraser'**
  String get eraserTabLabel;

  /// No description provided for @erasingLabel.
  ///
  /// In en, this message translates to:
  /// **'Erasing…'**
  String get erasingLabel;

  /// No description provided for @exportWithMusicFailedTryWithout.
  ///
  /// In en, this message translates to:
  /// **'Export with music failed — try again without music'**
  String get exportWithMusicFailedTryWithout;

  /// No description provided for @extraClipsWillJoinEnd.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 extra clip will be added at the end} other{{count} extra clips will be added at the end}}'**
  String extraClipsWillJoinEnd(int count);

  /// No description provided for @faceFiltersTitle.
  ///
  /// In en, this message translates to:
  /// **'Face Filters'**
  String get faceFiltersTitle;

  /// No description provided for @filtersTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Filters'**
  String get filtersTabLabel;

  /// No description provided for @flipHorizontalTooltip.
  ///
  /// In en, this message translates to:
  /// **'Flip horizontal'**
  String get flipHorizontalTooltip;

  /// No description provided for @flipVerticalTooltip.
  ///
  /// In en, this message translates to:
  /// **'Flip vertical'**
  String get flipVerticalTooltip;

  /// No description provided for @fontLabel.
  ///
  /// In en, this message translates to:
  /// **'Font'**
  String get fontLabel;

  /// No description provided for @fxBakeFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t apply the effect.'**
  String get fxBakeFailed;

  /// No description provided for @fxTabLabel.
  ///
  /// In en, this message translates to:
  /// **'FX'**
  String get fxTabLabel;

  /// No description provided for @keepOriginalAudioToo.
  ///
  /// In en, this message translates to:
  /// **'Also keep the video\'s original audio'**
  String get keepOriginalAudioToo;

  /// No description provided for @lengthStaysAsTrimSet.
  ///
  /// In en, this message translates to:
  /// **'Its length stays whatever is set in the Trim tab'**
  String get lengthStaysAsTrimSet;

  /// No description provided for @liveStickersTitle.
  ///
  /// In en, this message translates to:
  /// **'Live Stickers'**
  String get liveStickersTitle;

  /// No description provided for @loadingEllipsis.
  ///
  /// In en, this message translates to:
  /// **'Loading...'**
  String get loadingEllipsis;

  /// No description provided for @mediaAddFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t add that photo/video: {error}'**
  String mediaAddFailed(String error);

  /// No description provided for @montageExportFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t export the montage: {error}'**
  String montageExportFailed(String error);

  /// No description provided for @musicAddFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t add the music: {error}'**
  String musicAddFailed(String error);

  /// No description provided for @musicAttachFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t attach the music.'**
  String get musicAttachFailed;

  /// No description provided for @musicMixFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t mix in the music.'**
  String get musicMixFailed;

  /// No description provided for @musicSelectFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t select that music: {error}'**
  String musicSelectFailed(String error);

  /// No description provided for @musicTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Music'**
  String get musicTabLabel;

  /// No description provided for @musicTrackLabel.
  ///
  /// In en, this message translates to:
  /// **'Music track'**
  String get musicTrackLabel;

  /// No description provided for @myStickersTitle.
  ///
  /// In en, this message translates to:
  /// **'My Stickers'**
  String get myStickersTitle;

  /// No description provided for @nextClipTransitionLabel.
  ///
  /// In en, this message translates to:
  /// **'Transition into the next clip'**
  String get nextClipTransitionLabel;

  /// No description provided for @noExtraClipsYet.
  ///
  /// In en, this message translates to:
  /// **'No extra clips yet — add some to join one after another'**
  String get noExtraClipsYet;

  /// No description provided for @noFaceDetectedNote.
  ///
  /// In en, this message translates to:
  /// **'(no face detected — placed centered)'**
  String get noFaceDetectedNote;

  /// No description provided for @orLabel.
  ///
  /// In en, this message translates to:
  /// **'or'**
  String get orLabel;

  /// No description provided for @photoSelectFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t select that photo: {error}'**
  String photoSelectFailed(String error);

  /// No description provided for @primaryClipFxFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t apply effects to the main clip.'**
  String get primaryClipFxFailed;

  /// No description provided for @primaryClipTrimFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t trim the main clip.'**
  String get primaryClipTrimFailed;

  /// No description provided for @redoTooltip.
  ///
  /// In en, this message translates to:
  /// **'Redo'**
  String get redoTooltip;

  /// No description provided for @removeBackToEditing.
  ///
  /// In en, this message translates to:
  /// **'Remove — back to editing'**
  String get removeBackToEditing;

  /// No description provided for @removeMusicTooltip.
  ///
  /// In en, this message translates to:
  /// **'Remove music'**
  String get removeMusicTooltip;

  /// No description provided for @resetTooltip.
  ///
  /// In en, this message translates to:
  /// **'Reset'**
  String get resetTooltip;

  /// No description provided for @resetZoomLabel.
  ///
  /// In en, this message translates to:
  /// **'Reset zoom'**
  String get resetZoomLabel;

  /// No description provided for @rotateLabel.
  ///
  /// In en, this message translates to:
  /// **'Rotate'**
  String get rotateLabel;

  /// No description provided for @rotateRightLabel.
  ///
  /// In en, this message translates to:
  /// **'Rotate right'**
  String get rotateRightLabel;

  /// No description provided for @saturationLabel.
  ///
  /// In en, this message translates to:
  /// **'Saturation'**
  String get saturationLabel;

  /// No description provided for @searchFailedRetry.
  ///
  /// In en, this message translates to:
  /// **'Search failed — try again'**
  String get searchFailedRetry;

  /// No description provided for @speedAdjustFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t adjust the speed.'**
  String get speedAdjustFailed;

  /// No description provided for @speedTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Speed'**
  String get speedTabLabel;

  /// No description provided for @startFromLabel.
  ///
  /// In en, this message translates to:
  /// **'Start from'**
  String get startFromLabel;

  /// No description provided for @stickersTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Stickers'**
  String get stickersTabLabel;

  /// No description provided for @styleLabel.
  ///
  /// In en, this message translates to:
  /// **'Style'**
  String get styleLabel;

  /// No description provided for @tapAddStickerHint.
  ///
  /// In en, this message translates to:
  /// **'Tap Add Sticker, then drag/pinch/rotate it on the photo'**
  String get tapAddStickerHint;

  /// No description provided for @tapAddTextHint.
  ///
  /// In en, this message translates to:
  /// **'Tap Add Text, then drag/pinch/rotate it on the photo'**
  String get tapAddTextHint;

  /// No description provided for @tapToEnableBoomerang.
  ///
  /// In en, this message translates to:
  /// **'Tap to enable Boomerang'**
  String get tapToEnableBoomerang;

  /// No description provided for @textTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Text'**
  String get textTabLabel;

  /// No description provided for @timeLabel.
  ///
  /// In en, this message translates to:
  /// **'Time'**
  String get timeLabel;

  /// No description provided for @transformFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t apply that: {error}'**
  String transformFailed(String error);

  /// No description provided for @transitionChainFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t chain the transitions together.'**
  String get transitionChainFailed;

  /// No description provided for @transitionsBlendingProgress.
  ///
  /// In en, this message translates to:
  /// **'Blending transitions…'**
  String get transitionsBlendingProgress;

  /// No description provided for @trimTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Trim'**
  String get trimTabLabel;

  /// No description provided for @undoTooltip.
  ///
  /// In en, this message translates to:
  /// **'Undo'**
  String get undoTooltip;

  /// No description provided for @useLabel.
  ///
  /// In en, this message translates to:
  /// **'Use'**
  String get useLabel;

  /// No description provided for @useThisLabel.
  ///
  /// In en, this message translates to:
  /// **'Use This'**
  String get useThisLabel;

  /// No description provided for @videoControllerNotReady.
  ///
  /// In en, this message translates to:
  /// **'The video isn\'t ready yet.'**
  String get videoControllerNotReady;

  /// No description provided for @videoLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load the video: {error}'**
  String videoLoadFailed(String error);

  /// No description provided for @videoTrimExportFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t export the trimmed video.'**
  String get videoTrimExportFailed;

  /// No description provided for @videoTrimSaveFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t save the trimmed video: {error}'**
  String videoTrimSaveFailed(String error);

  /// App bar title of the focus session history screen
  ///
  /// In en, this message translates to:
  /// **'Focus Mode — History'**
  String get focusHistoryTitle;

  /// Error shown when focus session history fails to load
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load history.'**
  String get focusHistoryLoadFailed;

  /// Empty state of the focus session history list
  ///
  /// In en, this message translates to:
  /// **'No focus sessions yet.'**
  String get focusHistoryEmpty;

  /// Focus session length under an hour
  ///
  /// In en, this message translates to:
  /// **'{minutes} min'**
  String focusHistoryMinutes(int minutes);

  /// Focus session length in whole hours
  ///
  /// In en, this message translates to:
  /// **'{hours}h'**
  String focusHistoryHours(int hours);

  /// Focus session length in hours and minutes
  ///
  /// In en, this message translates to:
  /// **'{hours}h {minutes}m'**
  String focusHistoryHoursMinutes(int hours, int minutes);

  /// Focus session rule: nobody can message you
  ///
  /// In en, this message translates to:
  /// **'Full silence'**
  String get focusRuleNobody;

  /// Focus session rule: only teachers can message you
  ///
  /// In en, this message translates to:
  /// **'Teachers only'**
  String get focusRuleTeachersOnly;

  /// Subtitle of a focus history row: length and rule
  ///
  /// In en, this message translates to:
  /// **'{duration} · {rule}'**
  String focusHistoryMeta(String duration, String rule);

  /// Subtitle of a focus history row for a session that was ended early
  ///
  /// In en, this message translates to:
  /// **'{duration} · {rule} · ended early'**
  String focusHistoryMetaEndedEarly(String duration, String rule);

  /// App bar title of the parent access management screen
  ///
  /// In en, this message translates to:
  /// **'Parent/Guardian Access'**
  String get parentAccessTitle;

  /// Extended FAB label that creates a parent access code
  ///
  /// In en, this message translates to:
  /// **'New Code'**
  String get parentAccessNewCode;

  /// Explanation banner at the top of the parent access screen
  ///
  /// In en, this message translates to:
  /// **'Give the code to your parent/guardian from here — they will only see attendance and assignment status, never chat messages. Tap a code to manage its individual devices.'**
  String get parentAccessIntro;

  /// Empty state when the student has no parent codes
  ///
  /// In en, this message translates to:
  /// **'No active codes yet.'**
  String get parentAccessEmpty;

  /// Error when the list of parent codes fails to load
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load codes.'**
  String get parentAccessLoadFailed;

  /// Dialog title asking for a label for a new parent code
  ///
  /// In en, this message translates to:
  /// **'Who is this code for?'**
  String get parentAccessLabelDialogTitle;

  /// Hint in the parent code label field
  ///
  /// In en, this message translates to:
  /// **'e.g. Mom, Dad'**
  String get parentAccessLabelHint;

  /// Button that generates a parent code
  ///
  /// In en, this message translates to:
  /// **'Generate'**
  String get parentAccessGenerate;

  /// Snackbar when parent code generation fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t generate the code. Please try again.'**
  String get parentAccessGenerateFailed;

  /// Title of the dialog showing a freshly generated parent code
  ///
  /// In en, this message translates to:
  /// **'Share this code with your parent'**
  String get parentAccessShareCodeTitle;

  /// Snackbar after copying a parent code
  ///
  /// In en, this message translates to:
  /// **'Copied'**
  String get parentAccessCodeCopied;

  /// Title of the revoke-code confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'Revoke access?'**
  String get parentAccessRevokeTitle;

  /// Revoke confirmation when a code has several devices
  ///
  /// In en, this message translates to:
  /// **'All {count} devices linked to \"{name}\" will lose access immediately.'**
  String parentAccessRevokeBodyMany(int count, String name);

  /// Revoke confirmation when a code has at most one device
  ///
  /// In en, this message translates to:
  /// **'Access for \"{name}\" will be revoked immediately.'**
  String parentAccessRevokeBodyOne(String name);

  /// Confirm button for revoking access
  ///
  /// In en, this message translates to:
  /// **'Revoke'**
  String get parentAccessRevoke;

  /// Snackbar when revoking a code fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t revoke access. Please try again.'**
  String get parentAccessRevokeFailed;

  /// Snackbar when the reveal-code endpoint is throttled
  ///
  /// In en, this message translates to:
  /// **'Revealed too many times — try again in a while.'**
  String get parentAccessRevealRateLimited;

  /// Snackbar when revealing a code fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t reveal the code.'**
  String get parentAccessRevealFailed;

  /// Button that extends an expired parent code
  ///
  /// In en, this message translates to:
  /// **'Renew'**
  String get parentAccessRenew;

  /// Snackbar after a parent code is renewed
  ///
  /// In en, this message translates to:
  /// **'Access renewed.'**
  String get parentAccessRenewed;

  /// Snackbar when renewing a code fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t renew. Please try again.'**
  String get parentAccessRenewFailed;

  /// Fallback name for a parent code without a label
  ///
  /// In en, this message translates to:
  /// **'Unnamed'**
  String get parentAccessUnnamed;

  /// Device count under a code in the list
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 device · tap to manage} other{{count} devices · tap to manage}}'**
  String parentAccessDevicesTap(int count);

  /// Tooltip of the delete button on a code row
  ///
  /// In en, this message translates to:
  /// **'Revoke entire code (all devices)'**
  String get parentAccessRevokeAllTooltip;

  /// Expiry label for a code that expired today
  ///
  /// In en, this message translates to:
  /// **'Expired today'**
  String get parentAccessExpiredToday;

  /// Expiry label for a code that expired earlier
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{Expired 1 day ago} other{Expired {count} days ago}}'**
  String parentAccessExpiredDaysAgo(int count);

  /// Expiry label for a code that expires later today
  ///
  /// In en, this message translates to:
  /// **'Expires today'**
  String get parentAccessExpiresToday;

  /// Expiry label for a code expiring within 14 days
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{Expires in 1 day} other{Expires in {count} days}}'**
  String parentAccessExpiresInDays(int count);

  /// Expiry label with a formatted date
  ///
  /// In en, this message translates to:
  /// **'Expires {date}'**
  String parentAccessExpiresOn(String date);

  /// Relative-time fallback when a device was never active
  ///
  /// In en, this message translates to:
  /// **'Never used'**
  String get parentAccessNeverUsed;

  /// Devices sheet title when the code has no label
  ///
  /// In en, this message translates to:
  /// **'Devices'**
  String get parentAccessDevicesTitle;

  /// Devices sheet title for a labelled code
  ///
  /// In en, this message translates to:
  /// **'{name} — devices'**
  String parentAccessDevicesTitleNamed(String name);

  /// Helper text in the devices sheet
  ///
  /// In en, this message translates to:
  /// **'Revoking a single device keeps access for the other devices.'**
  String get parentAccessDevicesHint;

  /// Error when the device list fails to load
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load devices.'**
  String get parentAccessDevicesLoadFailed;

  /// Empty state of the devices sheet
  ///
  /// In en, this message translates to:
  /// **'No device has been verified with this code yet.'**
  String get parentAccessNoDevices;

  /// Device row: last activity time
  ///
  /// In en, this message translates to:
  /// **'Last active: {time}'**
  String parentAccessLastActive(String time);

  /// Device row: verification time
  ///
  /// In en, this message translates to:
  /// **'Verified: {time}'**
  String parentAccessVerifiedAt(String time);

  /// Tooltip of the per-device revoke button
  ///
  /// In en, this message translates to:
  /// **'Revoke only this device'**
  String get parentAccessRevokeDeviceTooltip;

  /// Title of the single-device revoke dialog
  ///
  /// In en, this message translates to:
  /// **'Revoke this device?'**
  String get parentAccessRevokeDeviceTitle;

  /// Body of the single-device revoke dialog
  ///
  /// In en, this message translates to:
  /// **'Only this one device will be disconnected; the others stay connected.'**
  String get parentAccessRevokeDeviceBody;

  /// Snackbar when revoking a single device fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t revoke the device.'**
  String get parentAccessRevokeDeviceFailed;

  /// Sender label for your own messages in a chat
  ///
  /// In en, this message translates to:
  /// **'You'**
  String get chatYou;

  /// Fallback name for an unknown chat participant
  ///
  /// In en, this message translates to:
  /// **'Unknown'**
  String get chatUnknown;

  /// Fallback name when the sender is not known
  ///
  /// In en, this message translates to:
  /// **'Someone'**
  String get chatSomeone;

  /// Fallback error text from the chat socket
  ///
  /// In en, this message translates to:
  /// **'Error'**
  String get chatGenericError;

  /// Snackbar when jumping to a message fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t scroll to that message (it may be too old)'**
  String get chatScrollToMessageFailed;

  /// Error loading the chat history
  ///
  /// In en, this message translates to:
  /// **'Failed to load messages: {error}'**
  String chatLoadMessagesFailed(String error);

  /// Error loading older messages when scrolling up
  ///
  /// In en, this message translates to:
  /// **'Failed to load older messages: {error}'**
  String chatLoadOlderFailed(String error);

  /// Generic load error in chat sheets
  ///
  /// In en, this message translates to:
  /// **'Failed to load: {error}'**
  String chatLoadFailed(String error);

  /// Pinned message banner under the app bar
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{Pinned message} other{{count} pinned messages}}'**
  String chatPinnedBanner(int count);

  /// Preview text for a message that only has an attachment
  ///
  /// In en, this message translates to:
  /// **'📎 Attachment'**
  String get chatAttachment;

  /// Title of the pinned messages sheet
  ///
  /// In en, this message translates to:
  /// **'Pinned messages'**
  String get chatPinnedMessagesTitle;

  /// Empty state of the pinned messages sheet
  ///
  /// In en, this message translates to:
  /// **'No pinned messages'**
  String get chatNoPinned;

  /// Who pinned a message
  ///
  /// In en, this message translates to:
  /// **'Pinned by {name}'**
  String chatPinnedBy(String name);

  /// Message action: pin
  ///
  /// In en, this message translates to:
  /// **'Pin'**
  String get chatPin;

  /// Message action: unpin
  ///
  /// In en, this message translates to:
  /// **'Unpin'**
  String get chatUnpin;

  /// Error setting a chat wallpaper
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t set wallpaper: {error}'**
  String chatWallpaperSetFailed(String error);

  /// Title of the remove-wallpaper dialog
  ///
  /// In en, this message translates to:
  /// **'Remove wallpaper?'**
  String get chatWallpaperRemoveTitle;

  /// Body of the remove-wallpaper dialog
  ///
  /// In en, this message translates to:
  /// **'This chat will go back to the default background.'**
  String get chatWallpaperRemoveBody;

  /// Confirm button in the remove-wallpaper dialog
  ///
  /// In en, this message translates to:
  /// **'Remove'**
  String get chatRemove;

  /// Error removing a chat wallpaper
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t remove wallpaper: {error}'**
  String chatWallpaperRemoveFailed(String error);

  /// Wallpaper sheet option when a wallpaper is already set
  ///
  /// In en, this message translates to:
  /// **'Change wallpaper'**
  String get chatChangeWallpaper;

  /// Wallpaper sheet option when no wallpaper is set
  ///
  /// In en, this message translates to:
  /// **'Set wallpaper'**
  String get chatSetWallpaper;

  /// Wallpaper sheet option that removes the wallpaper
  ///
  /// In en, this message translates to:
  /// **'Remove wallpaper'**
  String get chatRemoveWallpaper;

  /// 3-dot menu item that opens the wallpaper options
  ///
  /// In en, this message translates to:
  /// **'Chat wallpaper'**
  String get chatWallpaperMenu;

  /// Progress text while a wallpaper is being applied
  ///
  /// In en, this message translates to:
  /// **'Setting wallpaper…'**
  String get chatSettingWallpaper;

  /// Chat filter label: text messages
  ///
  /// In en, this message translates to:
  /// **'Text'**
  String get chatFilterText;

  /// Chat filter label: media
  ///
  /// In en, this message translates to:
  /// **'Media'**
  String get chatFilterMedia;

  /// Chat filter label: documents
  ///
  /// In en, this message translates to:
  /// **'Docs'**
  String get chatFilterDocs;

  /// Chat filter label: links
  ///
  /// In en, this message translates to:
  /// **'Links'**
  String get chatFilterLinks;

  /// Filter sheet title and 3-dot menu item
  ///
  /// In en, this message translates to:
  /// **'Filter messages'**
  String get chatFilterTitle;

  /// Filter sheet option: everything
  ///
  /// In en, this message translates to:
  /// **'All messages'**
  String get chatFilterAllMessages;

  /// Filter sheet option: images and videos
  ///
  /// In en, this message translates to:
  /// **'Image / Video'**
  String get chatFilterImageVideo;

  /// Filter sheet option: documents and files
  ///
  /// In en, this message translates to:
  /// **'Docs / Files'**
  String get chatFilterDocsFiles;

  /// Filter sheet option: links
  ///
  /// In en, this message translates to:
  /// **'URL / Links'**
  String get chatFilterUrlLinks;

  /// 3-dot menu item while a filter is active
  ///
  /// In en, this message translates to:
  /// **'Filter: {label}'**
  String chatFilterActive(String label);

  /// Banner above the filtered message list
  ///
  /// In en, this message translates to:
  /// **'{label} • {count, plural, =1{1 message} other{{count} messages}}'**
  String chatFilterResultCount(String label, int count);

  /// Empty state when a filter has no results
  ///
  /// In en, this message translates to:
  /// **'No {label} messages in this chat'**
  String chatNoFilteredMessages(String label);

  /// Snackbar after muting
  ///
  /// In en, this message translates to:
  /// **'Notifications muted'**
  String get chatNotificationsMuted;

  /// Snackbar after unmuting
  ///
  /// In en, this message translates to:
  /// **'Notifications unmuted'**
  String get chatNotificationsUnmuted;

  /// Generic update error in chat settings
  ///
  /// In en, this message translates to:
  /// **'Failed to update: {error}'**
  String chatUpdateFailed(String error);

  /// 3-dot menu item (group chats)
  ///
  /// In en, this message translates to:
  /// **'Mute group'**
  String get chatMuteGroup;

  /// 3-dot menu item (group chats)
  ///
  /// In en, this message translates to:
  /// **'Unmute group'**
  String get chatUnmuteGroup;

  /// 3-dot menu item (1:1 chats)
  ///
  /// In en, this message translates to:
  /// **'Mute notifications'**
  String get chatMuteNotifications;

  /// 3-dot menu item (1:1 chats)
  ///
  /// In en, this message translates to:
  /// **'Unmute notifications'**
  String get chatUnmuteNotifications;

  /// Fallback name in the block dialog
  ///
  /// In en, this message translates to:
  /// **'this user'**
  String get chatThisUser;

  /// Title of the block confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'Block user?'**
  String get chatBlockTitle;

  /// Body of the block confirmation dialog
  ///
  /// In en, this message translates to:
  /// **'{name} won\'t be able to call or message you, and you won\'t see their messages either.'**
  String chatBlockBody(String name);

  /// Confirm button of the block dialog
  ///
  /// In en, this message translates to:
  /// **'Block'**
  String get chatBlock;

  /// Snackbar after blocking
  ///
  /// In en, this message translates to:
  /// **'User blocked.'**
  String get chatUserBlocked;

  /// Error when blocking fails
  ///
  /// In en, this message translates to:
  /// **'Block failed: {error}'**
  String chatBlockFailed(String error);

  /// Snackbar after unblocking
  ///
  /// In en, this message translates to:
  /// **'User unblocked.'**
  String get chatUserUnblocked;

  /// Error when unblocking fails
  ///
  /// In en, this message translates to:
  /// **'Unblock failed: {error}'**
  String chatUnblockFailed(String error);

  /// 3-dot menu item
  ///
  /// In en, this message translates to:
  /// **'Block user'**
  String get chatBlockUser;

  /// 3-dot menu item
  ///
  /// In en, this message translates to:
  /// **'Unblock user'**
  String get chatUnblockUser;

  /// Composer replacement when the other user is blocked
  ///
  /// In en, this message translates to:
  /// **'You\'ve blocked this user. Unblock to send messages.'**
  String get chatBlockedBanner;

  /// Button in the blocked banner
  ///
  /// In en, this message translates to:
  /// **'Unblock'**
  String get chatUnblock;

  /// Disappearing messages duration: off
  ///
  /// In en, this message translates to:
  /// **'Off'**
  String get chatDisappearingOff;

  /// Disappearing messages duration
  ///
  /// In en, this message translates to:
  /// **'1 Month'**
  String get chatDisappear1Month;

  /// Disappearing messages duration
  ///
  /// In en, this message translates to:
  /// **'6 Months'**
  String get chatDisappear6Months;

  /// Disappearing messages duration
  ///
  /// In en, this message translates to:
  /// **'1 Year'**
  String get chatDisappear1Year;

  /// Title of the disappearing messages sheet and menu item
  ///
  /// In en, this message translates to:
  /// **'Disappearing messages'**
  String get chatDisappearingTitle;

  /// Helper text in the disappearing messages sheet
  ///
  /// In en, this message translates to:
  /// **'New messages will automatically disappear from the chat after the selected time.'**
  String get chatDisappearingSubtitle;

  /// Snackbar / system message when turned off
  ///
  /// In en, this message translates to:
  /// **'Disappearing messages turned off'**
  String get chatDisappearingTurnedOff;

  /// Snackbar after enabling disappearing messages
  ///
  /// In en, this message translates to:
  /// **'New messages will disappear after {duration}'**
  String chatDisappearingNewMessages(String duration);

  /// System message when another member changes the timer
  ///
  /// In en, this message translates to:
  /// **'Disappearing messages set to {duration}'**
  String chatDisappearingSetTo(String duration);

  /// 3-dot menu item showing the current timer
  ///
  /// In en, this message translates to:
  /// **'Disappearing: {duration}'**
  String chatDisappearingMenu(String duration);

  /// Title of the group message permissions sheet and menu item
  ///
  /// In en, this message translates to:
  /// **'Message permissions'**
  String get chatPermissionsTitle;

  /// Helper text in the permissions sheet
  ///
  /// In en, this message translates to:
  /// **'Decide who can send messages in this group.'**
  String get chatPermissionsSubtitle;

  /// Permission option: everyone can send
  ///
  /// In en, this message translates to:
  /// **'Everyone'**
  String get chatPermEveryone;

  /// Subtitle of the everyone option
  ///
  /// In en, this message translates to:
  /// **'All members can chat'**
  String get chatPermEveryoneSub;

  /// Permission option: admins and moderators only
  ///
  /// In en, this message translates to:
  /// **'Only admins & moderators'**
  String get chatPermAdminsOnly;

  /// Subtitle of the admins-only option
  ///
  /// In en, this message translates to:
  /// **'Everyone else can only read, not send messages'**
  String get chatPermAdminsOnlySub;

  /// Label of the daily limit field
  ///
  /// In en, this message translates to:
  /// **'Daily message limit (members)'**
  String get chatDailyLimitTitle;

  /// Help text of the daily limit field
  ///
  /// In en, this message translates to:
  /// **'Regular members can send only this many messages per day (admins/moderators are always unlimited). Leave empty for no limit.'**
  String get chatDailyLimitHelp;

  /// Hint of the daily limit field
  ///
  /// In en, this message translates to:
  /// **'e.g. 4'**
  String get chatDailyLimitHint;

  /// Suffix of the daily limit field
  ///
  /// In en, this message translates to:
  /// **'msgs / day'**
  String get chatDailyLimitSuffix;

  /// Snackbar after restricting the group
  ///
  /// In en, this message translates to:
  /// **'Now only admins & moderators can send messages.'**
  String get chatPermUpdatedAdminsOnly;

  /// Snackbar after opening the group
  ///
  /// In en, this message translates to:
  /// **'Now all members can send messages.'**
  String get chatPermUpdatedEveryone;

  /// Error updating group permissions
  ///
  /// In en, this message translates to:
  /// **'Update failed: {error}'**
  String chatUpdateFailedDetail(String error);

  /// Composer replacement in an admins-only group
  ///
  /// In en, this message translates to:
  /// **'Only admins and moderators can send messages in this group.'**
  String get chatAdminsOnlyBanner;

  /// Dialog title when the daily message limit is hit
  ///
  /// In en, this message translates to:
  /// **'Daily limit reached'**
  String get chatDailyLimitReached;

  /// Dialog title when the user was removed from the group
  ///
  /// In en, this message translates to:
  /// **'You\'re no longer a member'**
  String get chatNoLongerMember;

  /// Dialog title when sending is not permitted
  ///
  /// In en, this message translates to:
  /// **'Messages not allowed'**
  String get chatMessageNotAllowed;

  /// 3-dot menu item
  ///
  /// In en, this message translates to:
  /// **'Group info'**
  String get chatGroupInfo;

  /// 3-dot menu item
  ///
  /// In en, this message translates to:
  /// **'Change group photo'**
  String get chatChangeGroupPhoto;

  /// Snackbar while uploading the group photo
  ///
  /// In en, this message translates to:
  /// **'Uploading photo...'**
  String get chatUploadingPhoto;

  /// Snackbar after the group photo changed
  ///
  /// In en, this message translates to:
  /// **'Group photo updated ✅'**
  String get chatGroupPhotoUpdated;

  /// Error updating the group photo
  ///
  /// In en, this message translates to:
  /// **'Photo update failed: {error}'**
  String chatPhotoUpdateFailed(String error);

  /// Title of the join requests sheet and menu item
  ///
  /// In en, this message translates to:
  /// **'Join requests'**
  String get chatJoinRequestsTitle;

  /// 3-dot menu item with a pending count
  ///
  /// In en, this message translates to:
  /// **'Join requests ({count})'**
  String chatJoinRequestsCount(int count);

  /// Empty state of the join requests sheet
  ///
  /// In en, this message translates to:
  /// **'No pending requests'**
  String get chatNoPendingRequests;

  /// Approve a join request
  ///
  /// In en, this message translates to:
  /// **'Approve'**
  String get chatApprove;

  /// Error approving a join request
  ///
  /// In en, this message translates to:
  /// **'Approve failed: {error}'**
  String chatApproveFailed(String error);

  /// Reject a join request
  ///
  /// In en, this message translates to:
  /// **'Reject'**
  String get chatReject;

  /// Error rejecting a join request
  ///
  /// In en, this message translates to:
  /// **'Reject failed: {error}'**
  String chatRejectFailed(String error);

  /// 3-dot menu item
  ///
  /// In en, this message translates to:
  /// **'Leave group'**
  String get chatLeaveGroup;

  /// Title of the leave dialog
  ///
  /// In en, this message translates to:
  /// **'Leave group?'**
  String get chatLeaveGroupTitle;

  /// Body of the leave dialog
  ///
  /// In en, this message translates to:
  /// **'You\'ll no longer receive messages from \"{name}\".'**
  String chatLeaveGroupBody(String name);

  /// Confirm button of the leave dialog
  ///
  /// In en, this message translates to:
  /// **'Leave'**
  String get chatLeave;

  /// Error leaving a group
  ///
  /// In en, this message translates to:
  /// **'Leave failed: {error}'**
  String chatLeaveFailed(String error);

  /// 3-dot menu item
  ///
  /// In en, this message translates to:
  /// **'Delete group'**
  String get chatDeleteGroup;

  /// Title of the delete group dialog
  ///
  /// In en, this message translates to:
  /// **'Delete group?'**
  String get chatDeleteGroupTitle;

  /// Body of the delete group dialog
  ///
  /// In en, this message translates to:
  /// **'\"{name}\" will be deleted permanently — for all members, along with all messages and media. This can\'t be undone.'**
  String chatDeleteGroupBody(String name);

  /// Error deleting a group or a message
  ///
  /// In en, this message translates to:
  /// **'Delete failed: {error}'**
  String chatDeleteFailed(String error);

  /// Notice when the group was deleted remotely
  ///
  /// In en, this message translates to:
  /// **'This group was deleted by the admin.'**
  String get chatGroupDeletedByAdmin;

  /// 3-dot menu item
  ///
  /// In en, this message translates to:
  /// **'Search in chat'**
  String get chatSearchInChat;

  /// Study room menu item, card title and message preview
  ///
  /// In en, this message translates to:
  /// **'Study Room'**
  String get chatStudyRoom;

  /// App bar / menu action
  ///
  /// In en, this message translates to:
  /// **'Audio Call'**
  String get chatAudioCall;

  /// App bar / menu action
  ///
  /// In en, this message translates to:
  /// **'Video Call'**
  String get chatVideoCall;

  /// Error starting a call
  ///
  /// In en, this message translates to:
  /// **'Call failed: {error}'**
  String chatCallFailed(String error);

  /// Subtitle of the study room card in the chat
  ///
  /// In en, this message translates to:
  /// **'Whiteboard, timer and live call — all in one place.'**
  String get chatStudyRoomCardSubtitle;

  /// Call to action on the study room card
  ///
  /// In en, this message translates to:
  /// **'Tap to Join'**
  String get chatTapToJoin;

  /// Title of the create poll dialog
  ///
  /// In en, this message translates to:
  /// **'Create poll'**
  String get chatCreatePoll;

  /// Poll question field label
  ///
  /// In en, this message translates to:
  /// **'Question'**
  String get chatPollQuestion;

  /// Poll toggle
  ///
  /// In en, this message translates to:
  /// **'Allow multiple answers'**
  String get chatAllowMultipleAnswers;

  /// Validation message for a new poll
  ///
  /// In en, this message translates to:
  /// **'A question and at least 2 options are required'**
  String get chatPollNeedsQuestionAndTwo;

  /// Button that posts the poll
  ///
  /// In en, this message translates to:
  /// **'Send poll'**
  String get chatSendPoll;

  /// Vote count under a poll
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 vote} other{{count} votes}}'**
  String chatPollVotes(int count);

  /// Vote count under a closed poll
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 vote} other{{count} votes}} • Closed'**
  String chatPollVotesClosed(int count);

  /// Attach option and dialog title
  ///
  /// In en, this message translates to:
  /// **'Schedule message'**
  String get chatScheduleMessageTitle;

  /// Label of the message field in the schedule dialog
  ///
  /// In en, this message translates to:
  /// **'Message'**
  String get chatMessageFieldLabel;

  /// Button that opens scheduled messages
  ///
  /// In en, this message translates to:
  /// **'View scheduled'**
  String get chatViewScheduled;

  /// Validation message when the picked time is in the past
  ///
  /// In en, this message translates to:
  /// **'Pick a future time'**
  String get chatPickFutureTime;

  /// Snackbar after scheduling
  ///
  /// In en, this message translates to:
  /// **'Message scheduled'**
  String get chatMessageScheduled;

  /// Title of the scheduled messages sheet and menu item
  ///
  /// In en, this message translates to:
  /// **'Scheduled messages'**
  String get chatScheduledMessagesTitle;

  /// Empty state of scheduled messages
  ///
  /// In en, this message translates to:
  /// **'No scheduled messages'**
  String get chatNoScheduled;

  /// Subtitle in the app bar. {when} is chatLastSeenToday / Yesterday / On
  ///
  /// In en, this message translates to:
  /// **'last seen {when}'**
  String chatLastSeen(String when);

  /// Part of last seen
  ///
  /// In en, this message translates to:
  /// **'today at {time}'**
  String chatLastSeenToday(String time);

  /// Part of last seen
  ///
  /// In en, this message translates to:
  /// **'yesterday at {time}'**
  String chatLastSeenYesterday(String time);

  /// Part of last seen
  ///
  /// In en, this message translates to:
  /// **'on {date}'**
  String chatLastSeenOn(String date);

  /// Error uploading an attachment
  ///
  /// In en, this message translates to:
  /// **'Upload failed: {error}'**
  String chatUploadFailed(String error);

  /// Error sending a sticker
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t send sticker: {error}'**
  String chatStickerFailed(String error);

  /// Snackbar when the mic permission is missing
  ///
  /// In en, this message translates to:
  /// **'Microphone permission is needed to send voice notes'**
  String get chatMicPermission;

  /// Snackbar when the location permission is denied
  ///
  /// In en, this message translates to:
  /// **'Location permission denied'**
  String get chatLocationPermissionDenied;

  /// Error sharing the location
  ///
  /// In en, this message translates to:
  /// **'Location share failed: {error}'**
  String chatLocationShareFailed(String error);

  /// Attach sheet option
  ///
  /// In en, this message translates to:
  /// **'Photo Gallery'**
  String get chatPhotoGallery;

  /// Attach sheet option
  ///
  /// In en, this message translates to:
  /// **'Video Gallery'**
  String get chatVideoGallery;

  /// Attach sheet option
  ///
  /// In en, this message translates to:
  /// **'Audio'**
  String get chatAudio;

  /// Attach sheet option and generic file label
  ///
  /// In en, this message translates to:
  /// **'File'**
  String get chatFile;

  /// Attach sheet option and generic presentation label
  ///
  /// In en, this message translates to:
  /// **'Presentation'**
  String get chatPresentation;

  /// Message action
  ///
  /// In en, this message translates to:
  /// **'Forward'**
  String get chatForward;

  /// Message action
  ///
  /// In en, this message translates to:
  /// **'React'**
  String get chatReact;

  /// Message action
  ///
  /// In en, this message translates to:
  /// **'Info'**
  String get chatInfo;

  /// Message action
  ///
  /// In en, this message translates to:
  /// **'Select'**
  String get chatSelect;

  /// Message action
  ///
  /// In en, this message translates to:
  /// **'Save to device'**
  String get chatSaveToDevice;

  /// Message action
  ///
  /// In en, this message translates to:
  /// **'Delete for me'**
  String get chatDeleteForMe;

  /// Message action
  ///
  /// In en, this message translates to:
  /// **'Delete for everyone'**
  String get chatDeleteForEveryone;

  /// Snackbar after forwarding
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{Message forwarded} other{{count} messages forwarded}}'**
  String chatMessageForwarded(int count);

  /// App bar title in selection mode
  ///
  /// In en, this message translates to:
  /// **'{count} selected'**
  String chatSelectedCount(int count);

  /// Title of the edit message dialog
  ///
  /// In en, this message translates to:
  /// **'Edit message'**
  String get chatEditMessageTitle;

  /// Error editing a message
  ///
  /// In en, this message translates to:
  /// **'Edit failed: {error}'**
  String chatEditFailed(String error);

  /// Placeholder for a message deleted for everyone
  ///
  /// In en, this message translates to:
  /// **'This message was deleted'**
  String get chatMessageDeleted;

  /// Snackbar when the file is already in the gallery
  ///
  /// In en, this message translates to:
  /// **'Already saved in gallery ✅'**
  String get chatAlreadySaved;

  /// Snackbar while downloading
  ///
  /// In en, this message translates to:
  /// **'Downloading...'**
  String get chatDownloading;

  /// Snackbar after saving media to the gallery
  ///
  /// In en, this message translates to:
  /// **'Saved to gallery ✅'**
  String get chatSavedToGallery;

  /// Snackbar after downloading a file
  ///
  /// In en, this message translates to:
  /// **'Downloaded ✅ — in the Download/LearnScroll folder'**
  String get chatDownloadedToFolder;

  /// Error downloading a file
  ///
  /// In en, this message translates to:
  /// **'Download failed: {error}'**
  String chatDownloadFailed(String error);

  /// Empty chat title
  ///
  /// In en, this message translates to:
  /// **'Say hi 👋'**
  String get chatSayHi;

  /// Empty chat subtitle
  ///
  /// In en, this message translates to:
  /// **'Send a message to start the conversation'**
  String get chatSendToStart;

  /// Composer hint
  ///
  /// In en, this message translates to:
  /// **'Message...'**
  String get chatMessageHint;

  /// Shown while a voice note is being recorded
  ///
  /// In en, this message translates to:
  /// **'Recording...'**
  String get chatRecording;

  /// Reply/last-message preview for a photo
  ///
  /// In en, this message translates to:
  /// **'📷 Photo'**
  String get chatPreviewPhoto;

  /// Reply/last-message preview for a video
  ///
  /// In en, this message translates to:
  /// **'🎥 Video'**
  String get chatPreviewVideo;

  /// Reply/last-message preview for audio
  ///
  /// In en, this message translates to:
  /// **'🎵 Audio'**
  String get chatPreviewAudio;

  /// Reply/last-message preview for a file
  ///
  /// In en, this message translates to:
  /// **'📄 File'**
  String get chatPreviewFile;

  /// Reply/last-message preview for a presentation
  ///
  /// In en, this message translates to:
  /// **'📊 Presentation'**
  String get chatPreviewPresentation;

  /// Reply/last-message preview for a location
  ///
  /// In en, this message translates to:
  /// **'📍 Location'**
  String get chatPreviewLocation;

  /// Reply/last-message preview for a study room
  ///
  /// In en, this message translates to:
  /// **'🧑‍🎓 Study Room'**
  String get chatPreviewStudyRoom;

  /// Reply/last-message preview for a poll
  ///
  /// In en, this message translates to:
  /// **'📊 Poll'**
  String get chatPreviewPoll;

  /// Date separator between messages
  ///
  /// In en, this message translates to:
  /// **'Today'**
  String get chatToday;

  /// Date separator between messages
  ///
  /// In en, this message translates to:
  /// **'Yesterday'**
  String get chatYesterday;

  /// Label on an announcement message
  ///
  /// In en, this message translates to:
  /// **'Announcement'**
  String get chatAnnouncement;

  /// Upload progress inside a message bubble
  ///
  /// In en, this message translates to:
  /// **'{percent}% uploading...'**
  String chatUploadingPercent(int percent);

  /// Upload progress for a photo album bubble
  ///
  /// In en, this message translates to:
  /// **'{percent}% • {count, plural, =1{1 photo} other{{count} photos}}'**
  String chatUploadingPhotosPercent(int percent, int count);

  /// Photo count on an album bubble
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 photo} other{{count} photos}}'**
  String chatPhotosCount(int count);

  /// Label on a location message
  ///
  /// In en, this message translates to:
  /// **'Location shared'**
  String get chatLocationShared;

  /// Selected files count in the send preview
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 item} other{{count} items}}'**
  String chatItemsCount(int count);

  /// Audio bubble while uploading
  ///
  /// In en, this message translates to:
  /// **'Sending audio...'**
  String get chatSendingAudio;

  /// Audio bubble label
  ///
  /// In en, this message translates to:
  /// **'Audio message'**
  String get chatAudioMessage;

  /// Error playing audio
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t play audio: {error}'**
  String chatAudioPlayFailed(String error);

  /// Button that transcribes an audio message
  ///
  /// In en, this message translates to:
  /// **'Transcribe'**
  String get chatTranscribe;

  /// Shown while transcribing
  ///
  /// In en, this message translates to:
  /// **'Transcribing...'**
  String get chatTranscribing;

  /// Heading of an audio transcript
  ///
  /// In en, this message translates to:
  /// **'Transcript'**
  String get chatTranscript;

  /// Error transcribing audio
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t transcribe: {error}'**
  String chatTranscribeFailed(String error);

  /// Error loading a video
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load video: {error}'**
  String chatVideoLoadFailed(String error);

  /// App bar title of the parent/guardian screens
  ///
  /// In en, this message translates to:
  /// **'Parent Mode'**
  String get parentModeTitle;

  /// Heading on the parent code-entry screen
  ///
  /// In en, this message translates to:
  /// **'See your child\'s progress'**
  String get parentEntryHeading;

  /// Explanation under the heading on the parent code-entry screen
  ///
  /// In en, this message translates to:
  /// **'You\'ll only see attendance and assignment status — never chat messages. Enter the code your child gave you below.'**
  String get parentEntryBody;

  /// Hint inside the parent code text field
  ///
  /// In en, this message translates to:
  /// **'e.g. 7F3K9QRT'**
  String get parentEntryCodeHint;

  /// Validation error when the parent code field is empty
  ///
  /// In en, this message translates to:
  /// **'Enter the code'**
  String get parentEntryCodeRequired;

  /// Button that verifies the parent code
  ///
  /// In en, this message translates to:
  /// **'View Progress'**
  String get parentEntryViewProgress;

  /// Link on the login screen that opens Parent Mode
  ///
  /// In en, this message translates to:
  /// **'Parent/Guardian? View your child\'s progress'**
  String get parentLoginLink;

  /// Parent code verification failed (404)
  ///
  /// In en, this message translates to:
  /// **'Invalid or expired code. Please check it again.'**
  String get parentErrInvalidCode;

  /// Parent code verification rate-limited (429)
  ///
  /// In en, this message translates to:
  /// **'Too many attempts — please try again in a while.'**
  String get parentErrTooManyAttempts;

  /// Generic parent-mode error
  ///
  /// In en, this message translates to:
  /// **'Something went wrong. Please try again.'**
  String get parentErrGeneric;

  /// Parent session token missing or expired
  ///
  /// In en, this message translates to:
  /// **'Your session has expired. Enter the code again.'**
  String get parentErrSessionExpired;

  /// Parent access was revoked by the student
  ///
  /// In en, this message translates to:
  /// **'Access has been revoked. Ask your child for a new code.'**
  String get parentErrAccessRevoked;

  /// Parent dashboard failed to load
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load the dashboard. Please try again.'**
  String get parentErrDashboardLoad;

  /// Empty state of the parent dashboard
  ///
  /// In en, this message translates to:
  /// **'No classrooms found yet.'**
  String get parentDashNoClassrooms;

  /// Privacy note under the student name on the parent dashboard
  ///
  /// In en, this message translates to:
  /// **'Attendance and assignment status — chat content is never shown here.'**
  String get parentDashSubtitle;

  /// Attendance streak label
  ///
  /// In en, this message translates to:
  /// **'Current Streak'**
  String get parentDashStreak;

  /// Attendance streak value
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 day} other{{count} days}}'**
  String parentDashStreakDays(int count);

  /// Total classes attended label
  ///
  /// In en, this message translates to:
  /// **'Total Classes'**
  String get parentDashTotalClasses;

  /// Pending assignments label
  ///
  /// In en, this message translates to:
  /// **'Assignments Pending'**
  String get parentDashAssignmentsPending;

  /// Submitted assignments label
  ///
  /// In en, this message translates to:
  /// **'Submitted'**
  String get parentDashSubmitted;

  /// Tooltip of the parent sign-out icon
  ///
  /// In en, this message translates to:
  /// **'Sign out'**
  String get parentDashSignOut;

  /// Button shown when the parent session expired or was revoked
  ///
  /// In en, this message translates to:
  /// **'Enter a new code'**
  String get parentDashEnterNewCode;

  /// App bar title of the focus mode screen
  ///
  /// In en, this message translates to:
  /// **'Focus mode'**
  String get focusModeTitle;

  /// Tooltip of the history icon on the focus mode screen
  ///
  /// In en, this message translates to:
  /// **'History'**
  String get focusModeHistoryTooltip;

  /// Section title while a focus session is active
  ///
  /// In en, this message translates to:
  /// **'Change duration'**
  String get focusModeChangeDuration;

  /// Section title for choosing the focus duration
  ///
  /// In en, this message translates to:
  /// **'How long?'**
  String get focusModeHowLong;

  /// Button and sheet title for a custom focus duration
  ///
  /// In en, this message translates to:
  /// **'Custom duration'**
  String get focusModeCustomDuration;

  /// Section title for the exception rule
  ///
  /// In en, this message translates to:
  /// **'Who can still reach you?'**
  String get focusModeWhoCanReach;

  /// Exception rule: teachers only
  ///
  /// In en, this message translates to:
  /// **'Only teachers & staff'**
  String get focusModeRuleTeachersTitle;

  /// Explanation of the teachers-only rule
  ///
  /// In en, this message translates to:
  /// **'Messages and calls from group admins/moderators still come through; everyone else stays silent.'**
  String get focusModeRuleTeachersSub;

  /// Exception rule: nobody
  ///
  /// In en, this message translates to:
  /// **'Nobody — full silence'**
  String get focusModeRuleNobodyTitle;

  /// Explanation of the nobody rule
  ///
  /// In en, this message translates to:
  /// **'For exam time — no notification will come through, not even from teachers.'**
  String get focusModeRuleNobodySub;

  /// Primary button while a session is active
  ///
  /// In en, this message translates to:
  /// **'Update focus mode'**
  String get focusModeUpdate;

  /// Primary button to start a session
  ///
  /// In en, this message translates to:
  /// **'Start focus mode'**
  String get focusModeStart;

  /// Button to end the active session
  ///
  /// In en, this message translates to:
  /// **'End focus mode now'**
  String get focusModeEndNow;

  /// Banner with the remaining time
  ///
  /// In en, this message translates to:
  /// **'Focus mode is active — {time} left'**
  String focusModeActiveLeft(String time);

  /// Error when starting/stopping a focus session fails
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t update focus mode. Please try again.'**
  String get focusModeFailed;

  /// Confirm button of the custom duration sheet
  ///
  /// In en, this message translates to:
  /// **'Set'**
  String get focusModeSet;

  /// Hours stepper label
  ///
  /// In en, this message translates to:
  /// **'Hours'**
  String get focusModeHours;

  /// Minutes stepper label
  ///
  /// In en, this message translates to:
  /// **'Minutes'**
  String get focusModeMinutes;

  /// Presence subtitle while the other person is typing
  ///
  /// In en, this message translates to:
  /// **'typing...'**
  String get chatTyping;

  /// Presence subtitle when the other person is online
  ///
  /// In en, this message translates to:
  /// **'online'**
  String get chatOnline;

  /// No description provided for @enterClassCta.
  ///
  /// In en, this message translates to:
  /// **'Enter class'**
  String get enterClassCta;

  /// No description provided for @requestPendingCta.
  ///
  /// In en, this message translates to:
  /// **'Request pending'**
  String get requestPendingCta;

  /// No description provided for @endClassCta.
  ///
  /// In en, this message translates to:
  /// **'End class'**
  String get endClassCta;

  /// No description provided for @endClassConfirm.
  ///
  /// In en, this message translates to:
  /// **'End this class for everyone?'**
  String get endClassConfirm;

  /// No description provided for @removedFromSession.
  ///
  /// In en, this message translates to:
  /// **'You were removed from this session.'**
  String get removedFromSession;

  /// No description provided for @waitlistedNotice.
  ///
  /// In en, this message translates to:
  /// **'The class is full — you are on the waitlist and will be let in automatically.'**
  String get waitlistedNotice;

  /// No description provided for @waitingForTeacher.
  ///
  /// In en, this message translates to:
  /// **'Waiting for the teacher to start video…'**
  String get waitingForTeacher;

  /// No description provided for @savedPostsTitle.
  ///
  /// In en, this message translates to:
  /// **'Saved posts'**
  String get savedPostsTitle;

  /// No description provided for @explorePostsTitle.
  ///
  /// In en, this message translates to:
  /// **'Explore'**
  String get explorePostsTitle;

  /// No description provided for @noPostsHere.
  ///
  /// In en, this message translates to:
  /// **'No posts here yet'**
  String get noPostsHere;

  /// No description provided for @postsLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load posts'**
  String get postsLoadFailed;

  /// No description provided for @deletePostCta.
  ///
  /// In en, this message translates to:
  /// **'Delete post'**
  String get deletePostCta;

  /// No description provided for @deletePostConfirm.
  ///
  /// In en, this message translates to:
  /// **'Delete this post? This can’t be undone.'**
  String get deletePostConfirm;

  /// No description provided for @postDeleted.
  ///
  /// In en, this message translates to:
  /// **'Post deleted'**
  String get postDeleted;

  /// No description provided for @postDeleteFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t delete the post'**
  String get postDeleteFailed;

  /// No description provided for @allCaughtUp.
  ///
  /// In en, this message translates to:
  /// **'You\'re all caught up'**
  String get allCaughtUp;

  /// No description provided for @back.
  ///
  /// In en, this message translates to:
  /// **'Back'**
  String get back;

  /// No description provided for @bioLabel.
  ///
  /// In en, this message translates to:
  /// **'Bio'**
  String get bioLabel;

  /// No description provided for @changePhotoTooltip.
  ///
  /// In en, this message translates to:
  /// **'Change photo'**
  String get changePhotoTooltip;

  /// No description provided for @discardChangesTitle.
  ///
  /// In en, this message translates to:
  /// **'Discard changes?'**
  String get discardChangesTitle;

  /// No description provided for @discardChangesMessage.
  ///
  /// In en, this message translates to:
  /// **'You have unsaved changes. Are you sure you want to go back?'**
  String get discardChangesMessage;

  /// No description provided for @downloading.
  ///
  /// In en, this message translates to:
  /// **'Downloading…'**
  String get downloading;

  /// No description provided for @editProfileButton.
  ///
  /// In en, this message translates to:
  /// **'Edit profile'**
  String get editProfileButton;

  /// No description provided for @follow.
  ///
  /// In en, this message translates to:
  /// **'Follow'**
  String get follow;

  /// No description provided for @followBack.
  ///
  /// In en, this message translates to:
  /// **'Follow Back'**
  String get followBack;

  /// No description provided for @followersStat.
  ///
  /// In en, this message translates to:
  /// **'Followers'**
  String get followersStat;

  /// No description provided for @messageButton.
  ///
  /// In en, this message translates to:
  /// **'Message'**
  String get messageButton;

  /// No description provided for @moreOptions.
  ///
  /// In en, this message translates to:
  /// **'More options'**
  String get moreOptions;

  /// No description provided for @mediaTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Media ({count})'**
  String mediaTabLabel(int count);

  /// No description provided for @documentsTabLabel.
  ///
  /// In en, this message translates to:
  /// **'Documents ({count})'**
  String documentsTabLabel(int count);

  /// No description provided for @noDocumentsYetTitle.
  ///
  /// In en, this message translates to:
  /// **'No documents yet'**
  String get noDocumentsYetTitle;

  /// No description provided for @noDocumentsYetSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Documents you share will appear here.'**
  String get noDocumentsYetSubtitle;

  /// No description provided for @noMediaYetTitle.
  ///
  /// In en, this message translates to:
  /// **'No media yet'**
  String get noMediaYetTitle;

  /// No description provided for @noMediaYetSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Photos and videos you post will appear here.'**
  String get noMediaYetSubtitle;

  /// No description provided for @noNameYet.
  ///
  /// In en, this message translates to:
  /// **'No name yet'**
  String get noNameYet;

  /// No description provided for @noProfileFound.
  ///
  /// In en, this message translates to:
  /// **'Profile not found'**
  String get noProfileFound;

  /// No description provided for @photoPostLabel.
  ///
  /// In en, this message translates to:
  /// **'Photo'**
  String get photoPostLabel;

  /// No description provided for @videoPostLabel.
  ///
  /// In en, this message translates to:
  /// **'Video'**
  String get videoPostLabel;

  /// No description provided for @postsLoadErrorTitle.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load posts'**
  String get postsLoadErrorTitle;

  /// No description provided for @postsLoadErrorSubtitle.
  ///
  /// In en, this message translates to:
  /// **'Check your connection and pull down to retry.'**
  String get postsLoadErrorSubtitle;

  /// No description provided for @postsStat.
  ///
  /// In en, this message translates to:
  /// **'Posts'**
  String get postsStat;

  /// No description provided for @privateAccountBadge.
  ///
  /// In en, this message translates to:
  /// **'Private'**
  String get privateAccountBadge;

  /// No description provided for @privateAccountMessage.
  ///
  /// In en, this message translates to:
  /// **'This account is private. Follow to see their posts.'**
  String get privateAccountMessage;

  /// No description provided for @privateAccountPendingMessage.
  ///
  /// In en, this message translates to:
  /// **'Follow request sent. Wait for approval to see posts.'**
  String get privateAccountPendingMessage;

  /// No description provided for @profileLoadErrorTitle.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load profile'**
  String get profileLoadErrorTitle;

  /// No description provided for @profileUpdatedSuccess.
  ///
  /// In en, this message translates to:
  /// **'Profile updated successfully'**
  String get profileUpdatedSuccess;

  /// No description provided for @requestedLabel.
  ///
  /// In en, this message translates to:
  /// **'Requested'**
  String get requestedLabel;

  /// No description provided for @shareProfileButton.
  ///
  /// In en, this message translates to:
  /// **'Share profile'**
  String get shareProfileButton;

  /// No description provided for @shareProfileMessage.
  ///
  /// In en, this message translates to:
  /// **'Check out {username} on LearnScroll: {url}'**
  String shareProfileMessage(String username, String url);

  /// No description provided for @showingSavedProfileData.
  ///
  /// In en, this message translates to:
  /// **'Showing saved data — pull down to refresh'**
  String get showingSavedProfileData;

  /// No description provided for @verifiedAccount.
  ///
  /// In en, this message translates to:
  /// **'Verified account'**
  String get verifiedAccount;

  /// No description provided for @coinsBalance.
  ///
  /// In en, this message translates to:
  /// **'{coins} coins'**
  String coinsBalance(num coins);

  /// No description provided for @chatOpenFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t open chat: {error}'**
  String chatOpenFailed(String error);

  /// No description provided for @analyticsTitle.
  ///
  /// In en, this message translates to:
  /// **'Analytics'**
  String get analyticsTitle;

  /// No description provided for @analyticsActiveEnrollments.
  ///
  /// In en, this message translates to:
  /// **'Active enrollments'**
  String get analyticsActiveEnrollments;

  /// No description provided for @analyticsAvgAttendance.
  ///
  /// In en, this message translates to:
  /// **'Average attendance'**
  String get analyticsAvgAttendance;

  /// No description provided for @analyticsAvgMarks.
  ///
  /// In en, this message translates to:
  /// **'Average marks'**
  String get analyticsAvgMarks;

  /// No description provided for @analyticsSyllabusCompletion.
  ///
  /// In en, this message translates to:
  /// **'Syllabus completion'**
  String get analyticsSyllabusCompletion;

  /// No description provided for @analyticsNoSnapshot.
  ///
  /// In en, this message translates to:
  /// **'No analytics yet'**
  String get analyticsNoSnapshot;

  /// No description provided for @analyticsNoSnapshotHint.
  ///
  /// In en, this message translates to:
  /// **'Analytics are computed periodically. Check back later.'**
  String get analyticsNoSnapshotHint;

  /// No description provided for @analyticsComputedAt.
  ///
  /// In en, this message translates to:
  /// **'Computed {date}'**
  String analyticsComputedAt(String date);

  /// No description provided for @assignmentDueDateLabel.
  ///
  /// In en, this message translates to:
  /// **'Due date'**
  String get assignmentDueDateLabel;

  /// No description provided for @assignmentFeedbackLabel.
  ///
  /// In en, this message translates to:
  /// **'Feedback'**
  String get assignmentFeedbackLabel;

  /// No description provided for @assignmentGradeLabel.
  ///
  /// In en, this message translates to:
  /// **'Grade'**
  String get assignmentGradeLabel;

  /// No description provided for @assignmentNoSubmissions.
  ///
  /// In en, this message translates to:
  /// **'No submissions yet'**
  String get assignmentNoSubmissions;

  /// No description provided for @assignmentNotSubmitted.
  ///
  /// In en, this message translates to:
  /// **'Not submitted'**
  String get assignmentNotSubmitted;

  /// No description provided for @assignmentSubmitCta.
  ///
  /// In en, this message translates to:
  /// **'Submit'**
  String get assignmentSubmitCta;

  /// No description provided for @assignmentsAdd.
  ///
  /// In en, this message translates to:
  /// **'Add assignment'**
  String get assignmentsAdd;

  /// No description provided for @assignmentsEmpty.
  ///
  /// In en, this message translates to:
  /// **'No assignments yet'**
  String get assignmentsEmpty;

  /// No description provided for @campusCreateTitle.
  ///
  /// In en, this message translates to:
  /// **'Create campus'**
  String get campusCreateTitle;

  /// No description provided for @campusCreateNameLabel.
  ///
  /// In en, this message translates to:
  /// **'Campus name'**
  String get campusCreateNameLabel;

  /// No description provided for @campusCreateNameRequired.
  ///
  /// In en, this message translates to:
  /// **'Enter a campus name'**
  String get campusCreateNameRequired;

  /// No description provided for @campusCreateTypeLabel.
  ///
  /// In en, this message translates to:
  /// **'Campus type'**
  String get campusCreateTypeLabel;

  /// No description provided for @campusCreateTypeSchool.
  ///
  /// In en, this message translates to:
  /// **'School'**
  String get campusCreateTypeSchool;

  /// No description provided for @campusCreateTypeCollege.
  ///
  /// In en, this message translates to:
  /// **'College'**
  String get campusCreateTypeCollege;

  /// No description provided for @campusCreateTypeCoaching.
  ///
  /// In en, this message translates to:
  /// **'Coaching'**
  String get campusCreateTypeCoaching;

  /// No description provided for @campusCreateThresholdLabel.
  ///
  /// In en, this message translates to:
  /// **'Attendance threshold'**
  String get campusCreateThresholdLabel;

  /// No description provided for @campusCreateThresholdHint.
  ///
  /// In en, this message translates to:
  /// **'Students below this attendance percentage are flagged.'**
  String get campusCreateThresholdHint;

  /// No description provided for @campusCreateFeeModuleLabel.
  ///
  /// In en, this message translates to:
  /// **'Enable fee module'**
  String get campusCreateFeeModuleLabel;

  /// No description provided for @campusCreateFeeModuleHint.
  ///
  /// In en, this message translates to:
  /// **'Track fee structures, invoices and payments for this campus.'**
  String get campusCreateFeeModuleHint;

  /// No description provided for @campusCreateSubmit.
  ///
  /// In en, this message translates to:
  /// **'Create'**
  String get campusCreateSubmit;

  /// No description provided for @campusCreateSuccess.
  ///
  /// In en, this message translates to:
  /// **'Campus created'**
  String get campusCreateSuccess;

  /// No description provided for @campusManageSetupCta.
  ///
  /// In en, this message translates to:
  /// **'Manage setup'**
  String get campusManageSetupCta;

  /// No description provided for @campusNoneCreateCta.
  ///
  /// In en, this message translates to:
  /// **'Create a campus'**
  String get campusNoneCreateCta;

  /// No description provided for @campusSetupTitle.
  ///
  /// In en, this message translates to:
  /// **'Campus setup'**
  String get campusSetupTitle;

  /// No description provided for @classDepartmentLabel.
  ///
  /// In en, this message translates to:
  /// **'Department'**
  String get classDepartmentLabel;

  /// No description provided for @classDepartmentNone.
  ///
  /// In en, this message translates to:
  /// **'No department'**
  String get classDepartmentNone;

  /// No description provided for @classNameHint.
  ///
  /// In en, this message translates to:
  /// **'e.g. Class 10'**
  String get classNameHint;

  /// No description provided for @classNameLabel.
  ///
  /// In en, this message translates to:
  /// **'Class name'**
  String get classNameLabel;

  /// No description provided for @classNoSessionsError.
  ///
  /// In en, this message translates to:
  /// **'Create an academic session first'**
  String get classNoSessionsError;

  /// No description provided for @classSectionsCta.
  ///
  /// In en, this message translates to:
  /// **'Sections'**
  String get classSectionsCta;

  /// No description provided for @classSessionLabel.
  ///
  /// In en, this message translates to:
  /// **'Academic session'**
  String get classSessionLabel;

  /// No description provided for @classTeacherAssign.
  ///
  /// In en, this message translates to:
  /// **'Assign class teacher'**
  String get classTeacherAssign;

  /// No description provided for @classTeacherAssignedSuccess.
  ///
  /// In en, this message translates to:
  /// **'Class teacher assigned'**
  String get classTeacherAssignedSuccess;

  /// No description provided for @classTeacherNotAssigned.
  ///
  /// In en, this message translates to:
  /// **'No class teacher assigned'**
  String get classTeacherNotAssigned;

  /// No description provided for @classTeacherTitle.
  ///
  /// In en, this message translates to:
  /// **'Class teacher'**
  String get classTeacherTitle;

  /// No description provided for @departmentNameLabel.
  ///
  /// In en, this message translates to:
  /// **'Department name'**
  String get departmentNameLabel;

  /// No description provided for @examTermNameLabel.
  ///
  /// In en, this message translates to:
  /// **'Exam term name'**
  String get examTermNameLabel;

  /// No description provided for @examTermPickLabel.
  ///
  /// In en, this message translates to:
  /// **'Exam term'**
  String get examTermPickLabel;

  /// No description provided for @sectionDetailTitle.
  ///
  /// In en, this message translates to:
  /// **'{className} · {sectionName}'**
  String sectionDetailTitle(String className, String sectionName);

  /// No description provided for @sectionNameHint.
  ///
  /// In en, this message translates to:
  /// **'e.g. A'**
  String get sectionNameHint;

  /// No description provided for @sectionNameLabel.
  ///
  /// In en, this message translates to:
  /// **'Section name'**
  String get sectionNameLabel;

  /// No description provided for @sessionCurrentBadge.
  ///
  /// In en, this message translates to:
  /// **'Current'**
  String get sessionCurrentBadge;

  /// No description provided for @sessionDateOrderError.
  ///
  /// In en, this message translates to:
  /// **'End date must be after the start date'**
  String get sessionDateOrderError;

  /// No description provided for @sessionDatesRequiredError.
  ///
  /// In en, this message translates to:
  /// **'Pick a start and an end date'**
  String get sessionDatesRequiredError;

  /// No description provided for @sessionEndDateLabel.
  ///
  /// In en, this message translates to:
  /// **'End date'**
  String get sessionEndDateLabel;

  /// No description provided for @sessionNameHint.
  ///
  /// In en, this message translates to:
  /// **'e.g. 2026–27'**
  String get sessionNameHint;

  /// No description provided for @sessionNameLabel.
  ///
  /// In en, this message translates to:
  /// **'Session name'**
  String get sessionNameLabel;

  /// No description provided for @sessionSetCurrentLabel.
  ///
  /// In en, this message translates to:
  /// **'Set as current'**
  String get sessionSetCurrentLabel;

  /// No description provided for @sessionSetCurrentSuccess.
  ///
  /// In en, this message translates to:
  /// **'{name} is now the current session'**
  String sessionSetCurrentSuccess(String name);

  /// No description provided for @sessionStartDateLabel.
  ///
  /// In en, this message translates to:
  /// **'Start date'**
  String get sessionStartDateLabel;

  /// No description provided for @setupClassesAdd.
  ///
  /// In en, this message translates to:
  /// **'Add class'**
  String get setupClassesAdd;

  /// No description provided for @setupClassesEmpty.
  ///
  /// In en, this message translates to:
  /// **'No classes yet'**
  String get setupClassesEmpty;

  /// No description provided for @setupClassesTitle.
  ///
  /// In en, this message translates to:
  /// **'Classes'**
  String get setupClassesTitle;

  /// No description provided for @setupDepartmentsAdd.
  ///
  /// In en, this message translates to:
  /// **'Add department'**
  String get setupDepartmentsAdd;

  /// No description provided for @setupDepartmentsEmpty.
  ///
  /// In en, this message translates to:
  /// **'No departments yet'**
  String get setupDepartmentsEmpty;

  /// No description provided for @setupDepartmentsTitle.
  ///
  /// In en, this message translates to:
  /// **'Departments'**
  String get setupDepartmentsTitle;

  /// No description provided for @setupExamTermsAdd.
  ///
  /// In en, this message translates to:
  /// **'Add exam term'**
  String get setupExamTermsAdd;

  /// No description provided for @setupExamTermsEmpty.
  ///
  /// In en, this message translates to:
  /// **'No exam terms yet'**
  String get setupExamTermsEmpty;

  /// No description provided for @setupExamTermsTitle.
  ///
  /// In en, this message translates to:
  /// **'Exam terms'**
  String get setupExamTermsTitle;

  /// No description provided for @setupFeeStructuresAdd.
  ///
  /// In en, this message translates to:
  /// **'Add fee structure'**
  String get setupFeeStructuresAdd;

  /// No description provided for @setupFeeStructuresEmpty.
  ///
  /// In en, this message translates to:
  /// **'No fee structures yet'**
  String get setupFeeStructuresEmpty;

  /// No description provided for @setupFeeStructuresTitle.
  ///
  /// In en, this message translates to:
  /// **'Fee structures'**
  String get setupFeeStructuresTitle;

  /// No description provided for @setupLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load this list'**
  String get setupLoadFailed;

  /// No description provided for @setupRoomsAdd.
  ///
  /// In en, this message translates to:
  /// **'Add room'**
  String get setupRoomsAdd;

  /// No description provided for @setupRoomsEmpty.
  ///
  /// In en, this message translates to:
  /// **'No rooms yet'**
  String get setupRoomsEmpty;

  /// No description provided for @setupRoomsTitle.
  ///
  /// In en, this message translates to:
  /// **'Rooms'**
  String get setupRoomsTitle;

  /// No description provided for @setupSectionsAdd.
  ///
  /// In en, this message translates to:
  /// **'Add section'**
  String get setupSectionsAdd;

  /// No description provided for @setupSectionsEmpty.
  ///
  /// In en, this message translates to:
  /// **'No sections yet'**
  String get setupSectionsEmpty;

  /// No description provided for @setupSectionsTitle.
  ///
  /// In en, this message translates to:
  /// **'Sections · {className}'**
  String setupSectionsTitle(String className);

  /// No description provided for @setupSessionsAdd.
  ///
  /// In en, this message translates to:
  /// **'Add session'**
  String get setupSessionsAdd;

  /// No description provided for @setupSessionsEmpty.
  ///
  /// In en, this message translates to:
  /// **'No sessions yet'**
  String get setupSessionsEmpty;

  /// No description provided for @setupSessionsTitle.
  ///
  /// In en, this message translates to:
  /// **'Academic sessions'**
  String get setupSessionsTitle;

  /// No description provided for @setupStaffAdd.
  ///
  /// In en, this message translates to:
  /// **'Add staff'**
  String get setupStaffAdd;

  /// No description provided for @setupStaffEmpty.
  ///
  /// In en, this message translates to:
  /// **'No staff yet'**
  String get setupStaffEmpty;

  /// No description provided for @setupStaffPendingApproval.
  ///
  /// In en, this message translates to:
  /// **'Staff you add may need the user\'s approval before they appear as active.'**
  String get setupStaffPendingApproval;

  /// No description provided for @setupStaffTitle.
  ///
  /// In en, this message translates to:
  /// **'Staff'**
  String get setupStaffTitle;

  /// No description provided for @setupSubjectsAdd.
  ///
  /// In en, this message translates to:
  /// **'Add subject'**
  String get setupSubjectsAdd;

  /// No description provided for @setupSubjectsEmpty.
  ///
  /// In en, this message translates to:
  /// **'No subjects yet'**
  String get setupSubjectsEmpty;

  /// No description provided for @setupSubjectsTitle.
  ///
  /// In en, this message translates to:
  /// **'Subjects'**
  String get setupSubjectsTitle;

  /// No description provided for @roomNameLabel.
  ///
  /// In en, this message translates to:
  /// **'Room name'**
  String get roomNameLabel;

  /// No description provided for @roomVirtualHint.
  ///
  /// In en, this message translates to:
  /// **'Virtual rooms are online classrooms with no physical location.'**
  String get roomVirtualHint;

  /// No description provided for @roomVirtualLabel.
  ///
  /// In en, this message translates to:
  /// **'Virtual'**
  String get roomVirtualLabel;

  /// No description provided for @staffPickLabel.
  ///
  /// In en, this message translates to:
  /// **'Staff member'**
  String get staffPickLabel;

  /// No description provided for @staffRoleLabel.
  ///
  /// In en, this message translates to:
  /// **'Role'**
  String get staffRoleLabel;

  /// No description provided for @staffUserIdHint.
  ///
  /// In en, this message translates to:
  /// **'Enter the user\'s ID'**
  String get staffUserIdHint;

  /// No description provided for @staffUserIdLabel.
  ///
  /// In en, this message translates to:
  /// **'User ID'**
  String get staffUserIdLabel;

  /// No description provided for @subjectCodeLabel.
  ///
  /// In en, this message translates to:
  /// **'Subject code'**
  String get subjectCodeLabel;

  /// No description provided for @subjectDepartmentLabel.
  ///
  /// In en, this message translates to:
  /// **'Department'**
  String get subjectDepartmentLabel;

  /// No description provided for @subjectNameLabel.
  ///
  /// In en, this message translates to:
  /// **'Subject name'**
  String get subjectNameLabel;

  /// No description provided for @subjectPickLabel.
  ///
  /// In en, this message translates to:
  /// **'Subject'**
  String get subjectPickLabel;

  /// No description provided for @subjectTeacherApprove.
  ///
  /// In en, this message translates to:
  /// **'Approve'**
  String get subjectTeacherApprove;

  /// No description provided for @subjectTeacherApproved.
  ///
  /// In en, this message translates to:
  /// **'Approved'**
  String get subjectTeacherApproved;

  /// No description provided for @subjectTeacherAssign.
  ///
  /// In en, this message translates to:
  /// **'Assign subject teacher'**
  String get subjectTeacherAssign;

  /// No description provided for @subjectTeacherReject.
  ///
  /// In en, this message translates to:
  /// **'Reject'**
  String get subjectTeacherReject;

  /// No description provided for @subjectTeacherRejected.
  ///
  /// In en, this message translates to:
  /// **'Rejected'**
  String get subjectTeacherRejected;

  /// No description provided for @subjectTeacherRequest.
  ///
  /// In en, this message translates to:
  /// **'Request assignment'**
  String get subjectTeacherRequest;

  /// No description provided for @subjectTeacherRequested.
  ///
  /// In en, this message translates to:
  /// **'Request sent — waiting for approval'**
  String get subjectTeacherRequested;

  /// No description provided for @subjectTeacherStatusApproved.
  ///
  /// In en, this message translates to:
  /// **'Approved'**
  String get subjectTeacherStatusApproved;

  /// No description provided for @subjectTeacherStatusPending.
  ///
  /// In en, this message translates to:
  /// **'Pending'**
  String get subjectTeacherStatusPending;

  /// No description provided for @subjectTeacherStatusRejected.
  ///
  /// In en, this message translates to:
  /// **'Rejected'**
  String get subjectTeacherStatusRejected;

  /// No description provided for @subjectTeachersEmpty.
  ///
  /// In en, this message translates to:
  /// **'No subject teachers yet'**
  String get subjectTeachersEmpty;

  /// No description provided for @subjectTeachersTitle.
  ///
  /// In en, this message translates to:
  /// **'Subject teachers'**
  String get subjectTeachersTitle;

  /// No description provided for @enrollRollNumberLabel.
  ///
  /// In en, this message translates to:
  /// **'Roll number'**
  String get enrollRollNumberLabel;

  /// No description provided for @enrollStatusActive.
  ///
  /// In en, this message translates to:
  /// **'Active'**
  String get enrollStatusActive;

  /// No description provided for @enrollStatusGraduated.
  ///
  /// In en, this message translates to:
  /// **'Graduated'**
  String get enrollStatusGraduated;

  /// No description provided for @enrollStatusLabel.
  ///
  /// In en, this message translates to:
  /// **'Status'**
  String get enrollStatusLabel;

  /// No description provided for @enrollStatusTransferred.
  ///
  /// In en, this message translates to:
  /// **'Transferred'**
  String get enrollStatusTransferred;

  /// No description provided for @enrollStudentIdHint.
  ///
  /// In en, this message translates to:
  /// **'Enter the student\'s user ID'**
  String get enrollStudentIdHint;

  /// No description provided for @enrollStudentIdLabel.
  ///
  /// In en, this message translates to:
  /// **'Student ID'**
  String get enrollStudentIdLabel;

  /// No description provided for @enrollmentAddedSuccess.
  ///
  /// In en, this message translates to:
  /// **'Student enrolled'**
  String get enrollmentAddedSuccess;

  /// No description provided for @enrollmentsAdd.
  ///
  /// In en, this message translates to:
  /// **'Enroll student'**
  String get enrollmentsAdd;

  /// No description provided for @enrollmentsEmpty.
  ///
  /// In en, this message translates to:
  /// **'No students enrolled yet'**
  String get enrollmentsEmpty;

  /// No description provided for @enrollmentsTitle.
  ///
  /// In en, this message translates to:
  /// **'Enrollments'**
  String get enrollmentsTitle;

  /// No description provided for @feeAmountDueLabel.
  ///
  /// In en, this message translates to:
  /// **'Due'**
  String get feeAmountDueLabel;

  /// No description provided for @feeAmountLabel.
  ///
  /// In en, this message translates to:
  /// **'Amount'**
  String get feeAmountLabel;

  /// No description provided for @feeAmountPaidLabel.
  ///
  /// In en, this message translates to:
  /// **'Paid'**
  String get feeAmountPaidLabel;

  /// No description provided for @feeCampusWide.
  ///
  /// In en, this message translates to:
  /// **'Campus-wide'**
  String get feeCampusWide;

  /// No description provided for @feeGenerateInvoices.
  ///
  /// In en, this message translates to:
  /// **'Generate invoices'**
  String get feeGenerateInvoices;

  /// No description provided for @feeInsufficientCoinsBody.
  ///
  /// In en, this message translates to:
  /// **'You need {coins} coins to pay this fee. Add coins to your wallet and try again.'**
  String feeInsufficientCoinsBody(num coins);

  /// No description provided for @feeInsufficientCoinsTitle.
  ///
  /// In en, this message translates to:
  /// **'Not enough coins'**
  String get feeInsufficientCoinsTitle;

  /// No description provided for @feeInvoicesGenerated.
  ///
  /// In en, this message translates to:
  /// **'{created} invoices created, {existing} already existed'**
  String feeInvoicesGenerated(int created, int existing);

  /// No description provided for @feeModuleDisabledNote.
  ///
  /// In en, this message translates to:
  /// **'The fee module is turned off for this campus.'**
  String get feeModuleDisabledNote;

  /// No description provided for @feeNoPayments.
  ///
  /// In en, this message translates to:
  /// **'No payments recorded'**
  String get feeNoPayments;

  /// No description provided for @feeNotesLabel.
  ///
  /// In en, this message translates to:
  /// **'Notes'**
  String get feeNotesLabel;

  /// No description provided for @feeOfficeTitle.
  ///
  /// In en, this message translates to:
  /// **'Fee office'**
  String get feeOfficeTitle;

  /// No description provided for @feePayCta.
  ///
  /// In en, this message translates to:
  /// **'Pay with coins'**
  String get feePayCta;

  /// No description provided for @feePaySuccess.
  ///
  /// In en, this message translates to:
  /// **'Payment successful'**
  String get feePaySuccess;

  /// No description provided for @feePaymentHistory.
  ///
  /// In en, this message translates to:
  /// **'Payment history'**
  String get feePaymentHistory;

  /// No description provided for @feePaymentModeLabel.
  ///
  /// In en, this message translates to:
  /// **'Payment mode'**
  String get feePaymentModeLabel;

  /// No description provided for @feeRecordPayment.
  ///
  /// In en, this message translates to:
  /// **'Record payment'**
  String get feeRecordPayment;

  /// No description provided for @feeRefund.
  ///
  /// In en, this message translates to:
  /// **'Refund'**
  String get feeRefund;

  /// No description provided for @feeStatusOverdue.
  ///
  /// In en, this message translates to:
  /// **'Overdue'**
  String get feeStatusOverdue;

  /// No description provided for @feeStatusPaid.
  ///
  /// In en, this message translates to:
  /// **'Paid'**
  String get feeStatusPaid;

  /// No description provided for @feeStatusPartial.
  ///
  /// In en, this message translates to:
  /// **'Partially paid'**
  String get feeStatusPartial;

  /// No description provided for @feeStatusPending.
  ///
  /// In en, this message translates to:
  /// **'Pending'**
  String get feeStatusPending;

  /// No description provided for @feeStatusWaived.
  ///
  /// In en, this message translates to:
  /// **'Waived'**
  String get feeStatusWaived;

  /// No description provided for @feeTitleLabel.
  ///
  /// In en, this message translates to:
  /// **'Fee title'**
  String get feeTitleLabel;

  /// No description provided for @feeWalletCoinsNote.
  ///
  /// In en, this message translates to:
  /// **'Fees are paid from your LearnScroll coin wallet.'**
  String get feeWalletCoinsNote;

  /// No description provided for @myFeesEmpty.
  ///
  /// In en, this message translates to:
  /// **'No fees to show'**
  String get myFeesEmpty;

  /// No description provided for @myFeesTitle.
  ///
  /// In en, this message translates to:
  /// **'My fees'**
  String get myFeesTitle;

  /// No description provided for @idCardTitle.
  ///
  /// In en, this message translates to:
  /// **'Digital ID card'**
  String get idCardTitle;

  /// No description provided for @idCardAllIssued.
  ///
  /// In en, this message translates to:
  /// **'Everyone has an ID card'**
  String get idCardAllIssued;

  /// No description provided for @idCardIssueForOthers.
  ///
  /// In en, this message translates to:
  /// **'Issue for others'**
  String get idCardIssueForOthers;

  /// No description provided for @idCardIssueOwn.
  ///
  /// In en, this message translates to:
  /// **'Issue my ID card'**
  String get idCardIssueOwn;

  /// No description provided for @idCardIssuedOn.
  ///
  /// In en, this message translates to:
  /// **'Issued on'**
  String get idCardIssuedOn;

  /// No description provided for @idCardNotIssued.
  ///
  /// In en, this message translates to:
  /// **'No ID card issued yet'**
  String get idCardNotIssued;

  /// No description provided for @idCardValidUntil.
  ///
  /// In en, this message translates to:
  /// **'Valid until'**
  String get idCardValidUntil;

  /// No description provided for @parentLinkCampusIdHint.
  ///
  /// In en, this message translates to:
  /// **'Campus ID shared by the school'**
  String get parentLinkCampusIdHint;

  /// No description provided for @parentLinkCampusIdLabel.
  ///
  /// In en, this message translates to:
  /// **'Campus ID'**
  String get parentLinkCampusIdLabel;

  /// No description provided for @parentLinkLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load linked children'**
  String get parentLinkLoadFailed;

  /// No description provided for @parentLinkMyChildrenTitle.
  ///
  /// In en, this message translates to:
  /// **'My children'**
  String get parentLinkMyChildrenTitle;

  /// No description provided for @parentLinkNoChildren.
  ///
  /// In en, this message translates to:
  /// **'No children linked yet'**
  String get parentLinkNoChildren;

  /// No description provided for @parentLinkScreenTitle.
  ///
  /// In en, this message translates to:
  /// **'Link a child'**
  String get parentLinkScreenTitle;

  /// No description provided for @parentLinkSubmit.
  ///
  /// In en, this message translates to:
  /// **'Link'**
  String get parentLinkSubmit;

  /// No description provided for @parentLinkSuccess.
  ///
  /// In en, this message translates to:
  /// **'Child linked successfully'**
  String get parentLinkSuccess;

  /// No description provided for @parentLinkTokenHint.
  ///
  /// In en, this message translates to:
  /// **'Verification code from the school'**
  String get parentLinkTokenHint;

  /// No description provided for @parentLinkTokenLabel.
  ///
  /// In en, this message translates to:
  /// **'Verification code'**
  String get parentLinkTokenLabel;

  /// No description provided for @parentLinkVerifyHint.
  ///
  /// In en, this message translates to:
  /// **'Enter the campus ID and the code you received to link your child.'**
  String get parentLinkVerifyHint;

  /// No description provided for @parentLinkVerifyTitle.
  ///
  /// In en, this message translates to:
  /// **'Verify and link'**
  String get parentLinkVerifyTitle;

  /// No description provided for @parentLinkedOn.
  ///
  /// In en, this message translates to:
  /// **'Linked on {date}'**
  String parentLinkedOn(String date);

  /// No description provided for @parentOverviewNoEnrollment.
  ///
  /// In en, this message translates to:
  /// **'This child isn\'t currently enrolled in an active section at this campus.'**
  String get parentOverviewNoEnrollment;

  /// No description provided for @parentOverviewReportCard.
  ///
  /// In en, this message translates to:
  /// **'Report card'**
  String get parentOverviewReportCard;

  /// No description provided for @parentOverviewNoNotices.
  ///
  /// In en, this message translates to:
  /// **'No notices for this class yet.'**
  String get parentOverviewNoNotices;

  /// No description provided for @parentOverviewLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load this child\'s details'**
  String get parentOverviewLoadFailed;

  /// No description provided for @reportCardPercentage.
  ///
  /// In en, this message translates to:
  /// **'Percentage'**
  String get reportCardPercentage;

  /// No description provided for @reportCardTitle.
  ///
  /// In en, this message translates to:
  /// **'Report card'**
  String get reportCardTitle;

  /// No description provided for @reportCardView.
  ///
  /// In en, this message translates to:
  /// **'Report cards'**
  String get reportCardView;

  /// No description provided for @resultMarksInvalid.
  ///
  /// In en, this message translates to:
  /// **'Enter valid marks'**
  String get resultMarksInvalid;

  /// No description provided for @resultMaxLabel.
  ///
  /// In en, this message translates to:
  /// **'Max'**
  String get resultMaxLabel;

  /// No description provided for @resultObtainedLabel.
  ///
  /// In en, this message translates to:
  /// **'Obtained'**
  String get resultObtainedLabel;

  /// No description provided for @resultsManage.
  ///
  /// In en, this message translates to:
  /// **'Enter results'**
  String get resultsManage;

  /// No description provided for @resultsTitle.
  ///
  /// In en, this message translates to:
  /// **'Results'**
  String get resultsTitle;

  /// No description provided for @syllabusAdd.
  ///
  /// In en, this message translates to:
  /// **'Add unit'**
  String get syllabusAdd;

  /// No description provided for @syllabusCoveredOn.
  ///
  /// In en, this message translates to:
  /// **'Covered on {date}'**
  String syllabusCoveredOn(String date);

  /// No description provided for @syllabusEmpty.
  ///
  /// In en, this message translates to:
  /// **'No syllabus units yet'**
  String get syllabusEmpty;

  /// No description provided for @syllabusMarkCovered.
  ///
  /// In en, this message translates to:
  /// **'Mark as covered'**
  String get syllabusMarkCovered;

  /// No description provided for @syllabusTitle.
  ///
  /// In en, this message translates to:
  /// **'Syllabus'**
  String get syllabusTitle;

  /// No description provided for @syllabusUnitOrderLabel.
  ///
  /// In en, this message translates to:
  /// **'Order'**
  String get syllabusUnitOrderLabel;

  /// No description provided for @syllabusUnitTitleLabel.
  ///
  /// In en, this message translates to:
  /// **'Unit title'**
  String get syllabusUnitTitleLabel;

  /// No description provided for @noUsersHere.
  ///
  /// In en, this message translates to:
  /// **'No one here yet'**
  String get noUsersHere;

  /// No description provided for @usersLoadFailed.
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load the list'**
  String get usersLoadFailed;

  /// No description provided for @mutualFriendsCount.
  ///
  /// In en, this message translates to:
  /// **'{count} mutual'**
  String mutualFriendsCount(int count);
}

class _AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const _AppLocalizationsDelegate();

  @override
  Future<AppLocalizations> load(Locale locale) {
    return SynchronousFuture<AppLocalizations>(lookupAppLocalizations(locale));
  }

  @override
  bool isSupported(Locale locale) =>
      <String>['en', 'hi'].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

AppLocalizations lookupAppLocalizations(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'en':
      return AppLocalizationsEn();
    case 'hi':
      return AppLocalizationsHi();
  }

  throw FlutterError(
    'AppLocalizations.delegate failed to load unsupported locale "$locale". This is likely '
    'an issue with the localizations generation tool. Please file an issue '
    'on GitHub with a reproducible sample app and the gen-l10n configuration '
    'that was used.',
  );
}
