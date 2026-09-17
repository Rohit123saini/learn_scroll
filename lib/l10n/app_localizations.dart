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

  /// No description provided for @like.
  ///
  /// In en, this message translates to:
  /// **'Like'**
  String get like;

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

  /// Shown when opening a downloaded file fails
  ///
  /// In en, this message translates to:
  /// **'Open failed: {error}'**
  String openFailed(String error);

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
