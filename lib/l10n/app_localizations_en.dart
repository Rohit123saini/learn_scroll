// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get appTitle => 'LearnScroll';

  @override
  String get inviteEarn => 'Invite & Earn';

  @override
  String get inviteEarnSub => 'Invite friends and earn rewards';

  @override
  String inviteEarnSubCoins(int coins) {
    return 'Invite friends and earn $coins coins';
  }

  @override
  String get inviteCta => 'Invite';

  @override
  String get searchHint => 'Search courses, tests, notices...';

  @override
  String get liveNow => 'Live Now';

  @override
  String get liveBadge => 'LIVE';

  @override
  String liveNowCardSubtitle(String teacher, int viewers) {
    return '$teacher · $viewers watching';
  }

  @override
  String get seeAll => 'See all';

  @override
  String get join => 'Join';

  @override
  String get yourFeed => 'Your Feed';

  @override
  String get following => 'Following';

  @override
  String get addFriends => 'Add Friends';

  @override
  String get startClass => 'Start Class';

  @override
  String get startTestSeries => 'Start Test Series';

  @override
  String get assignments => 'Assignments';

  @override
  String get testSeries => 'Test Series';

  @override
  String get notices => 'Notices';

  @override
  String get wallet => 'Wallet';

  @override
  String get yourClassrooms => 'Your Classrooms';

  @override
  String get joinClassroom => '+ Join';

  @override
  String get jumpBackIn => 'Jump Back In';

  @override
  String get themeToggleTooltip => 'Toggle dark mode';

  @override
  String get languageToggleTooltip => 'Change language';

  @override
  String get notificationsTooltip => 'Notifications';

  @override
  String get newPostTooltip => 'Create new post';

  @override
  String get navHome => 'Home';

  @override
  String get navDiscover => 'Discover';

  @override
  String get navCreate => 'Create';

  @override
  String get navNotifications => 'Alerts';

  @override
  String get navProfile => 'Profile';

  @override
  String get save => 'Save';

  @override
  String get saveFailed => 'Failed to update bookmark state';

  @override
  String get comment => 'Comment';

  @override
  String get share => 'Share';

  @override
  String commentsCount(int count) {
    return '$count comments';
  }

  @override
  String get sessionExpiredRedirecting =>
      'Session expired. Redirecting to login...';

  @override
  String get feedErrorTitle => 'Couldn\'t load feed';

  @override
  String get feedErrorSubtitle =>
      'Check your internet connection and try again.';

  @override
  String get retry => 'Retry';

  @override
  String get feedEmptyTitle => 'No posts yet';

  @override
  String get feedEmptySubtitle =>
      'Follow creators or search subjects to fill your feed.';

  @override
  String featureComingSoon(String feature) {
    return '$feature is coming soon!';
  }

  @override
  String get authOr => 'OR';

  @override
  String get authGoogleCredentialsFailed =>
      'Could not get Google credentials. Please try again.';

  @override
  String get authGoogleSignedIn => 'Signed in with Google';

  @override
  String get authGoogleSignInFailed =>
      'Google sign-in failed. Please try again.';

  @override
  String get loginSuccessful => 'Login Successful!';

  @override
  String get loginWelcomeBack => 'Welcome Back';

  @override
  String get loginSubtitle => 'Sign in to continue your journey';

  @override
  String get loginUsernameOrEmail => 'Username or Email';

  @override
  String get loginUsernameRequired => 'Username or Email is required';

  @override
  String get loginPassword => 'Password';

  @override
  String get loginPasswordRequired => 'Password is required';

  @override
  String get loginForgotPassword => 'Forgot Password?';

  @override
  String get loginSignIn => 'Sign In';

  @override
  String get loginContinueWithGoogle => 'Continue with Google';

  @override
  String get loginNoAccount => 'Don\'t have an account? ';

  @override
  String get loginSignUp => 'Sign Up';

  @override
  String get signupSuccessful => 'Signup Successful!';

  @override
  String get signupUsernameExists => 'User already exists with this username';

  @override
  String get signupEmailExists => 'Account already exists with this email';

  @override
  String get signupGoogleSuccessful => 'Account created with Google';

  @override
  String get signupVerifyEmail => 'Verify Email Address';

  @override
  String get signupOtpSentTo => 'We have sent a verification code to';

  @override
  String get signupOtpInvalid => 'Please enter a valid OTP';

  @override
  String get signupVerifyAndCreate => 'Verify & Create Account';

  @override
  String get signupCreateAccount => 'Create Account';

  @override
  String get signupSubtitle => 'Sign up to get started with LearnScroll';

  @override
  String get signupWithGoogle => 'Sign up with Google';

  @override
  String get signupOrEmail => 'OR SIGN UP WITH EMAIL';

  @override
  String get signupUsername => 'Username';

  @override
  String get signupUsernameRequired => 'Username is required';

  @override
  String get signupEmail => 'Email Address';

  @override
  String get signupEmailRequired => 'Email is required';

  @override
  String get signupEmailInvalid => 'Enter a valid email address';

  @override
  String get signupContact => 'Contact Number';

  @override
  String get signupContactRequired => 'Contact number is required';

  @override
  String get signupContactInvalid => 'Enter a valid mobile number';

  @override
  String get signupFirstName => 'First Name';

  @override
  String get signupLastName => 'Last Name';

  @override
  String get signupFieldRequired => 'Required';

  @override
  String get signupPassword => 'Password';

  @override
  String get signupPasswordRequired => 'Password is required';

  @override
  String get signupPasswordMinLength =>
      'Password must be at least 8 characters';

  @override
  String get signupConfirmPassword => 'Confirm Password';

  @override
  String get signupConfirmPasswordRequired => 'Confirm password is required';

  @override
  String get signupPasswordsNoMatch => 'Passwords do not match';

  @override
  String get signupButton => 'Sign Up';

  @override
  String get forgotOtpSentSuccess => 'OTP sent successfully!';

  @override
  String get forgotOtpRequired => 'Please enter the OTP';

  @override
  String get forgotPasswordUpdated => 'Password updated successfully!';

  @override
  String get forgotTitleStep1 => 'Forgot Password?';

  @override
  String get forgotTitleStep2 => 'Verify OTP';

  @override
  String get forgotTitleStep3 => 'Reset Password';

  @override
  String get forgotSubtitleStep1 =>
      'Enter your registered email or phone number to receive a verification OTP.';

  @override
  String get forgotSubtitleStep2 =>
      'Enter the 6-digit verification code sent to your registered contact.';

  @override
  String get forgotSubtitleStep3 =>
      'Enter and confirm your new password to complete the reset.';

  @override
  String get forgotIdentityLabel => 'Email or Phone Number';

  @override
  String get forgotFieldRequired => 'This field is required';

  @override
  String get forgotOtpLabel => 'Enter OTP Code';

  @override
  String get forgotNewPassword => 'New Password';

  @override
  String get forgotNewPasswordRequired => 'New password is required';

  @override
  String get forgotConfirmNewPassword => 'Confirm New Password';

  @override
  String get forgotConfirmPasswordRequired => 'Confirm password is required';

  @override
  String get forgotPasswordsNoMatch => 'Passwords do not match';

  @override
  String get forgotSendOtp => 'Send OTP';

  @override
  String get forgotVerifyOtp => 'Verify OTP';

  @override
  String get forgotUpdatePassword => 'Update Password';

  @override
  String get completeProfileTitle => 'One Last Step';

  @override
  String get completeProfileSubtitle =>
      'Please add your phone number to finish setting up your account';

  @override
  String get completeProfilePhone => 'Phone Number';

  @override
  String get completeProfilePhoneRequired => 'Phone number is required';

  @override
  String get completeProfilePhoneInvalid => 'Enter a valid mobile number';

  @override
  String get completeProfileContinue => 'Continue';

  @override
  String get cancel => 'Cancel';

  @override
  String get somethingWentWrong => 'Something went wrong';

  @override
  String get testSubmit => 'Submit';

  @override
  String get testSubmitFailed => 'Failed to submit test';

  @override
  String get testExitTitle => 'Exit Test?';

  @override
  String get testExitBody => 'Your progress will be lost if you exit now.';

  @override
  String get testExitConfirm => 'Exit';

  @override
  String get testPaletteTitle => 'Question Palette';

  @override
  String testAnsweredOf(int answered, int total) {
    return '$answered/$total answered';
  }

  @override
  String get testSeriesErrorTitle => 'Couldn\'t load test';

  @override
  String get testNoQuestions => 'No questions found';

  @override
  String questionOf(int number, int total) {
    return 'Question $number of $total';
  }

  @override
  String questionShort(int number) {
    return 'Q$number';
  }

  @override
  String marksShort(num marks) {
    return '$marks marks';
  }

  @override
  String get answerHintSelectOne => 'Select one option';

  @override
  String get answerHintSelectMultiple => 'Select all that apply';

  @override
  String get answerHintMatch => 'Match the following';

  @override
  String get answerHintArrange => 'Arrange in the correct order';

  @override
  String get answerHintText => 'Type your answer';

  @override
  String get testTypeAnswerHint => 'Type your answer here...';

  @override
  String get testAttachPhoto => 'Attach Photo';

  @override
  String get testMatchSelect => 'Select a match';

  @override
  String get testPrevious => 'Previous';

  @override
  String get testNext => 'Next';

  @override
  String get testRateTitle => 'Rate this Test Series';

  @override
  String get testRateSubtitle => 'Let us know how it was';

  @override
  String get testReviewHint => 'Write a review (optional)';

  @override
  String get testSubmitReview => 'Submit Review';

  @override
  String get testReviewThanks => 'Thanks for your feedback!';

  @override
  String get testAskQueryTitle => 'Have a Question?';

  @override
  String get testAskQuerySubtitle => 'Ask about this test series';

  @override
  String get testQueryHint => 'Type your question here...';

  @override
  String get testQueryAnonymous => 'Ask anonymously';

  @override
  String get testSendQuery => 'Send';

  @override
  String get testQueryEmpty => 'Please enter your question';

  @override
  String get testQuerySent => 'Your question has been sent';

  @override
  String get testResultTitle => 'Test Result';

  @override
  String get testFinalScore => 'Final Score';

  @override
  String get testAutoScore => 'Auto Score';

  @override
  String get testTotalMarks => 'Total Marks';

  @override
  String get testPercentage => 'Percentage';

  @override
  String get testAwaitingCheckNote =>
      'Some answers are awaiting manual checking';

  @override
  String get testSubmittedOn => 'Submitted On';

  @override
  String get testCheckedOn => 'Checked On';

  @override
  String get testAttemptNumber => 'Attempt Number';

  @override
  String get testRateSeries => 'Rate this Series';

  @override
  String get testAskQuery => 'Ask a Question';

  @override
  String get testBreakdown => 'Question Breakdown';

  @override
  String get testAwaitingReview => 'Awaiting Review';

  @override
  String get answerCorrect => 'Correct';

  @override
  String get answerIncorrect => 'Incorrect';

  @override
  String get assignmentReviewed => 'Reviewed';

  @override
  String assignmentMarksOf(num awarded, num max) {
    return '$awarded/$max marks';
  }

  @override
  String get testYourAnswer => 'Your Answer';

  @override
  String get answerNotAnswered => 'Not answered';

  @override
  String get homeTab => 'Home';

  @override
  String get campusTab => 'Campus';

  @override
  String get classesTab => 'Classes';

  @override
  String get chatTab => 'Chat';

  @override
  String get profileTab => 'Profile';

  @override
  String get confirm => 'Confirm';

  @override
  String get assignmentsTabPending => 'Pending';

  @override
  String get assignmentsTabSubmitted => 'Submitted';

  @override
  String get assignmentsTabChecked => 'Checked';

  @override
  String get assignmentsErrorTitle => 'Couldn\'t load assignments';

  @override
  String get assignmentsEmptyTitle => 'No assignments yet';

  @override
  String get assignmentsEmptySubtitle =>
      'Assignments from your classrooms will show up here.';

  @override
  String assignmentPostedBy(String postedBy) {
    return 'Posted by $postedBy';
  }

  @override
  String assignmentGradeValue(String grade) {
    return 'Grade: $grade';
  }

  @override
  String assignmentTotalMarks(num totalMarks) {
    return 'Total Marks: $totalMarks';
  }

  @override
  String get assignmentNoDueDate => 'No due date';

  @override
  String assignmentDueOn(String date) {
    return 'Due on $date';
  }

  @override
  String get assignmentOverdue => 'Overdue';

  @override
  String get assignmentDueToday => 'Due today';

  @override
  String get assignmentDueTomorrow => 'Due tomorrow';

  @override
  String assignmentDaysLeft(int days) {
    return '$days days left';
  }

  @override
  String get assignmentStatusChecked => 'Checked';

  @override
  String get assignmentStatusPartiallyChecked => 'Partially Checked';

  @override
  String get assignmentStatusSubmitted => 'Submitted';

  @override
  String get assignmentStatusLate => 'Late';

  @override
  String get assignmentStatusMissing => 'Missing';

  @override
  String get assignmentNothingToSubmit => 'There\'s nothing to submit';

  @override
  String get assignmentSubmitConfirmTitle => 'Submit Assignment?';

  @override
  String assignmentSubmitConfirmUnanswered(int unanswered) {
    return '$unanswered questions are unanswered. Submit anyway?';
  }

  @override
  String get assignmentSubmitConfirmBody =>
      'Are you sure you want to submit this assignment?';

  @override
  String get assignmentSubmitSuccess => 'Assignment submitted successfully';

  @override
  String get assignmentSubmitFailed => 'Failed to submit assignment';

  @override
  String get assignmentDueLabel => 'Due';

  @override
  String get assignmentTotalMarksLabel => 'Total Marks';

  @override
  String get assignmentQuestionsLabel => 'Questions';

  @override
  String get assignmentOpenAttachment => 'Open Attachment';

  @override
  String get assignmentLateNotice => 'This assignment was submitted late';

  @override
  String get assignmentYourAnswer => 'Your Answer';

  @override
  String get assignmentAnswerHint => 'Type your answer here...';

  @override
  String get assignmentNoQuestions => 'No questions found';

  @override
  String get assignmentAttachFile => 'Attach File';

  @override
  String get assignmentRemoveFile => 'Remove File';

  @override
  String assignmentAnsweredOf(int answered, int total) {
    return '$answered/$total answered';
  }

  @override
  String get assignmentSubmit => 'Submit';

  @override
  String get assignmentResult => 'Result';

  @override
  String get assignmentMarksAwarded => 'Marks Awarded';

  @override
  String get assignmentGrade => 'Grade';

  @override
  String get assignmentSubmittedOn => 'Submitted On';

  @override
  String get assignmentCheckedOn => 'Checked On';

  @override
  String get assignmentPartiallyCheckedNote =>
      'Some answers are awaiting manual checking';

  @override
  String get assignmentTeacherFeedback => 'Teacher\'s Feedback';

  @override
  String get assignmentNoWrittenAnswer => 'No written answer provided';

  @override
  String get assignmentOpenSubmittedFile => 'Open Submitted File';

  @override
  String get assignmentAwaitingReview => 'Awaiting Review';

  @override
  String get assignmentAttachPhoto => 'Attach Photo';

  @override
  String get assignmentPickFromCamera => 'Take a photo';

  @override
  String get assignmentPickFromGallery => 'Choose from gallery';

  @override
  String get assignmentPickFailed =>
      'Couldn\'t pick that file. Please try again.';

  @override
  String get assignmentFileNotFound => 'This file is no longer available.';

  @override
  String get assignmentNoInternet =>
      'Check your internet connection and try again.';

  @override
  String get assignmentCreateTitle => 'Create Assignment';

  @override
  String get assignmentTitleLabel => 'Title';

  @override
  String get assignmentDescriptionLabel => 'Description';

  @override
  String get assignmentDescriptionHint =>
      'What should students do? Add instructions here…';

  @override
  String get assignmentPickDueDate => 'Pick a due date';

  @override
  String get assignmentClearDueDate => 'Clear';

  @override
  String get assignmentStructuredToggleLabel => 'Structured questions';

  @override
  String get assignmentStructuredToggleHint =>
      'Turn this on for auto-graded multiple-choice or ordered-list questions instead of one written answer.';

  @override
  String get assignmentTotalMarksHint =>
      'Optional — leave blank for no fixed scale';

  @override
  String get assignmentAddQuestion => 'Add question';

  @override
  String get assignmentQuestionTypeLabel => 'Question type';

  @override
  String get assignmentQuestionTypeText => 'Written answer';

  @override
  String get assignmentQuestionTypeMcq => 'Multiple choice (one answer)';

  @override
  String get assignmentQuestionTypeMsq => 'Multiple choice (multiple answers)';

  @override
  String get assignmentQuestionTypeList => 'Ordered list';

  @override
  String get assignmentQuestionTextHint => 'Type your question…';

  @override
  String get assignmentQuestionMarksLabel => 'Marks';

  @override
  String get assignmentAddOption => 'Add option';

  @override
  String get assignmentOptionHint => 'Option text';

  @override
  String get assignmentRemoveOption => 'Remove option';

  @override
  String get assignmentRemoveQuestion => 'Remove question';

  @override
  String get assignmentCreateValidationTitle => 'Please enter a title';

  @override
  String get assignmentCreateValidationQuestion =>
      'Every question needs question text, marks, and (if applicable) a marked correct answer';

  @override
  String get assignmentCreateValidationOptions =>
      'Add at least 2 options to this question';

  @override
  String get assignmentCreateValidationCorrect =>
      'Mark at least one correct option for this question';

  @override
  String get assignmentCreateSuccess => 'Assignment created';

  @override
  String get assignmentCreateFailed =>
      'Couldn\'t create the assignment. Please try again.';

  @override
  String get assignmentGradeSectionTitle => 'Grade this submission';

  @override
  String get assignmentFeedbackHint => 'Feedback (optional)';

  @override
  String get assignmentGradeSaved => 'Grade saved';

  @override
  String get assignmentGradeFailed =>
      'Couldn\'t save the grade. Please try again.';

  @override
  String get assignmentReviewAnswerTitle => 'Review this answer';

  @override
  String get assignmentReviewSaved => 'Review saved';

  @override
  String get assignmentReviewFailed =>
      'Couldn\'t save the review. Please try again.';

  @override
  String get assignmentNotShared => 'Not shared — only you can see this result';

  @override
  String get assignmentSharePublicNote =>
      'Anyone with the link can view this result';

  @override
  String get assignmentShareResult => 'Share';

  @override
  String get assignmentUnshareResult => 'Stop sharing';

  @override
  String get assignmentLinkCopied =>
      'Link copied — anyone with it can view your result';

  @override
  String get assignmentPublishFailed =>
      'Couldn\'t share this result. Please try again.';

  @override
  String get assignmentUnpublishFailed =>
      'Couldn\'t stop sharing this result. Please try again.';

  @override
  String get testSeriesTabAll => 'All';

  @override
  String get testSeriesTabFree => 'Free';

  @override
  String get testSeriesTabPaid => 'Paid';

  @override
  String get testSeriesEmptyTitle => 'No test series yet';

  @override
  String get testSeriesEmptySubtitle =>
      'Test series from your classrooms will show up here.';

  @override
  String testSeriesCoins(int coins) {
    return '$coins coins';
  }

  @override
  String get testSeriesFree => 'Free';

  @override
  String testSeriesBy(String creator) {
    return 'By $creator';
  }

  @override
  String testSeriesMarks(num marks) {
    return '$marks marks';
  }

  @override
  String testSeriesDuration(int minutes) {
    return '$minutes min';
  }

  @override
  String testSeriesAttempts(int attempts) {
    return '$attempts attempts';
  }

  @override
  String get testSeriesStart => 'Start';

  @override
  String get testSeriesResume => 'Resume';

  @override
  String get testSeriesViewResult => 'View Result';

  @override
  String get testSeriesNoRatings => 'No ratings yet';

  @override
  String testSeriesRating(String rating, int reviewCount) {
    return '$rating ($reviewCount reviews)';
  }

  @override
  String get testStatusChecked => 'Checked';

  @override
  String get testStatusPartiallyChecked => 'Partially Checked';

  @override
  String get testStatusSubmitted => 'Submitted';

  @override
  String get testStatusInProgress => 'In Progress';

  @override
  String get testSeriesPayTitle => 'Unlock Test Series';

  @override
  String testSeriesPayBody(int coins) {
    return 'This test series costs $coins coins. Continue?';
  }

  @override
  String get testSeriesNotEnoughCoins =>
      'You don\'t have enough coins for this test series';

  @override
  String get testSeriesQuestionsLabel => 'Questions';

  @override
  String get testSeriesDurationLabel => 'Duration';

  @override
  String get testSeriesNoTimeLimit => 'No time limit';

  @override
  String get testSeriesAttemptsLabel => 'Attempts';

  @override
  String get testSeriesYourAttempt => 'Your Attempt';

  @override
  String testSeriesTimerNotice(int minutes) {
    return 'This test has a time limit of $minutes minutes';
  }

  @override
  String get testSeriesReviews => 'Reviews';

  @override
  String get testSeriesNoReviews => 'No reviews yet';

  @override
  String get testTimeUp => 'Time\'s up!';

  @override
  String get testSubmitConfirmTitle => 'Submit Test?';

  @override
  String testSubmitConfirmUnanswered(int unanswered) {
    return '$unanswered questions are unanswered. Submit anyway?';
  }

  @override
  String get testSubmitConfirmBody =>
      'Are you sure you want to submit this test?';

  @override
  String get viewAll => 'View all';

  @override
  String get settingsTitle => 'Settings';

  @override
  String get settingsLanguageSection => 'Language';

  @override
  String get settingsThemeSection => 'Appearance';

  @override
  String get settingsThemeLight => 'Light';

  @override
  String get settingsThemeDark => 'Dark';

  @override
  String get settingsThemeSystem => 'Match device';

  @override
  String get settingsAccountSection => 'Account';

  @override
  String get settingsLogout => 'Log out';

  @override
  String get settingsLogoutConfirm =>
      'You\'ll need to sign in again to use the app.';

  @override
  String get roleAdmin => 'Campus admin';

  @override
  String get rolePrincipalHod => 'Principal / HOD';

  @override
  String get roleClassTeacher => 'Class teacher';

  @override
  String get roleSubjectTeacher => 'Subject teacher';

  @override
  String get roleNonTeaching => 'Office staff';

  @override
  String get roleStudent => 'Student';

  @override
  String get roleParent => 'Parent';

  @override
  String get roleManagement => 'Management';

  @override
  String get roleNone => 'Member';

  @override
  String get campusSwitchTooltip => 'Switch campus';

  @override
  String get campusLoadFailed => 'Couldn\'t load your campus';

  @override
  String get campusNoneTitle => 'You\'re not part of a campus yet';

  @override
  String get campusNoneSubtitle =>
      'Ask your college or school to add you, or create a campus of your own.';

  @override
  String get campusPendingTitle => 'Waiting for approval';

  @override
  String get campusPendingBody =>
      'This campus is under review. Once it\'s approved you can add staff and students.';

  @override
  String get campusRejectedTitle => 'Campus not approved';

  @override
  String get campusRejectedBody =>
      'This campus was not approved. Contact support if you think this is a mistake.';

  @override
  String get campusMyClassTitle => 'My class';

  @override
  String get campusMySectionsTitle => 'My sections';

  @override
  String get campusAllSectionsTitle => 'All sections';

  @override
  String get campusManagementTitle => 'Campus overview';

  @override
  String get campusNoSectionsAssigned =>
      'No sections assigned to you yet. Your campus admin sets this up.';

  @override
  String get campusSectionsCount => 'Sections';

  @override
  String get campusSubjectsCount => 'Subjects';

  @override
  String get campusAttendanceLabel => 'Attendance';

  @override
  String get campusTapToView => 'Tap to view';

  @override
  String get campusMarkAttendance => 'Mark attendance';

  @override
  String get campusStudents => 'Students';

  @override
  String get campusTimetableTitle => 'Timetable';

  @override
  String get campusTimetableLoadFailed => 'Couldn\'t load the timetable';

  @override
  String get campusNoClassesToday => 'No periods scheduled for this day';

  @override
  String get campusUnknownSubject => 'Subject';

  @override
  String get campusSearchStudents => 'Search by name or roll number';

  @override
  String get campusNoMatches => 'No students match that search';

  @override
  String campusStudentCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count students',
      one: '1 student',
    );
    return '$_temp0';
  }

  @override
  String get campusRosterLoadFailed => 'Couldn\'t load the student list';

  @override
  String get campusRosterEmptyTitle => 'No students in this section';

  @override
  String get campusRosterEmptySubtitle =>
      'Students appear here once they\'re enrolled for the current session.';

  @override
  String get campusNoAttendancePermissionTitle =>
      'You can\'t mark this attendance';

  @override
  String get campusNoAttendancePermissionBody =>
      'Only the class teacher or the assigned subject teacher can mark attendance for this section.';

  @override
  String get campusNoticesTitle => 'Notices';

  @override
  String get campusNoticesLoadFailed => 'Couldn\'t load notices';

  @override
  String get campusNoNotices => 'No notices yet';

  @override
  String get campusPostNotice => 'Post a notice';

  @override
  String get noticeAudienceLabel => 'Who sees this';

  @override
  String get noticeTitleLabel => 'Title';

  @override
  String get noticeBodyLabel => 'Message';

  @override
  String get noticePinLabel => 'Pin to the top';

  @override
  String get noticePinHint => 'Stays pinned for 7 days';

  @override
  String get noticeSendLabel => 'Post notice';

  @override
  String get noticeEmptyError => 'Add a title and a message before posting.';

  @override
  String get noticeNoSessionError =>
      'No active academic session for this campus.';

  @override
  String get noticeSelectDepartmentError =>
      'Pick which department this notice is for.';

  @override
  String get noticeScopeWholeCampus => 'Whole campus';

  @override
  String get noticeScopeCampus => 'Campus';

  @override
  String get noticeScopeClass => 'Class';

  @override
  String get noticeScopeDepartment => 'Department';

  @override
  String get noticeScopeSection => 'Section';

  @override
  String get attendanceWholeDay => 'Whole day';

  @override
  String get attendanceSubjectLabel => 'Subject';

  @override
  String get attendancePresent => 'Present';

  @override
  String get attendanceAbsent => 'Absent';

  @override
  String get attendanceTotal => 'Total';

  @override
  String get attendanceOverall => 'Overall attendance';

  @override
  String get attendanceBySubject => 'By subject';

  @override
  String get attendanceNoSubjectData => 'No subject-wise records yet';

  @override
  String get attendanceSummaryFailed => 'Couldn\'t load attendance';

  @override
  String get attendanceAlreadyMarked =>
      'Attendance for this day is already marked. Pick another date to make changes.';

  @override
  String attendanceSaveLabel(int count) {
    return 'Save for $count students';
  }

  @override
  String attendanceSaving(int done, int total) {
    return 'Saving $done of $total';
  }

  @override
  String attendanceSavedAll(int count) {
    return 'Attendance saved for $count students';
  }

  @override
  String attendanceSavedPartial(int saved, int failed) {
    return 'Saved $saved, $failed didn\'t go through';
  }

  @override
  String attendanceFailedCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count students didn\'t save',
      one: '1 student didn\'t save',
    );
    return '$_temp0';
  }

  @override
  String attendanceBelowThreshold(int percent) {
    return 'Attendance is below the $percent% your campus expects.';
  }

  @override
  String timeMinutesAgo(int count) {
    return '${count}m ago';
  }

  @override
  String timeHoursAgo(int count) {
    return '${count}h ago';
  }

  @override
  String timeDaysAgo(int count) {
    return '${count}d ago';
  }

  @override
  String get searchFilterAll => 'All';

  @override
  String get searchFilterPeople => 'People';

  @override
  String get searchFilterNotices => 'Notices';

  @override
  String get searchFilterAssignments => 'Assignments';

  @override
  String get searchFilterTests => 'Test Series';

  @override
  String get searchFilterMessages => 'Messages';

  @override
  String get searchSectionPeople => 'People';

  @override
  String get searchSectionNotices => 'Campus Notices';

  @override
  String get searchSectionAssignments => 'Assignments';

  @override
  String get searchSectionTests => 'Test Series';

  @override
  String get searchSectionMessages => 'Messages';

  @override
  String get searchRecentTitle => 'Recent';

  @override
  String get searchRecentClear => 'Clear all';

  @override
  String get searchEmptyPrompt =>
      'Search for people, classes, notices, assignments and tests';

  @override
  String searchNoResultsFor(String query) {
    return 'No results for \"$query\"';
  }

  @override
  String get searchDetailUnavailable =>
      'Full detail view for this result isn\'t wired up yet';

  @override
  String get couldNotOpenClassroom => 'Could not open this classroom.';

  @override
  String get couldNotOpenClass => 'Could not open this class.';

  @override
  String get camera => 'Camera';

  @override
  String get recordVideo => 'Record video';

  @override
  String get chooseFromGallery => 'Choose from gallery';

  @override
  String get yourStory => 'Your Story';

  @override
  String get couldNotUploadStory => 'Could not upload your story.';

  @override
  String get noClassroomsWithReferrals =>
      'None of your classrooms have referrals turned on yet.';

  @override
  String get couldNotCreateReferralLink =>
      'Could not create your referral link.';

  @override
  String get open => 'Open';

  @override
  String inviteEarnedSoFar(int earned, int pending) {
    return 'You\'ve earned ₹$earned so far — ₹$pending on the way';
  }

  @override
  String get inviteShareClassroomLink =>
      'Share a classroom\'s link — earn a daily % commission for as long as they stay enrolled';

  @override
  String referralCommissionEarned(String percent, String name) {
    return 'You\'ll earn $percent% of the daily fee for every student you refer to \"$name\".';
  }

  @override
  String openFailed(String error) {
    return 'Open failed: $error';
  }

  @override
  String failedWithError(String error) {
    return 'Failed: $error';
  }

  @override
  String filesSelectedCount(int count) {
    return '$count selected';
  }

  @override
  String get noCommentsYet => 'No comments yet';

  @override
  String get beFirstToComment => 'Be the first to share what you think';

  @override
  String get addCommentHint => 'Add a comment…';

  @override
  String replyingTo(String name) {
    return 'Replying to @$name';
  }

  @override
  String get like => 'Like';

  @override
  String get reply => 'Reply';

  @override
  String viewReplies(int count) {
    return '$count replies';
  }

  @override
  String get hideReplies => 'Hide replies';

  @override
  String get loadingReplies => 'Loading…';

  @override
  String get edited => 'edited';

  @override
  String get you => 'you';

  @override
  String get editComment => 'Edit comment';

  @override
  String get commentEdited => 'Comment updated';

  @override
  String editFailed(String error) {
    return 'Couldn\'t edit: $error';
  }

  @override
  String get deleteComment => 'Delete comment?';

  @override
  String get deleteCommentBody => 'This can\'t be undone.';

  @override
  String get delete => 'Delete';

  @override
  String deleteFailed(String error) {
    return 'Couldn\'t delete: $error';
  }

  @override
  String get hideComment => 'Hide comment';

  @override
  String get commentHidden => 'Comment hidden';

  @override
  String hideFailed(String error) {
    return 'Couldn\'t hide: $error';
  }

  @override
  String get attachCameraPhoto => 'Camera photo';

  @override
  String get attachCameraVideo => 'Camera video';

  @override
  String get attachGalleryPhoto => 'Gallery photo';

  @override
  String get attachGalleryVideo => 'Gallery video';

  @override
  String get attachDocument => 'Document';

  @override
  String get fileTooLargeTitle => 'File too large';

  @override
  String fileTooLargeBody(String size) {
    return 'This file is ${size}MB — the limit is 200MB. Please choose a smaller file.';
  }

  @override
  String uploadingPercent(String percent) {
    return 'Uploading $percent%';
  }

  @override
  String get compressing => 'Compressing…';

  @override
  String openFileFailed(String error) {
    return 'Couldn\'t open file: $error';
  }

  @override
  String get ok => 'OK';

  @override
  String get addCaptionHint => 'Add a caption…';

  @override
  String get download => 'Download';

  @override
  String downloadedFile(String fileName) {
    return 'Downloaded: $fileName';
  }

  @override
  String downloadFailed(String error) {
    return 'Download failed: $error';
  }

  @override
  String get reactionUpdateFailed =>
      'Couldn\'t update your reaction — check your connection';

  @override
  String get loadingDocument => 'Loading document…';

  @override
  String get previewNotSupported => 'Preview not supported for this file';

  @override
  String get directDownloadOpen => 'Direct download & open';

  @override
  String get openDownloadedFile => 'Open downloaded file';

  @override
  String viewersCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count viewers',
      one: '1 viewer',
    );
    return '$_temp0';
  }

  @override
  String get viewersTitle => 'Viewers';

  @override
  String get couldntLoadViewers => 'Couldn\'t load viewers.';

  @override
  String get noViewsYet => 'No views yet.';

  @override
  String get tsSourceIndividual => 'Individual';

  @override
  String get tsSourceCampus => 'Campus';

  @override
  String get tsSourceLiveClass => 'Live Class';

  @override
  String get tsSearchHint => 'Search test series';

  @override
  String get tsNoSearchResults => 'No test series match your search';

  @override
  String get tsLoadMoreFailed => 'Couldn\'t load more. Tap to retry.';

  @override
  String get tsErrOffline =>
      'No internet connection. Check your network and try again.';

  @override
  String get tsErrTimeout =>
      'The server took too long to respond. Please try again.';

  @override
  String get tsErrUnauthorized =>
      'Your session has expired. Please log in again.';

  @override
  String get tsErrForbidden => 'You don\'t have access to this test.';

  @override
  String get tsErrNotFound => 'This test is no longer available.';

  @override
  String get tsErrRateLimited =>
      'Too many requests. Please wait a moment and try again.';

  @override
  String get tsErrServer =>
      'Something went wrong on our side. Please try again shortly.';

  @override
  String get tsAttemptAgain => 'Attempt Again';

  @override
  String get tsUnlimited => 'Unlimited';

  @override
  String tsAttemptsUsedOf(int used, int allowed) {
    return '$used of $allowed used';
  }

  @override
  String get tsAttemptHistory => 'Your Attempts';

  @override
  String tsAttemptRow(int n) {
    return 'Attempt $n';
  }

  @override
  String get tsSeriesArchived =>
      'This test series is archived and can\'t be started.';

  @override
  String tsViewAllReviews(int count) {
    return 'View All Reviews ($count)';
  }

  @override
  String get tsReviewsTitle => 'Reviews';

  @override
  String get tsPendingSynced => 'Your pending test was submitted.';

  @override
  String get tsQuestionUnsupported =>
      'This question type isn\'t supported in your app version. Please update the app.';

  @override
  String get tsQuestionImage => 'Question image';

  @override
  String get tsMarkForReview => 'Mark for Review';

  @override
  String get tsUnmarkReview => 'Remove Review Mark';

  @override
  String get tsLegendAnswered => 'Answered';

  @override
  String get tsLegendNotAnswered => 'Not answered';

  @override
  String get tsLegendMarked => 'Marked';

  @override
  String get tsOrderNotArranged => 'Not arranged yet — drag to set the order';

  @override
  String tsSubmitConfirmMarked(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count questions are marked for review.',
      one: '1 question is marked for review.',
    );
    return '$_temp0';
  }

  @override
  String tsTimeLeftWarning(int minutes) {
    String _temp0 = intl.Intl.pluralLogic(
      minutes,
      locale: localeName,
      other: '$minutes min left',
      one: '1 min left',
    );
    return '$_temp0';
  }

  @override
  String get tsTimeUpLocked => 'Time\'s up. Submitting your answers…';

  @override
  String get tsSubmitPendingTitle => 'Couldn\'t submit yet';

  @override
  String get tsSubmitPendingBody =>
      'Your answers are saved safely on this device. Check your connection and retry.';

  @override
  String get tsRetrySubmit => 'Retry';

  @override
  String get tsExitBody =>
      'Your answers are saved. You can resume this test later.';

  @override
  String get tsExitBodyTimerRunning =>
      'Your answers are saved, but the timer keeps running. You can resume from the test page.';

  @override
  String get tsDraftRestored => 'Your previous answers were restored.';

  @override
  String tsSemanticsTimer(String time) {
    return 'Time remaining $time';
  }

  @override
  String get tsFromCamera => 'Camera';

  @override
  String get tsFromGallery => 'Gallery';

  @override
  String get tsRemovePhoto => 'Remove Photo';

  @override
  String tsPhotoTooLarge(int mb) {
    return 'Photo is larger than $mb MB. Please choose a smaller one.';
  }

  @override
  String get tsPhotoError =>
      'Couldn\'t open the camera or gallery. Check app permissions in Settings.';

  @override
  String get tsPhotoMissing =>
      'An attached photo is no longer available. Please attach it again.';

  @override
  String get tsCorrectAnswer => 'Correct Answer';

  @override
  String get tsYourPhoto => 'Your Attached Photo';

  @override
  String tsSummaryCorrect(int count) {
    return 'Correct: $count';
  }

  @override
  String tsSummaryIncorrect(int count) {
    return 'Incorrect: $count';
  }

  @override
  String tsSummarySkipped(int count) {
    return 'Skipped: $count';
  }

  @override
  String tsSummaryAwaiting(int count) {
    return 'Awaiting review: $count';
  }

  @override
  String get languageSectionTitle => 'Language';

  @override
  String get themeSectionTitle => 'Theme';

  @override
  String get notificationSettingsTitle => 'Notification settings';

  @override
  String get notificationSettingsLoadFailed =>
      'Couldn\'t load notification settings';

  @override
  String get notifChannelsSectionTitle => 'Channels';

  @override
  String get notifChannelPush => 'Push notifications';

  @override
  String get notifChannelEmail => 'Email';

  @override
  String get notifChannelSms => 'SMS';

  @override
  String get notifChannelWhatsapp => 'WhatsApp';

  @override
  String get notifDigestSectionTitle => 'Email digest';

  @override
  String get notifDigestOff => 'Off';

  @override
  String get notifDigestDaily => 'Daily';

  @override
  String get notifDigestWeekly => 'Weekly';

  @override
  String get notifCategoriesSectionTitle => 'Notify me about';

  @override
  String get notifCategoriesHint =>
      'Turning a category off still shows it in your notification list — it just won\'t send a push, email, SMS or WhatsApp alert.';

  @override
  String get notifCategoryLiveClasses => 'Live classes & sessions';

  @override
  String get notifCategoryAssignmentsTests => 'Assignments & tests';

  @override
  String get notifCategoryMessagesCalls => 'Messages & calls';

  @override
  String get notifCategorySocial => 'Posts & reviews';

  @override
  String get notifCategoryPayments => 'Payments & wallet';

  @override
  String get notifCategoryCampus => 'Campus notices';

  @override
  String get languageEnglish => 'English';

  @override
  String get languageHindi => 'Hindi';

  @override
  String get languageSystemDefault => 'System default';

  @override
  String get themeLight => 'Light';

  @override
  String get themeDark => 'Dark';

  @override
  String get themeSystem => 'System default';

  @override
  String get liveClassesTitle => 'Live Classes';

  @override
  String get searchClassroomsHint => 'Search classrooms';

  @override
  String get filterAll => 'All';

  @override
  String get filterMine => 'Mine';

  @override
  String get couldNotLoadClassrooms => 'Couldn\'t load classrooms';

  @override
  String get checkConnectionRetry => 'Check your connection and try again';

  @override
  String get noClassroomsFound => 'No classrooms found';

  @override
  String get tryDifferentSearch => 'Try a different search or filter';

  @override
  String enrolledCountLabel(int count) {
    return '$count enrolled';
  }

  @override
  String get classroomDetailTitle => 'Classroom';

  @override
  String get upcomingSessionsTitle => 'Upcoming Sessions';

  @override
  String get noUpcomingSessions => 'No upcoming sessions';

  @override
  String get materialsTitle => 'Materials';

  @override
  String get noMaterialsYet => 'No materials yet';

  @override
  String get viewScheduleCta => 'View schedule';

  @override
  String get requestToJoinCta => 'Request to join';

  @override
  String get choosePassTitle => 'Choose a pass';

  @override
  String passSubtitle(String price, int days) {
    return '$price coins - valid $days days';
  }

  @override
  String get couponCodeOptional => 'Coupon code (optional)';

  @override
  String get messageToTeacherOptional => 'Message to teacher (optional)';

  @override
  String get sendRequestCta => 'Send request';

  @override
  String get liveSessionTitle => 'Live Session';

  @override
  String get couldNotJoinSession => 'Couldn\'t join the session';

  @override
  String get recordingBadge => 'REC';

  @override
  String get raiseHandCta => 'Raise hand';

  @override
  String get typeMessageHint => 'Type a message';

  @override
  String get dashboardTitle => 'Home';

  @override
  String get couldNotLoadDashboard => 'Couldn\'t load your dashboard';

  @override
  String viewerCountLabel(int count) {
    return '$count watching';
  }

  @override
  String get myProgressTitle => 'My Progress';

  @override
  String get attendanceLabel => 'Attendance';

  @override
  String get streakLabel => 'Day streak';

  @override
  String get certificatesLabel => 'Certificates';

  @override
  String get myEarningsTitle => 'My Earnings';

  @override
  String get totalEarnedLabel => 'Total earned';

  @override
  String get walletTitle => 'Wallet';

  @override
  String get couldNotLoadWallet => 'Couldn\'t load your wallet';

  @override
  String get coinBalanceLabel => 'Coin balance';

  @override
  String get buyCoinsCta => 'Buy coins';

  @override
  String get buyCoinsTitle => 'Buy coins';

  @override
  String get withdrawCta => 'Withdraw';

  @override
  String get cancelCta => 'Cancel';

  @override
  String get continueCta => 'Continue';

  @override
  String get orderStartedMessage =>
      'Order started - complete payment to add coins';

  @override
  String get transactionsTitle => 'Transactions';

  @override
  String get noTransactionsYet => 'No transactions yet';

  @override
  String get withdrawalsTitle => 'Withdrawals';

  @override
  String get coinsUnit => 'coins';

  @override
  String get actionCta => 'Take action';

  @override
  String get answerCta => 'Answer';

  @override
  String get answerDoubtTitle => 'Answer doubt';

  @override
  String get askCta => 'Ask';

  @override
  String get askDoubtTitle => 'Ask a doubt';

  @override
  String get assignRoomHint => 'Room';

  @override
  String get assignmentsTitle => 'Assignments';

  @override
  String get breakoutRoomsTitle => 'Breakout Rooms';

  @override
  String get certificatesReportCardsTitle => 'Certificates & Report Cards';

  @override
  String get certificatesTab => 'Certificates';

  @override
  String get chatReportsTab => 'Chat Reports';

  @override
  String get classroomInfoTitle => 'Classroom Info';

  @override
  String get classroomReferralSummaryTitle => 'Classroom Referral Summary';

  @override
  String get classroomReportsTab => 'Classroom Reports';

  @override
  String get closeBreakoutCta => 'Close rooms';

  @override
  String get commissionEarnedLabel => 'Commission earned';

  @override
  String get couldNotLoadAssignments => 'Couldn\'t load assignments';

  @override
  String get couldNotLoadModerationData => 'Couldn\'t load moderation data';

  @override
  String get couldNotLoadPasses => 'Couldn\'t load passes';

  @override
  String get couldNotLoadRecordings => 'Couldn\'t load recordings';

  @override
  String get couldNotLoadReferrals => 'Couldn\'t load referrals';

  @override
  String get createCta => 'Create';

  @override
  String get daysAbbrev => 'd';

  @override
  String get dismissCta => 'Dismiss';

  @override
  String get doneCta => 'Done';

  @override
  String get doubtsTab => 'Doubts';

  @override
  String get enterReferralCodeHint => 'Enter referral code';

  @override
  String get generateParentCodeCta => 'Generate parent code';

  @override
  String get giftsTitle => 'Gifts';

  @override
  String get gradeCta => 'Grade';

  @override
  String get gradeSubmissionTitle => 'Grade submission';

  @override
  String get holidaysTab => 'Holidays';

  @override
  String get homeworkLabel => 'Homework';

  @override
  String get issueCertificateCta => 'Issue certificate';

  @override
  String get mainRoomLabel => 'Main room';

  @override
  String get managePassesTitle => 'Manage Passes';

  @override
  String get marksLabel => 'Marks';

  @override
  String get moderationTitle => 'Moderation';

  @override
  String get newPassTitle => 'New pass';

  @override
  String get noAssignmentsYet => 'No assignments yet';

  @override
  String get noCertificatesYet => 'No certificates yet';

  @override
  String get noChatReportsPending => 'No chat reports pending';

  @override
  String get noClassroomReportsPending => 'No classroom reports pending';

  @override
  String get noDoubtsYet => 'No doubts asked yet';

  @override
  String get noGiftsYet => 'No gifts yet';

  @override
  String get noHolidaysListed => 'No holidays listed';

  @override
  String get noNoticesYet => 'No notices yet';

  @override
  String get noParentQueriesYet => 'No parent queries yet';

  @override
  String get noParticipantsYet => 'No participants yet';

  @override
  String get noPassesYet => 'No passes yet';

  @override
  String get noRecordingsYet => 'No recordings yet';

  @override
  String get noReferralsYet => 'No referrals yet';

  @override
  String get noReportCardsYet => 'No report cards yet';

  @override
  String get noSubmissionsYet => 'No submissions yet';

  @override
  String get noticesTab => 'Notices';

  @override
  String get parentCodeGeneratedTitle => 'Parent access code';

  @override
  String get parentObserverConnectedMessage =>
      'Connected — you can now watch the class';

  @override
  String get parentQueriesTitle => 'Parent Queries';

  @override
  String get participantsTab => 'Participants';

  @override
  String get passTitleLabel => 'Pass title';

  @override
  String get passesTitle => 'Passes';

  @override
  String get peopleYouReferredTitle => 'People you referred';

  @override
  String get priceInCoinsLabel => 'Price (coins)';

  @override
  String recordingsPendingNote(int count) {
    return '$count recording(s) still processing';
  }

  @override
  String get recordingsTitle => 'Recordings';

  @override
  String get redeemCta => 'Redeem';

  @override
  String get referAndEarnTitle => 'Refer & Earn';

  @override
  String get referralRedeemedMessage => 'Referral code redeemed';

  @override
  String get replyCta => 'Reply';

  @override
  String get replyToParentTitle => 'Reply to parent';

  @override
  String get reportCardsTab => 'Report Cards';

  @override
  String roomNumberLabel(int n) {
    return 'Room $n';
  }

  @override
  String roomsActiveLabel(int count) {
    return '$count rooms active';
  }

  @override
  String roomsCountLabel(int n) {
    return '$n rooms';
  }

  @override
  String get saveCta => 'Save';

  @override
  String get sendCta => 'Send';

  @override
  String timesRedeemedLabel(int count) {
    return 'Redeemed $count times';
  }

  @override
  String get validityDaysLabel => 'Validity (days)';

  @override
  String get yourReferralCodeLabel => 'Your referral code';

  @override
  String get submitAnswerTitle => 'Submit your answer';

  @override
  String get submitCta => 'Submit';

  @override
  String get newCouponTitle => 'New coupon';

  @override
  String get couponCodeLabel => 'Coupon code';

  @override
  String get discountPercentLabel => 'Discount %';

  @override
  String get validForDaysLabel => 'Valid for (days)';

  @override
  String get maxUsesOptionalLabel => 'Max uses (optional)';

  @override
  String get couponsTab => 'Coupons';

  @override
  String get noCouponsYet => 'No coupons yet';

  @override
  String discountPercentValueLabel(int percent) {
    return '$percent% off';
  }

  @override
  String discountAmountValueLabel(String amount) {
    return '$amount coins off';
  }

  @override
  String couponUsageLabel(int used, String max) {
    return 'Used $used / $max';
  }

  @override
  String get wishlistTitle => 'Wishlist';

  @override
  String get couldNotLoadWishlist => 'Couldn\'t load your wishlist';

  @override
  String get wishlistEmptyTitle => 'Your wishlist is empty';

  @override
  String get wishlistEmptySubtitle =>
      'Tap the heart on a classroom to save it here';

  @override
  String get attachFileOptionalCta => 'Attach file (optional)';

  @override
  String get draftRestored => 'Draft restored';

  @override
  String get removeVideoFirst => 'Remove the video first';

  @override
  String maxMediaAllowed(int count) {
    return 'Max $count media allowed';
  }

  @override
  String onlyNMoreCouldBeAdded(int count) {
    return 'Only $count more could be added';
  }

  @override
  String galleryError(String error) {
    return 'Gallery error: $error';
  }

  @override
  String cameraError(String error) {
    return 'Camera error: $error';
  }

  @override
  String get removeImagesFirst => 'Remove images first for video';

  @override
  String videoError(String error) {
    return 'Video error: $error';
  }

  @override
  String get addToPost => 'Add to post';

  @override
  String get gallery => 'Gallery';

  @override
  String get videoFromGallery => 'Video from gallery';

  @override
  String get gifSticker => 'GIF / Sticker';

  @override
  String get gifComingSoon => 'GIF integration coming soon!';

  @override
  String get voiceNoteAttached => 'Voice note attached (simulated)';

  @override
  String get discardPostTitle => 'Discard post?';

  @override
  String get discardPostBody => 'Your draft is saved. You can continue later.';

  @override
  String get keepEditing => 'Keep editing';

  @override
  String get discard => 'Discard';

  @override
  String get writeSomethingOrAddMedia => 'Write something or add media';

  @override
  String postTooLong(int count) {
    return 'Post can\'t be longer than $count characters';
  }

  @override
  String get categoriesLoading => 'Categories are loading…';

  @override
  String get categoryLoadFailed => 'Couldn\'t load a category';

  @override
  String get uploadingMedia => 'Uploading media…';

  @override
  String get posting => 'Posting…';

  @override
  String get postCreatedSuccess => 'Post created successfully!';

  @override
  String get whatsOnYourMind => 'What\'s on your mind?';

  @override
  String get addPhotoOrVideoTooltip => 'Add photo or video';

  @override
  String get emojisTooltip => 'Emojis';

  @override
  String get voiceNoteTooltip => 'Voice note';

  @override
  String get visibilityPublic => 'Public';

  @override
  String get visibilityNetwork => 'Network';

  @override
  String get visibilityPrivate => 'Private';

  @override
  String get quickPostTitle => 'Quick Post';

  @override
  String savedAgo(String time) {
    return 'Saved $time';
  }

  @override
  String get postButton => 'Post';

  @override
  String get postedExclaim => 'Posted!';

  @override
  String get timeAgoJustNow => 'just now';

  @override
  String timeAgoMinutes(int count) {
    return '${count}m ago';
  }

  @override
  String timeAgoHours(int count) {
    return '${count}h ago';
  }

  @override
  String timeAgoDays(int count) {
    return '${count}d ago';
  }

  @override
  String get addLocationSheetTitle => 'Add Location';

  @override
  String get addLocationTagPlaceholder => 'Add location tag';

  @override
  String get addOptionLabel => 'Add option';

  @override
  String get addSomeContentFirst => 'Add some content first';

  @override
  String get addTitleOptional => 'Add a title (optional)';

  @override
  String get attachmentRemoved => 'Attachment removed';

  @override
  String attachmentsCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count Attachments',
      one: '1 Attachment',
    );
    return '$_temp0';
  }

  @override
  String get autoPublishSubtitle => 'Auto-publish at scheduled time';

  @override
  String get captionLabel => 'Caption';

  @override
  String get capturePhotoNow => 'Capture a photo right now';

  @override
  String get categoryNoun => 'category';

  @override
  String get categoryRequiredError => 'Selecting a category is required';

  @override
  String get categoryRequiredLabel => 'CATEGORY *';

  @override
  String get clearAllButton => 'Clear all';

  @override
  String get clearButton => 'Clear';

  @override
  String get clearEverythingBody =>
      'Title, content, media, poll and the saved draft will all be cleared together. This cannot be undone.';

  @override
  String get clearEverythingTitle => 'Clear everything?';

  @override
  String get clearLocationLabel => 'Clear location';

  @override
  String get clearSearchTooltip => 'Clear search';

  @override
  String get clearTitleTooltip => 'Clear title';

  @override
  String get closeLabel => 'Close';

  @override
  String get contentMediaPollRequired => 'Content, media or poll is required';

  @override
  String couldNotSelectDocuments(String error) {
    return 'Could not select documents: $error';
  }

  @override
  String couldNotSelectImages(String error) {
    return 'Could not select images from gallery: $error';
  }

  @override
  String couldNotSelectVideo(String error) {
    return 'Could not select video from gallery: $error';
  }

  @override
  String get dismissLabel => 'DISMISS';

  @override
  String draftCachedOnDevice(String label) {
    return '$label · cached on this device';
  }

  @override
  String draftSavedAt(String time) {
    return 'Draft saved $time';
  }

  @override
  String get draftSavedJustNow => 'Draft saved just now';

  @override
  String draftSavedMinutesAgo(int count) {
    return 'Draft saved ${count}m ago';
  }

  @override
  String draftSavedSecondsAgo(int count) {
    return 'Draft saved ${count}s ago';
  }

  @override
  String get editImageLabel => 'Edit image';

  @override
  String get editVideoLabel => 'Edit video';

  @override
  String get everythingCleared => 'Everything cleared';

  @override
  String failedToLoadCategories(String error) {
    return 'Failed to load categories: $error';
  }

  @override
  String get filesLabel => 'Files';

  @override
  String get fillAllPollOptions => 'Fill in all poll options';

  @override
  String get locationLabel => 'Location';

  @override
  String maxFilesAllowed(int count) {
    return 'Max $count files allowed';
  }

  @override
  String pdfPagesCount(int count) {
    return '$count pages';
  }

  @override
  String fileSizeKb(int count) {
    return '$count KB';
  }

  @override
  String get newPostTitle => 'New post';

  @override
  String noMatchForQuery(String query) {
    return 'No match for \"$query\"';
  }

  @override
  String onlyNMoreFilesCouldBeAdded(int count) {
    return 'Only $count more file(s) could be added';
  }

  @override
  String get photosLabel => 'Photos';

  @override
  String get pollLabel => 'Poll';

  @override
  String get pollNeedsTwoOptions => 'Poll needs at least 2 options';

  @override
  String pollOptionNumber(int number) {
    return 'Option $number';
  }

  @override
  String get pollOptionRemoved => 'Poll option removed';

  @override
  String get pollOptionsMustDiffer => 'Poll options cannot be the same';

  @override
  String get postDetailsLabel => 'Post Details';

  @override
  String postScheduledFor(String time) {
    return 'Post scheduled for $time!';
  }

  @override
  String get quickPostBannerSubtitle =>
      'Just want to write text? Fast compose here';

  @override
  String get removeAttachmentLabel => 'Remove attachment';

  @override
  String get removeOptionTooltip => 'Remove option';

  @override
  String get removePollTooltip => 'Remove poll';

  @override
  String get scheduleInOneHour => 'In 1 hour';

  @override
  String get scheduleLabel => 'Schedule';

  @override
  String get scheduleThisEvening => 'This evening';

  @override
  String get scheduleTomorrowEvening => 'Tomorrow evening';

  @override
  String get scheduleTomorrowMorning => 'Tomorrow morning';

  @override
  String searchWithin(String title) {
    return 'Search $title';
  }

  @override
  String get searchingHashtags => 'Searching hashtags…';

  @override
  String get searchingPeople => 'Searching people…';

  @override
  String get selectCategoryError => 'Select a category';

  @override
  String get selectCategoryTitle => 'Select Category';

  @override
  String get selectFutureTime => 'Select a future time';

  @override
  String get selectSubcategoryError => 'Select a subcategory';

  @override
  String get selectSubcategoryTitle => 'Select Subcategory';

  @override
  String get shootQuickVideoClip => 'Shoot a quick video clip';

  @override
  String get subcategoryNoun => 'subcategory';

  @override
  String get subcategoryRequiredLabel => 'SUBCATEGORY';

  @override
  String get takePhotoLabel => 'Take Photo';

  @override
  String get undoLabel => 'UNDO';

  @override
  String get unsavedCloseWarning =>
      'What you\'ve written hasn\'t been saved yet. If you close now, it will be lost.';

  @override
  String get useCameraLabel => 'Use Camera';

  @override
  String get videosLabel => 'Videos';

  @override
  String get visibilityAnyoneCanSee => 'Anyone can see';

  @override
  String get visibilityConnections => 'Connections';

  @override
  String get visibilityJustYou => 'Just you';

  @override
  String get visibilityOnlyMe => 'Only me';

  @override
  String get visibilityOnlyYourNetwork => 'Only your network';

  @override
  String get whatsOnYourMindHashtags =>
      'What\'s on your mind? Use #hashtags to boost reach';

  @override
  String get writeSomethingAboutMedia => 'Write something about this media...';

  @override
  String get addMediaLabel => 'Add Media';

  @override
  String get addPhotosVideosMinTwo => 'Add photos/videos — at least 2';

  @override
  String get addTextLabel => 'Add Text';

  @override
  String get addingMusicProgress => 'Adding music…';

  @override
  String get aspectPortrait => '4:5 Portrait';

  @override
  String get aspectReel => '9:16 Reel';

  @override
  String get aspectSquare => '1:1 Square';

  @override
  String get aspectWide => '16:9 Wide';

  @override
  String get autoBeatSyncLabel => 'Auto (Beat Sync)';

  @override
  String get autoEditTitle => 'Auto Edit';

  @override
  String get autoModeDescription =>
      'Auto: energy-aware cuts, transitions & Ken Burns, BPM detected.';

  @override
  String get backgroundMusicPlaceholder => 'Choose background music';

  @override
  String get beatSyncSetupFailed => 'Setting up the beat-synced cut failed.';

  @override
  String get blendingTransitionsProgress => 'Blending transitions…';

  @override
  String get canvasColorGradeTooltip => 'Canvas & color grade';

  @override
  String canvasGradeSummary(String aspect, String grade) {
    return '$aspect · $grade grade — tap the tune icon to change';
  }

  @override
  String get canvasLabel => 'Canvas';

  @override
  String get captionFontNotReady =>
      'Still preparing the caption font — try again in a moment, or this text will be skipped when rendering.';

  @override
  String get captionForClipHint => 'Caption for this clip';

  @override
  String get cc0OnlyNotice => 'Only copyright-free (CC0) music is shown';

  @override
  String get changeMusicButton => 'Change Music';

  @override
  String get chooseMusicFromDevice => 'Choose from device';

  @override
  String get clipLengthLabel => 'Clip length';

  @override
  String clipNumberDuration(int number, String duration) {
    return 'Clip $number · ${duration}s';
  }

  @override
  String get clipSetupFailed => 'Setting up the clips failed.';

  @override
  String get clipTextDialogTitle => 'Clip text';

  @override
  String get colorGradeLabel => 'Color grade';

  @override
  String get couldntAddMusicTrack =>
      'Couldn\'t add the music track to the montage.';

  @override
  String get couldntAddTrack => 'Couldn\'t add that track — try again.';

  @override
  String get couldntBlendTransitions =>
      'Couldn\'t blend the transitions between clips.';

  @override
  String couldntDownloadTrack(int code) {
    return 'Couldn\'t download that track (server said $code). Try again.';
  }

  @override
  String couldntProcessClip(int number) {
    return 'Couldn\'t process clip $number — it may be corrupted or an unsupported format. Try removing or replacing it.';
  }

  @override
  String get couldntTrimTrack =>
      'Couldn\'t trim that track — try a different clip length.';

  @override
  String get doneLabel => 'Done';

  @override
  String get emptySearchPrompt => 'Search for something...';

  @override
  String get exportButton => 'Export';

  @override
  String get gradeBw => 'B&W';

  @override
  String get gradeMoody => 'Moody';

  @override
  String get gradeNone => 'None';

  @override
  String get gradeVibrant => 'Vibrant';

  @override
  String get gradeVintage => 'Vintage';

  @override
  String get gradeWarm => 'Warm';

  @override
  String get hitRegenerateHint =>
      'Hit Regenerate to re-render with the new canvas/grade.';

  @override
  String inPointSeconds(String seconds) {
    return 'In-point: ${seconds}s';
  }

  @override
  String get kenBurnsLabel => 'Ken Burns';

  @override
  String get lastClipLabel => 'Last clip';

  @override
  String get manualModeDescription =>
      'Manual: clips evenly split, you set everything yourself.';

  @override
  String get manualSetupLabel => 'Manual Setup';

  @override
  String get nextMusicButton => 'Next: Music';

  @override
  String get noInternetConnectionRetry =>
      'No internet connection — check your connection and try again.';

  @override
  String get placingCutsProgress => 'Placing cuts on the beat…';

  @override
  String preparingClipOfTotal(int current, int total) {
    return 'Preparing clip $current of $total…';
  }

  @override
  String preparingOfTotal(int current, int total) {
    return 'Preparing $current of $total…';
  }

  @override
  String get readingMusicProgress => 'Reading music…';

  @override
  String get regenerateButton => 'Regenerate';

  @override
  String get renderingMontageFailed => 'Rendering the montage failed.';

  @override
  String get retryButton => 'Retry';

  @override
  String scoringClipProgress(int current, int total) {
    return 'Scoring clip $current of $total…';
  }

  @override
  String get searchDidntGoThrough => 'Search didn\'t go through — try again.';

  @override
  String get searchFreesoundHint => 'Search Freesound (e.g. lofi, guitar)';

  @override
  String get settingUpClipsProgress => 'Setting up clips…';

  @override
  String get someMediaNotOptimized =>
      'Some media couldn\'t be optimized, but was added as-is.';

  @override
  String get sourceLengthUnknown => 'Source length unknown — in-point disabled';

  @override
  String speedLabel(String value) {
    return 'Speed: ${value}x';
  }

  @override
  String get startPointLabel => 'Start point';

  @override
  String get textAddedCheckLabel => 'Text ✓';

  @override
  String get transCircle => 'Circle';

  @override
  String get transDissolve => 'Dissolve';

  @override
  String get transFlash => 'Flash';

  @override
  String get transGlitch => 'Glitch';

  @override
  String get transRadial => 'Radial';

  @override
  String get transRgbSplit => 'RGB Split';

  @override
  String get transShake => 'Shake';

  @override
  String get transSlide => 'Slide';

  @override
  String get transSmoothSlide => 'Smooth Slide';

  @override
  String get transSqueeze => 'Squeeze';

  @override
  String get transWipe => 'Wipe';

  @override
  String get transZoom => 'Zoom';

  @override
  String get trimSpeedLabel => 'Trim & Speed';

  @override
  String get useThisSoundButton => 'Use this sound';

  @override
  String get addClipLabel => 'Add Clip';

  @override
  String get addMorePhotosVideosHint =>
      'Add more photos/videos — they\'ll all be joined into one video';

  @override
  String get addPhotoLabel => 'Add Photo';

  @override
  String get addPhotosTabLabel => 'Add Photos';

  @override
  String get addStickerTitle => 'Add Sticker';

  @override
  String get adjustTabLabel => 'Adjust';

  @override
  String get analyzingLabel => 'Analyzing…';

  @override
  String get applyLabel => 'Apply';

  @override
  String autoEnhanceFailed(String error) {
    return 'Auto-enhance failed: $error';
  }

  @override
  String get autoEnhanceLabel => 'Auto-Enhance';

  @override
  String get autoPlacedOnFaceHint =>
      'Auto-placed on the detected face — drag/pinch/rotate after.';

  @override
  String get batteryLabel => 'Battery';

  @override
  String get boomerangBakeFailed => 'Couldn\'t create the Boomerang effect.';

  @override
  String get boomerangOnHint => 'Boomerang ON — forward + reverse loop';

  @override
  String get boomerangTabLabel => 'Boomerang';

  @override
  String get brightnessLabel => 'Brightness';

  @override
  String get brushLabel => 'Brush';

  @override
  String get chooseCoverFrame => 'Choose cover frame';

  @override
  String get clearDrawingTooltip => 'Clear drawing';

  @override
  String get clip1ThisMediaLabel => 'Clip 1 (this photo/video)';

  @override
  String get clip2TransitionLabel => 'Transition into clip 2';

  @override
  String clipNormalizeFailed(int number) {
    return 'Couldn\'t process clip $number.';
  }

  @override
  String clipPinchZoomPan(int number) {
    return 'Clip $number — pinch to zoom, drag to pan';
  }

  @override
  String clipPreparingOfTotal(int current, int total) {
    return 'Preparing clip $current/$total…';
  }

  @override
  String clipSelectFailed(String error) {
    return 'Couldn\'t select that clip: $error';
  }

  @override
  String get clipsJoinFailed => 'Couldn\'t join the clips together.';

  @override
  String get clipsTabLabel => 'Clips';

  @override
  String clipsWillMakeVideoWith(int count) {
    return '$count clips will make up the video (with this photo/video)';
  }

  @override
  String get color2GradientHint => 'Color 2 (gradient) — tap a swatch above';

  @override
  String get contrastLabel => 'Contrast';

  @override
  String get coverPreviewUnavailable => 'Cover preview isn\'t available';

  @override
  String get coverTabLabel => 'Cover';

  @override
  String get cropFailed => 'Crop failed.';

  @override
  String get dateLabel => 'Date';

  @override
  String get deleteLabel => 'Delete';

  @override
  String downloadFailedCode(int code) {
    return 'Download failed ($code)';
  }

  @override
  String get drawTabLabel => 'Draw';

  @override
  String get editLabel => 'Edit';

  @override
  String editSaveFailed(String error) {
    return 'Couldn\'t save the edit: $error';
  }

  @override
  String eraserApplyFailed(String error) {
    return 'Couldn\'t apply the eraser: $error';
  }

  @override
  String get eraserHint =>
      'Paint over a blemish/watermark, then tap Apply. Best for small spots on plain backgrounds.';

  @override
  String get eraserTabLabel => 'Eraser';

  @override
  String get erasingLabel => 'Erasing…';

  @override
  String get exportWithMusicFailedTryWithout =>
      'Export with music failed — try again without music';

  @override
  String extraClipsWillJoinEnd(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count extra clips will be added at the end',
      one: '1 extra clip will be added at the end',
    );
    return '$_temp0';
  }

  @override
  String get faceFiltersTitle => 'Face Filters';

  @override
  String get filtersTabLabel => 'Filters';

  @override
  String get flipHorizontalTooltip => 'Flip horizontal';

  @override
  String get flipVerticalTooltip => 'Flip vertical';

  @override
  String get fontLabel => 'Font';

  @override
  String get fxBakeFailed => 'Couldn\'t apply the effect.';

  @override
  String get fxTabLabel => 'FX';

  @override
  String get keepOriginalAudioToo => 'Also keep the video\'s original audio';

  @override
  String get lengthStaysAsTrimSet =>
      'Its length stays whatever is set in the Trim tab';

  @override
  String get liveStickersTitle => 'Live Stickers';

  @override
  String get loadingEllipsis => 'Loading...';

  @override
  String mediaAddFailed(String error) {
    return 'Couldn\'t add that photo/video: $error';
  }

  @override
  String montageExportFailed(String error) {
    return 'Couldn\'t export the montage: $error';
  }

  @override
  String musicAddFailed(String error) {
    return 'Couldn\'t add the music: $error';
  }

  @override
  String get musicAttachFailed => 'Couldn\'t attach the music.';

  @override
  String get musicMixFailed => 'Couldn\'t mix in the music.';

  @override
  String musicSelectFailed(String error) {
    return 'Couldn\'t select that music: $error';
  }

  @override
  String get musicTabLabel => 'Music';

  @override
  String get musicTrackLabel => 'Music track';

  @override
  String get myStickersTitle => 'My Stickers';

  @override
  String get nextClipTransitionLabel => 'Transition into the next clip';

  @override
  String get noExtraClipsYet =>
      'No extra clips yet — add some to join one after another';

  @override
  String get noFaceDetectedNote => '(no face detected — placed centered)';

  @override
  String get orLabel => 'or';

  @override
  String photoSelectFailed(String error) {
    return 'Couldn\'t select that photo: $error';
  }

  @override
  String get primaryClipFxFailed => 'Couldn\'t apply effects to the main clip.';

  @override
  String get primaryClipTrimFailed => 'Couldn\'t trim the main clip.';

  @override
  String get redoTooltip => 'Redo';

  @override
  String get removeBackToEditing => 'Remove — back to editing';

  @override
  String get removeMusicTooltip => 'Remove music';

  @override
  String get resetTooltip => 'Reset';

  @override
  String get resetZoomLabel => 'Reset zoom';

  @override
  String get rotateLabel => 'Rotate';

  @override
  String get rotateRightLabel => 'Rotate right';

  @override
  String get saturationLabel => 'Saturation';

  @override
  String get searchFailedRetry => 'Search failed — try again';

  @override
  String get speedAdjustFailed => 'Couldn\'t adjust the speed.';

  @override
  String get speedTabLabel => 'Speed';

  @override
  String get startFromLabel => 'Start from';

  @override
  String get stickersTabLabel => 'Stickers';

  @override
  String get styleLabel => 'Style';

  @override
  String get tapAddStickerHint =>
      'Tap Add Sticker, then drag/pinch/rotate it on the photo';

  @override
  String get tapAddTextHint =>
      'Tap Add Text, then drag/pinch/rotate it on the photo';

  @override
  String get tapToEnableBoomerang => 'Tap to enable Boomerang';

  @override
  String get textTabLabel => 'Text';

  @override
  String get timeLabel => 'Time';

  @override
  String transformFailed(String error) {
    return 'Couldn\'t apply that: $error';
  }

  @override
  String get transitionChainFailed =>
      'Couldn\'t chain the transitions together.';

  @override
  String get transitionsBlendingProgress => 'Blending transitions…';

  @override
  String get trimTabLabel => 'Trim';

  @override
  String get undoTooltip => 'Undo';

  @override
  String get useLabel => 'Use';

  @override
  String get useThisLabel => 'Use This';

  @override
  String get videoControllerNotReady => 'The video isn\'t ready yet.';

  @override
  String videoLoadFailed(String error) {
    return 'Couldn\'t load the video: $error';
  }

  @override
  String get videoTrimExportFailed => 'Couldn\'t export the trimmed video.';

  @override
  String videoTrimSaveFailed(String error) {
    return 'Couldn\'t save the trimmed video: $error';
  }

  @override
  String get focusHistoryTitle => 'Focus Mode — History';

  @override
  String get focusHistoryLoadFailed => 'Couldn\'t load history.';

  @override
  String get focusHistoryEmpty => 'No focus sessions yet.';

  @override
  String focusHistoryMinutes(int minutes) {
    return '$minutes min';
  }

  @override
  String focusHistoryHours(int hours) {
    return '${hours}h';
  }

  @override
  String focusHistoryHoursMinutes(int hours, int minutes) {
    return '${hours}h ${minutes}m';
  }

  @override
  String get focusRuleNobody => 'Full silence';

  @override
  String get focusRuleTeachersOnly => 'Teachers only';

  @override
  String focusHistoryMeta(String duration, String rule) {
    return '$duration · $rule';
  }

  @override
  String focusHistoryMetaEndedEarly(String duration, String rule) {
    return '$duration · $rule · ended early';
  }

  @override
  String get parentAccessTitle => 'Parent/Guardian Access';

  @override
  String get parentAccessNewCode => 'New Code';

  @override
  String get parentAccessIntro =>
      'Give the code to your parent/guardian from here — they will only see attendance and assignment status, never chat messages. Tap a code to manage its individual devices.';

  @override
  String get parentAccessEmpty => 'No active codes yet.';

  @override
  String get parentAccessLoadFailed => 'Couldn\'t load codes.';

  @override
  String get parentAccessLabelDialogTitle => 'Who is this code for?';

  @override
  String get parentAccessLabelHint => 'e.g. Mom, Dad';

  @override
  String get parentAccessGenerate => 'Generate';

  @override
  String get parentAccessGenerateFailed =>
      'Couldn\'t generate the code. Please try again.';

  @override
  String get parentAccessShareCodeTitle => 'Share this code with your parent';

  @override
  String get parentAccessCodeCopied => 'Copied';

  @override
  String get parentAccessRevokeTitle => 'Revoke access?';

  @override
  String parentAccessRevokeBodyMany(int count, String name) {
    return 'All $count devices linked to \"$name\" will lose access immediately.';
  }

  @override
  String parentAccessRevokeBodyOne(String name) {
    return 'Access for \"$name\" will be revoked immediately.';
  }

  @override
  String get parentAccessRevoke => 'Revoke';

  @override
  String get parentAccessRevokeFailed =>
      'Couldn\'t revoke access. Please try again.';

  @override
  String get parentAccessRevealRateLimited =>
      'Revealed too many times — try again in a while.';

  @override
  String get parentAccessRevealFailed => 'Couldn\'t reveal the code.';

  @override
  String get parentAccessRenew => 'Renew';

  @override
  String get parentAccessRenewed => 'Access renewed.';

  @override
  String get parentAccessRenewFailed => 'Couldn\'t renew. Please try again.';

  @override
  String get parentAccessUnnamed => 'Unnamed';

  @override
  String parentAccessDevicesTap(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count devices · tap to manage',
      one: '1 device · tap to manage',
    );
    return '$_temp0';
  }

  @override
  String get parentAccessRevokeAllTooltip => 'Revoke entire code (all devices)';

  @override
  String get parentAccessExpiredToday => 'Expired today';

  @override
  String parentAccessExpiredDaysAgo(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: 'Expired $count days ago',
      one: 'Expired 1 day ago',
    );
    return '$_temp0';
  }

  @override
  String get parentAccessExpiresToday => 'Expires today';

  @override
  String parentAccessExpiresInDays(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: 'Expires in $count days',
      one: 'Expires in 1 day',
    );
    return '$_temp0';
  }

  @override
  String parentAccessExpiresOn(String date) {
    return 'Expires $date';
  }

  @override
  String get parentAccessNeverUsed => 'Never used';

  @override
  String get parentAccessDevicesTitle => 'Devices';

  @override
  String parentAccessDevicesTitleNamed(String name) {
    return '$name — devices';
  }

  @override
  String get parentAccessDevicesHint =>
      'Revoking a single device keeps access for the other devices.';

  @override
  String get parentAccessDevicesLoadFailed => 'Couldn\'t load devices.';

  @override
  String get parentAccessNoDevices =>
      'No device has been verified with this code yet.';

  @override
  String parentAccessLastActive(String time) {
    return 'Last active: $time';
  }

  @override
  String parentAccessVerifiedAt(String time) {
    return 'Verified: $time';
  }

  @override
  String get parentAccessRevokeDeviceTooltip => 'Revoke only this device';

  @override
  String get parentAccessRevokeDeviceTitle => 'Revoke this device?';

  @override
  String get parentAccessRevokeDeviceBody =>
      'Only this one device will be disconnected; the others stay connected.';

  @override
  String get parentAccessRevokeDeviceFailed => 'Couldn\'t revoke the device.';

  @override
  String get chatYou => 'You';

  @override
  String get chatUnknown => 'Unknown';

  @override
  String get chatSomeone => 'Someone';

  @override
  String get chatGenericError => 'Error';

  @override
  String get chatScrollToMessageFailed =>
      'Couldn\'t scroll to that message (it may be too old)';

  @override
  String chatLoadMessagesFailed(String error) {
    return 'Failed to load messages: $error';
  }

  @override
  String chatLoadOlderFailed(String error) {
    return 'Failed to load older messages: $error';
  }

  @override
  String chatLoadFailed(String error) {
    return 'Failed to load: $error';
  }

  @override
  String chatPinnedBanner(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count pinned messages',
      one: 'Pinned message',
    );
    return '$_temp0';
  }

  @override
  String get chatAttachment => '📎 Attachment';

  @override
  String get chatPinnedMessagesTitle => 'Pinned messages';

  @override
  String get chatNoPinned => 'No pinned messages';

  @override
  String chatPinnedBy(String name) {
    return 'Pinned by $name';
  }

  @override
  String get chatPin => 'Pin';

  @override
  String get chatUnpin => 'Unpin';

  @override
  String chatWallpaperSetFailed(String error) {
    return 'Couldn\'t set wallpaper: $error';
  }

  @override
  String get chatWallpaperRemoveTitle => 'Remove wallpaper?';

  @override
  String get chatWallpaperRemoveBody =>
      'This chat will go back to the default background.';

  @override
  String get chatRemove => 'Remove';

  @override
  String chatWallpaperRemoveFailed(String error) {
    return 'Couldn\'t remove wallpaper: $error';
  }

  @override
  String get chatChangeWallpaper => 'Change wallpaper';

  @override
  String get chatSetWallpaper => 'Set wallpaper';

  @override
  String get chatRemoveWallpaper => 'Remove wallpaper';

  @override
  String get chatWallpaperMenu => 'Chat wallpaper';

  @override
  String get chatSettingWallpaper => 'Setting wallpaper…';

  @override
  String get chatFilterText => 'Text';

  @override
  String get chatFilterMedia => 'Media';

  @override
  String get chatFilterDocs => 'Docs';

  @override
  String get chatFilterLinks => 'Links';

  @override
  String get chatFilterTitle => 'Filter messages';

  @override
  String get chatFilterAllMessages => 'All messages';

  @override
  String get chatFilterImageVideo => 'Image / Video';

  @override
  String get chatFilterDocsFiles => 'Docs / Files';

  @override
  String get chatFilterUrlLinks => 'URL / Links';

  @override
  String chatFilterActive(String label) {
    return 'Filter: $label';
  }

  @override
  String chatFilterResultCount(String label, int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count messages',
      one: '1 message',
    );
    return '$label • $_temp0';
  }

  @override
  String chatNoFilteredMessages(String label) {
    return 'No $label messages in this chat';
  }

  @override
  String get chatNotificationsMuted => 'Notifications muted';

  @override
  String get chatNotificationsUnmuted => 'Notifications unmuted';

  @override
  String chatUpdateFailed(String error) {
    return 'Failed to update: $error';
  }

  @override
  String get chatMuteGroup => 'Mute group';

  @override
  String get chatUnmuteGroup => 'Unmute group';

  @override
  String get chatMuteNotifications => 'Mute notifications';

  @override
  String get chatUnmuteNotifications => 'Unmute notifications';

  @override
  String get chatThisUser => 'this user';

  @override
  String get chatBlockTitle => 'Block user?';

  @override
  String chatBlockBody(String name) {
    return '$name won\'t be able to call or message you, and you won\'t see their messages either.';
  }

  @override
  String get chatBlock => 'Block';

  @override
  String get chatUserBlocked => 'User blocked.';

  @override
  String chatBlockFailed(String error) {
    return 'Block failed: $error';
  }

  @override
  String get chatUserUnblocked => 'User unblocked.';

  @override
  String chatUnblockFailed(String error) {
    return 'Unblock failed: $error';
  }

  @override
  String get chatBlockUser => 'Block user';

  @override
  String get chatUnblockUser => 'Unblock user';

  @override
  String get chatBlockedBanner =>
      'You\'ve blocked this user. Unblock to send messages.';

  @override
  String get chatUnblock => 'Unblock';

  @override
  String get chatDisappearingOff => 'Off';

  @override
  String get chatDisappear1Month => '1 Month';

  @override
  String get chatDisappear6Months => '6 Months';

  @override
  String get chatDisappear1Year => '1 Year';

  @override
  String get chatDisappearingTitle => 'Disappearing messages';

  @override
  String get chatDisappearingSubtitle =>
      'New messages will automatically disappear from the chat after the selected time.';

  @override
  String get chatDisappearingTurnedOff => 'Disappearing messages turned off';

  @override
  String chatDisappearingNewMessages(String duration) {
    return 'New messages will disappear after $duration';
  }

  @override
  String chatDisappearingSetTo(String duration) {
    return 'Disappearing messages set to $duration';
  }

  @override
  String chatDisappearingMenu(String duration) {
    return 'Disappearing: $duration';
  }

  @override
  String get chatPermissionsTitle => 'Message permissions';

  @override
  String get chatPermissionsSubtitle =>
      'Decide who can send messages in this group.';

  @override
  String get chatPermEveryone => 'Everyone';

  @override
  String get chatPermEveryoneSub => 'All members can chat';

  @override
  String get chatPermAdminsOnly => 'Only admins & moderators';

  @override
  String get chatPermAdminsOnlySub =>
      'Everyone else can only read, not send messages';

  @override
  String get chatDailyLimitTitle => 'Daily message limit (members)';

  @override
  String get chatDailyLimitHelp =>
      'Regular members can send only this many messages per day (admins/moderators are always unlimited). Leave empty for no limit.';

  @override
  String get chatDailyLimitHint => 'e.g. 4';

  @override
  String get chatDailyLimitSuffix => 'msgs / day';

  @override
  String get chatPermUpdatedAdminsOnly =>
      'Now only admins & moderators can send messages.';

  @override
  String get chatPermUpdatedEveryone => 'Now all members can send messages.';

  @override
  String chatUpdateFailedDetail(String error) {
    return 'Update failed: $error';
  }

  @override
  String get chatAdminsOnlyBanner =>
      'Only admins and moderators can send messages in this group.';

  @override
  String get chatDailyLimitReached => 'Daily limit reached';

  @override
  String get chatNoLongerMember => 'You\'re no longer a member';

  @override
  String get chatMessageNotAllowed => 'Messages not allowed';

  @override
  String get chatGroupInfo => 'Group info';

  @override
  String get chatChangeGroupPhoto => 'Change group photo';

  @override
  String get chatUploadingPhoto => 'Uploading photo...';

  @override
  String get chatGroupPhotoUpdated => 'Group photo updated ✅';

  @override
  String chatPhotoUpdateFailed(String error) {
    return 'Photo update failed: $error';
  }

  @override
  String get chatJoinRequestsTitle => 'Join requests';

  @override
  String chatJoinRequestsCount(int count) {
    return 'Join requests ($count)';
  }

  @override
  String get chatNoPendingRequests => 'No pending requests';

  @override
  String get chatApprove => 'Approve';

  @override
  String chatApproveFailed(String error) {
    return 'Approve failed: $error';
  }

  @override
  String get chatReject => 'Reject';

  @override
  String chatRejectFailed(String error) {
    return 'Reject failed: $error';
  }

  @override
  String get chatLeaveGroup => 'Leave group';

  @override
  String get chatLeaveGroupTitle => 'Leave group?';

  @override
  String chatLeaveGroupBody(String name) {
    return 'You\'ll no longer receive messages from \"$name\".';
  }

  @override
  String get chatLeave => 'Leave';

  @override
  String chatLeaveFailed(String error) {
    return 'Leave failed: $error';
  }

  @override
  String get chatDeleteGroup => 'Delete group';

  @override
  String get chatDeleteGroupTitle => 'Delete group?';

  @override
  String chatDeleteGroupBody(String name) {
    return '\"$name\" will be deleted permanently — for all members, along with all messages and media. This can\'t be undone.';
  }

  @override
  String chatDeleteFailed(String error) {
    return 'Delete failed: $error';
  }

  @override
  String get chatGroupDeletedByAdmin => 'This group was deleted by the admin.';

  @override
  String get chatSearchInChat => 'Search in chat';

  @override
  String get chatStudyRoom => 'Study Room';

  @override
  String get chatAudioCall => 'Audio Call';

  @override
  String get chatVideoCall => 'Video Call';

  @override
  String chatCallFailed(String error) {
    return 'Call failed: $error';
  }

  @override
  String get chatStudyRoomCardSubtitle =>
      'Whiteboard, timer and live call — all in one place.';

  @override
  String get chatTapToJoin => 'Tap to Join';

  @override
  String get chatCreatePoll => 'Create poll';

  @override
  String get chatPollQuestion => 'Question';

  @override
  String get chatAllowMultipleAnswers => 'Allow multiple answers';

  @override
  String get chatPollNeedsQuestionAndTwo =>
      'A question and at least 2 options are required';

  @override
  String get chatSendPoll => 'Send poll';

  @override
  String chatPollVotes(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count votes',
      one: '1 vote',
    );
    return '$_temp0';
  }

  @override
  String chatPollVotesClosed(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count votes',
      one: '1 vote',
    );
    return '$_temp0 • Closed';
  }

  @override
  String get chatScheduleMessageTitle => 'Schedule message';

  @override
  String get chatMessageFieldLabel => 'Message';

  @override
  String get chatViewScheduled => 'View scheduled';

  @override
  String get chatPickFutureTime => 'Pick a future time';

  @override
  String get chatMessageScheduled => 'Message scheduled';

  @override
  String get chatScheduledMessagesTitle => 'Scheduled messages';

  @override
  String get chatNoScheduled => 'No scheduled messages';

  @override
  String chatLastSeen(String when) {
    return 'last seen $when';
  }

  @override
  String chatLastSeenToday(String time) {
    return 'today at $time';
  }

  @override
  String chatLastSeenYesterday(String time) {
    return 'yesterday at $time';
  }

  @override
  String chatLastSeenOn(String date) {
    return 'on $date';
  }

  @override
  String chatUploadFailed(String error) {
    return 'Upload failed: $error';
  }

  @override
  String chatStickerFailed(String error) {
    return 'Couldn\'t send sticker: $error';
  }

  @override
  String get chatMicPermission =>
      'Microphone permission is needed to send voice notes';

  @override
  String get chatLocationPermissionDenied => 'Location permission denied';

  @override
  String chatLocationShareFailed(String error) {
    return 'Location share failed: $error';
  }

  @override
  String get chatPhotoGallery => 'Photo Gallery';

  @override
  String get chatVideoGallery => 'Video Gallery';

  @override
  String get chatAudio => 'Audio';

  @override
  String get chatFile => 'File';

  @override
  String get chatPresentation => 'Presentation';

  @override
  String get chatForward => 'Forward';

  @override
  String get chatReact => 'React';

  @override
  String get chatInfo => 'Info';

  @override
  String get chatSelect => 'Select';

  @override
  String get chatSaveToDevice => 'Save to device';

  @override
  String get chatDeleteForMe => 'Delete for me';

  @override
  String get chatDeleteForEveryone => 'Delete for everyone';

  @override
  String chatMessageForwarded(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count messages forwarded',
      one: 'Message forwarded',
    );
    return '$_temp0';
  }

  @override
  String chatSelectedCount(int count) {
    return '$count selected';
  }

  @override
  String get chatEditMessageTitle => 'Edit message';

  @override
  String chatEditFailed(String error) {
    return 'Edit failed: $error';
  }

  @override
  String get chatMessageDeleted => 'This message was deleted';

  @override
  String get chatAlreadySaved => 'Already saved in gallery ✅';

  @override
  String get chatDownloading => 'Downloading...';

  @override
  String get chatSavedToGallery => 'Saved to gallery ✅';

  @override
  String get chatDownloadedToFolder =>
      'Downloaded ✅ — in the Download/LearnScroll folder';

  @override
  String chatDownloadFailed(String error) {
    return 'Download failed: $error';
  }

  @override
  String get chatSayHi => 'Say hi 👋';

  @override
  String get chatSendToStart => 'Send a message to start the conversation';

  @override
  String get chatMessageHint => 'Message...';

  @override
  String get chatRecording => 'Recording...';

  @override
  String get chatPreviewPhoto => '📷 Photo';

  @override
  String get chatPreviewVideo => '🎥 Video';

  @override
  String get chatPreviewAudio => '🎵 Audio';

  @override
  String get chatPreviewFile => '📄 File';

  @override
  String get chatPreviewPresentation => '📊 Presentation';

  @override
  String get chatPreviewLocation => '📍 Location';

  @override
  String get chatPreviewStudyRoom => '🧑‍🎓 Study Room';

  @override
  String get chatPreviewPoll => '📊 Poll';

  @override
  String get chatToday => 'Today';

  @override
  String get chatYesterday => 'Yesterday';

  @override
  String get chatAnnouncement => 'Announcement';

  @override
  String chatUploadingPercent(int percent) {
    return '$percent% uploading...';
  }

  @override
  String chatUploadingPhotosPercent(int percent, int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count photos',
      one: '1 photo',
    );
    return '$percent% • $_temp0';
  }

  @override
  String chatPhotosCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count photos',
      one: '1 photo',
    );
    return '$_temp0';
  }

  @override
  String get chatLocationShared => 'Location shared';

  @override
  String chatItemsCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count items',
      one: '1 item',
    );
    return '$_temp0';
  }

  @override
  String get chatSendingAudio => 'Sending audio...';

  @override
  String get chatAudioMessage => 'Audio message';

  @override
  String chatAudioPlayFailed(String error) {
    return 'Couldn\'t play audio: $error';
  }

  @override
  String get chatTranscribe => 'Transcribe';

  @override
  String get chatTranscribing => 'Transcribing...';

  @override
  String get chatTranscript => 'Transcript';

  @override
  String chatTranscribeFailed(String error) {
    return 'Couldn\'t transcribe: $error';
  }

  @override
  String chatVideoLoadFailed(String error) {
    return 'Couldn\'t load video: $error';
  }

  @override
  String get parentModeTitle => 'Parent Mode';

  @override
  String get parentEntryHeading => 'See your child\'s progress';

  @override
  String get parentEntryBody =>
      'You\'ll only see attendance and assignment status — never chat messages. Enter the code your child gave you below.';

  @override
  String get parentEntryCodeHint => 'e.g. 7F3K9QRT';

  @override
  String get parentEntryCodeRequired => 'Enter the code';

  @override
  String get parentEntryViewProgress => 'View Progress';

  @override
  String get parentLoginLink => 'Parent/Guardian? View your child\'s progress';

  @override
  String get parentErrInvalidCode =>
      'Invalid or expired code. Please check it again.';

  @override
  String get parentErrTooManyAttempts =>
      'Too many attempts — please try again in a while.';

  @override
  String get parentErrGeneric => 'Something went wrong. Please try again.';

  @override
  String get parentErrSessionExpired =>
      'Your session has expired. Enter the code again.';

  @override
  String get parentErrAccessRevoked =>
      'Access has been revoked. Ask your child for a new code.';

  @override
  String get parentErrDashboardLoad =>
      'Couldn\'t load the dashboard. Please try again.';

  @override
  String get parentDashNoClassrooms => 'No classrooms found yet.';

  @override
  String get parentDashSubtitle =>
      'Attendance and assignment status — chat content is never shown here.';

  @override
  String get parentDashStreak => 'Current Streak';

  @override
  String parentDashStreakDays(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count days',
      one: '1 day',
    );
    return '$_temp0';
  }

  @override
  String get parentDashTotalClasses => 'Total Classes';

  @override
  String get parentDashAssignmentsPending => 'Assignments Pending';

  @override
  String get parentDashSubmitted => 'Submitted';

  @override
  String get parentDashSignOut => 'Sign out';

  @override
  String get parentDashEnterNewCode => 'Enter a new code';

  @override
  String get focusModeTitle => 'Focus mode';

  @override
  String get focusModeHistoryTooltip => 'History';

  @override
  String get focusModeChangeDuration => 'Change duration';

  @override
  String get focusModeHowLong => 'How long?';

  @override
  String get focusModeCustomDuration => 'Custom duration';

  @override
  String get focusModeWhoCanReach => 'Who can still reach you?';

  @override
  String get focusModeRuleTeachersTitle => 'Only teachers & staff';

  @override
  String get focusModeRuleTeachersSub =>
      'Messages and calls from group admins/moderators still come through; everyone else stays silent.';

  @override
  String get focusModeRuleNobodyTitle => 'Nobody — full silence';

  @override
  String get focusModeRuleNobodySub =>
      'For exam time — no notification will come through, not even from teachers.';

  @override
  String get focusModeUpdate => 'Update focus mode';

  @override
  String get focusModeStart => 'Start focus mode';

  @override
  String get focusModeEndNow => 'End focus mode now';

  @override
  String focusModeActiveLeft(String time) {
    return 'Focus mode is active — $time left';
  }

  @override
  String get focusModeFailed =>
      'Couldn\'t update focus mode. Please try again.';

  @override
  String get focusModeSet => 'Set';

  @override
  String get focusModeHours => 'Hours';

  @override
  String get focusModeMinutes => 'Minutes';

  @override
  String get chatTyping => 'typing...';

  @override
  String get chatOnline => 'online';

  @override
  String get enterClassCta => 'Enter class';

  @override
  String get requestPendingCta => 'Request pending';

  @override
  String get endClassCta => 'End class';

  @override
  String get endClassConfirm => 'End this class for everyone?';

  @override
  String get removedFromSession => 'You were removed from this session.';

  @override
  String get waitlistedNotice =>
      'The class is full — you are on the waitlist and will be let in automatically.';

  @override
  String get waitingForTeacher => 'Waiting for the teacher to start video…';

  @override
  String get savedPostsTitle => 'Saved posts';

  @override
  String get explorePostsTitle => 'Explore';

  @override
  String get noPostsHere => 'No posts here yet';

  @override
  String get postsLoadFailed => 'Couldn\'t load posts';

  @override
  String get deletePostCta => 'Delete post';

  @override
  String get deletePostConfirm => 'Delete this post? This can’t be undone.';

  @override
  String get postDeleted => 'Post deleted';

  @override
  String get postDeleteFailed => 'Couldn\'t delete the post';

  @override
  String get allCaughtUp => 'You\'re all caught up';

  @override
  String get back => 'Back';

  @override
  String get bioLabel => 'Bio';

  @override
  String get changePhotoTooltip => 'Change photo';

  @override
  String get discardChangesTitle => 'Discard changes?';

  @override
  String get discardChangesMessage =>
      'You have unsaved changes. Are you sure you want to go back?';

  @override
  String get downloading => 'Downloading…';

  @override
  String get editProfileButton => 'Edit profile';

  @override
  String get follow => 'Follow';

  @override
  String get followBack => 'Follow Back';

  @override
  String get followersStat => 'Followers';

  @override
  String get messageButton => 'Message';

  @override
  String get moreOptions => 'More options';

  @override
  String mediaTabLabel(int count) {
    return 'Media ($count)';
  }

  @override
  String documentsTabLabel(int count) {
    return 'Documents ($count)';
  }

  @override
  String get noDocumentsYetTitle => 'No documents yet';

  @override
  String get noDocumentsYetSubtitle => 'Documents you share will appear here.';

  @override
  String get noMediaYetTitle => 'No media yet';

  @override
  String get noMediaYetSubtitle =>
      'Photos and videos you post will appear here.';

  @override
  String get noNameYet => 'No name yet';

  @override
  String get noProfileFound => 'Profile not found';

  @override
  String get photoPostLabel => 'Photo';

  @override
  String get videoPostLabel => 'Video';

  @override
  String get postsLoadErrorTitle => 'Couldn\'t load posts';

  @override
  String get postsLoadErrorSubtitle =>
      'Check your connection and pull down to retry.';

  @override
  String get postsStat => 'Posts';

  @override
  String get privateAccountBadge => 'Private';

  @override
  String get privateAccountMessage =>
      'This account is private. Follow to see their posts.';

  @override
  String get privateAccountPendingMessage =>
      'Follow request sent. Wait for approval to see posts.';

  @override
  String get profileLoadErrorTitle => 'Couldn\'t load profile';

  @override
  String get profileUpdatedSuccess => 'Profile updated successfully';

  @override
  String get requestedLabel => 'Requested';

  @override
  String get shareProfileButton => 'Share profile';

  @override
  String shareProfileMessage(String username, String url) {
    return 'Check out $username on LearnScroll: $url';
  }

  @override
  String get showingSavedProfileData =>
      'Showing saved data — pull down to refresh';

  @override
  String get verifiedAccount => 'Verified account';

  @override
  String coinsBalance(num coins) {
    return '$coins coins';
  }

  @override
  String chatOpenFailed(String error) {
    return 'Couldn\'t open chat: $error';
  }

  @override
  String get analyticsTitle => 'Analytics';

  @override
  String get analyticsActiveEnrollments => 'Active enrollments';

  @override
  String get analyticsAvgAttendance => 'Average attendance';

  @override
  String get analyticsAvgMarks => 'Average marks';

  @override
  String get analyticsSyllabusCompletion => 'Syllabus completion';

  @override
  String get analyticsNoSnapshot => 'No analytics yet';

  @override
  String get analyticsNoSnapshotHint =>
      'Analytics are computed periodically. Check back later.';

  @override
  String analyticsComputedAt(String date) {
    return 'Computed $date';
  }

  @override
  String get assignmentDueDateLabel => 'Due date';

  @override
  String get assignmentFeedbackLabel => 'Feedback';

  @override
  String get assignmentGradeLabel => 'Grade';

  @override
  String get assignmentNoSubmissions => 'No submissions yet';

  @override
  String get assignmentNotSubmitted => 'Not submitted';

  @override
  String get assignmentSubmitCta => 'Submit';

  @override
  String get assignmentsAdd => 'Add assignment';

  @override
  String get assignmentsEmpty => 'No assignments yet';

  @override
  String get campusCreateTitle => 'Create campus';

  @override
  String get campusCreateNameLabel => 'Campus name';

  @override
  String get campusCreateNameRequired => 'Enter a campus name';

  @override
  String get campusCreateTypeLabel => 'Campus type';

  @override
  String get campusCreateTypeSchool => 'School';

  @override
  String get campusCreateTypeCollege => 'College';

  @override
  String get campusCreateTypeCoaching => 'Coaching';

  @override
  String get campusCreateThresholdLabel => 'Attendance threshold';

  @override
  String get campusCreateThresholdHint =>
      'Students below this attendance percentage are flagged.';

  @override
  String get campusCreateFeeModuleLabel => 'Enable fee module';

  @override
  String get campusCreateFeeModuleHint =>
      'Track fee structures, invoices and payments for this campus.';

  @override
  String get campusCreateSubmit => 'Create';

  @override
  String get campusCreateSuccess => 'Campus created';

  @override
  String get campusManageSetupCta => 'Manage setup';

  @override
  String get campusNoneCreateCta => 'Create a campus';

  @override
  String get campusSetupTitle => 'Campus setup';

  @override
  String get classDepartmentLabel => 'Department';

  @override
  String get classDepartmentNone => 'No department';

  @override
  String get classNameHint => 'e.g. Class 10';

  @override
  String get classNameLabel => 'Class name';

  @override
  String get classNoSessionsError => 'Create an academic session first';

  @override
  String get classSectionsCta => 'Sections';

  @override
  String get classSessionLabel => 'Academic session';

  @override
  String get classTeacherAssign => 'Assign class teacher';

  @override
  String get classTeacherAssignedSuccess => 'Class teacher assigned';

  @override
  String get classTeacherNotAssigned => 'No class teacher assigned';

  @override
  String get classTeacherTitle => 'Class teacher';

  @override
  String get departmentNameLabel => 'Department name';

  @override
  String get examTermNameLabel => 'Exam term name';

  @override
  String get examTermPickLabel => 'Exam term';

  @override
  String sectionDetailTitle(String className, String sectionName) {
    return '$className · $sectionName';
  }

  @override
  String get sectionNameHint => 'e.g. A';

  @override
  String get sectionNameLabel => 'Section name';

  @override
  String get sessionCurrentBadge => 'Current';

  @override
  String get sessionDateOrderError => 'End date must be after the start date';

  @override
  String get sessionDatesRequiredError => 'Pick a start and an end date';

  @override
  String get sessionEndDateLabel => 'End date';

  @override
  String get sessionNameHint => 'e.g. 2026–27';

  @override
  String get sessionNameLabel => 'Session name';

  @override
  String get sessionSetCurrentLabel => 'Set as current';

  @override
  String sessionSetCurrentSuccess(String name) {
    return '$name is now the current session';
  }

  @override
  String get sessionStartDateLabel => 'Start date';

  @override
  String get setupClassesAdd => 'Add class';

  @override
  String get setupClassesEmpty => 'No classes yet';

  @override
  String get setupClassesTitle => 'Classes';

  @override
  String get setupDepartmentsAdd => 'Add department';

  @override
  String get setupDepartmentsEmpty => 'No departments yet';

  @override
  String get setupDepartmentsTitle => 'Departments';

  @override
  String get setupExamTermsAdd => 'Add exam term';

  @override
  String get setupExamTermsEmpty => 'No exam terms yet';

  @override
  String get setupExamTermsTitle => 'Exam terms';

  @override
  String get setupFeeStructuresAdd => 'Add fee structure';

  @override
  String get setupFeeStructuresEmpty => 'No fee structures yet';

  @override
  String get setupFeeStructuresTitle => 'Fee structures';

  @override
  String get setupLoadFailed => 'Couldn\'t load this list';

  @override
  String get setupRoomsAdd => 'Add room';

  @override
  String get setupRoomsEmpty => 'No rooms yet';

  @override
  String get setupRoomsTitle => 'Rooms';

  @override
  String get setupSectionsAdd => 'Add section';

  @override
  String get setupSectionsEmpty => 'No sections yet';

  @override
  String setupSectionsTitle(String className) {
    return 'Sections · $className';
  }

  @override
  String get setupSessionsAdd => 'Add session';

  @override
  String get setupSessionsEmpty => 'No sessions yet';

  @override
  String get setupSessionsTitle => 'Academic sessions';

  @override
  String get setupStaffAdd => 'Add staff';

  @override
  String get setupStaffEmpty => 'No staff yet';

  @override
  String get setupStaffPendingApproval =>
      'Staff you add may need the user\'s approval before they appear as active.';

  @override
  String get setupStaffTitle => 'Staff';

  @override
  String get setupSubjectsAdd => 'Add subject';

  @override
  String get setupSubjectsEmpty => 'No subjects yet';

  @override
  String get setupSubjectsTitle => 'Subjects';

  @override
  String get roomNameLabel => 'Room name';

  @override
  String get roomVirtualHint =>
      'Virtual rooms are online classrooms with no physical location.';

  @override
  String get roomVirtualLabel => 'Virtual';

  @override
  String get staffPickLabel => 'Staff member';

  @override
  String get staffRoleLabel => 'Role';

  @override
  String get staffUserIdHint => 'Enter the user\'s ID';

  @override
  String get staffUserIdLabel => 'User ID';

  @override
  String get subjectCodeLabel => 'Subject code';

  @override
  String get subjectDepartmentLabel => 'Department';

  @override
  String get subjectNameLabel => 'Subject name';

  @override
  String get subjectPickLabel => 'Subject';

  @override
  String get subjectTeacherApprove => 'Approve';

  @override
  String get subjectTeacherApproved => 'Approved';

  @override
  String get subjectTeacherAssign => 'Assign subject teacher';

  @override
  String get subjectTeacherReject => 'Reject';

  @override
  String get subjectTeacherRejected => 'Rejected';

  @override
  String get subjectTeacherRequest => 'Request assignment';

  @override
  String get subjectTeacherRequested => 'Request sent — waiting for approval';

  @override
  String get subjectTeacherStatusApproved => 'Approved';

  @override
  String get subjectTeacherStatusPending => 'Pending';

  @override
  String get subjectTeacherStatusRejected => 'Rejected';

  @override
  String get subjectTeachersEmpty => 'No subject teachers yet';

  @override
  String get subjectTeachersTitle => 'Subject teachers';

  @override
  String get enrollRollNumberLabel => 'Roll number';

  @override
  String get enrollStatusActive => 'Active';

  @override
  String get enrollStatusGraduated => 'Graduated';

  @override
  String get enrollStatusLabel => 'Status';

  @override
  String get enrollStatusTransferred => 'Transferred';

  @override
  String get enrollStudentIdHint => 'Enter the student\'s user ID';

  @override
  String get enrollStudentIdLabel => 'Student ID';

  @override
  String get enrollmentAddedSuccess => 'Student enrolled';

  @override
  String get enrollmentsAdd => 'Enroll student';

  @override
  String get enrollmentsEmpty => 'No students enrolled yet';

  @override
  String get enrollmentsTitle => 'Enrollments';

  @override
  String get feeAmountDueLabel => 'Due';

  @override
  String get feeAmountLabel => 'Amount';

  @override
  String get feeAmountPaidLabel => 'Paid';

  @override
  String get feeCampusWide => 'Campus-wide';

  @override
  String get feeGenerateInvoices => 'Generate invoices';

  @override
  String feeInsufficientCoinsBody(num coins) {
    return 'You need $coins coins to pay this fee. Add coins to your wallet and try again.';
  }

  @override
  String get feeInsufficientCoinsTitle => 'Not enough coins';

  @override
  String feeInvoicesGenerated(int created, int existing) {
    return '$created invoices created, $existing already existed';
  }

  @override
  String get feeModuleDisabledNote =>
      'The fee module is turned off for this campus.';

  @override
  String get feeNoPayments => 'No payments recorded';

  @override
  String get feeNotesLabel => 'Notes';

  @override
  String get feeOfficeTitle => 'Fee office';

  @override
  String get feePayCta => 'Pay with coins';

  @override
  String get feePaySuccess => 'Payment successful';

  @override
  String get feePaymentHistory => 'Payment history';

  @override
  String get feePaymentModeLabel => 'Payment mode';

  @override
  String get feeRecordPayment => 'Record payment';

  @override
  String get feeRefund => 'Refund';

  @override
  String get feeStatusOverdue => 'Overdue';

  @override
  String get feeStatusPaid => 'Paid';

  @override
  String get feeStatusPartial => 'Partially paid';

  @override
  String get feeStatusPending => 'Pending';

  @override
  String get feeStatusWaived => 'Waived';

  @override
  String get feeTitleLabel => 'Fee title';

  @override
  String get feeWalletCoinsNote =>
      'Fees are paid from your LearnScroll coin wallet.';

  @override
  String get myFeesEmpty => 'No fees to show';

  @override
  String get myFeesTitle => 'My fees';

  @override
  String get idCardTitle => 'Digital ID card';

  @override
  String get idCardAllIssued => 'Everyone has an ID card';

  @override
  String get idCardIssueForOthers => 'Issue for others';

  @override
  String get idCardIssueOwn => 'Issue my ID card';

  @override
  String get idCardIssuedOn => 'Issued on';

  @override
  String get idCardNotIssued => 'No ID card issued yet';

  @override
  String get idCardValidUntil => 'Valid until';

  @override
  String get parentLinkCampusIdHint => 'Campus ID shared by the school';

  @override
  String get parentLinkCampusIdLabel => 'Campus ID';

  @override
  String get parentLinkLoadFailed => 'Couldn\'t load linked children';

  @override
  String get parentLinkMyChildrenTitle => 'My children';

  @override
  String get parentLinkNoChildren => 'No children linked yet';

  @override
  String get parentLinkScreenTitle => 'Link a child';

  @override
  String get parentLinkSubmit => 'Link';

  @override
  String get parentLinkSuccess => 'Child linked successfully';

  @override
  String get parentLinkTokenHint => 'Verification code from the school';

  @override
  String get parentLinkTokenLabel => 'Verification code';

  @override
  String get parentLinkVerifyHint =>
      'Enter the campus ID and the code you received to link your child.';

  @override
  String get parentLinkVerifyTitle => 'Verify and link';

  @override
  String parentLinkedOn(String date) {
    return 'Linked on $date';
  }

  @override
  String get parentOverviewNoEnrollment =>
      'This child isn\'t currently enrolled in an active section at this campus.';

  @override
  String get parentOverviewReportCard => 'Report card';

  @override
  String get parentOverviewNoNotices => 'No notices for this class yet.';

  @override
  String get parentOverviewLoadFailed => 'Couldn\'t load this child\'s details';

  @override
  String get reportCardPercentage => 'Percentage';

  @override
  String get reportCardTitle => 'Report card';

  @override
  String get reportCardView => 'Report cards';

  @override
  String get resultMarksInvalid => 'Enter valid marks';

  @override
  String get resultMaxLabel => 'Max';

  @override
  String get resultObtainedLabel => 'Obtained';

  @override
  String get resultsManage => 'Enter results';

  @override
  String get resultsTitle => 'Results';

  @override
  String get syllabusAdd => 'Add unit';

  @override
  String syllabusCoveredOn(String date) {
    return 'Covered on $date';
  }

  @override
  String get syllabusEmpty => 'No syllabus units yet';

  @override
  String get syllabusMarkCovered => 'Mark as covered';

  @override
  String get syllabusTitle => 'Syllabus';

  @override
  String get syllabusUnitOrderLabel => 'Order';

  @override
  String get syllabusUnitTitleLabel => 'Unit title';

  @override
  String get noUsersHere => 'No one here yet';

  @override
  String get usersLoadFailed => 'Couldn\'t load the list';

  @override
  String mutualFriendsCount(int count) {
    return '$count mutual';
  }
}
