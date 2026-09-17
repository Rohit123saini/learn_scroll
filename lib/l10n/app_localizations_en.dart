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
  String get like => 'Like';

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
  String openFailed(String error) {
    return 'Open failed: $error';
  }

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
  String failedWithError(String error) {
    return 'Failed: $error';
  }

  @override
  String filesSelectedCount(int count) {
    return '$count selected';
  }
}
