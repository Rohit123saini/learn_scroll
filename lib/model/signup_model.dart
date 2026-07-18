class SignupResponse {
  bool status;
  String message;

  SignupResponse({
    required this.status,
    required this.message,
  });

  factory SignupResponse.fromJson(Map<String, dynamic> json) {
    return SignupResponse(
      status: json["status"],
      message: json["message"],
    );
  }
}