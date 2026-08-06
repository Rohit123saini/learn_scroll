class LoginResponse {

  bool status;
  String message;
  String access;
  String refresh;
  bool isNewUser;     // Google auth response me aata hai (naya signup vs existing login)
  bool phoneMissing;  // ✅ pehle ye field missing thi -> login/signup screens me
                       // res.phoneMissing use ho raha tha, isse compile error aata tha

  LoginResponse({
    required this.status,
    required this.message,
    required this.access,
    required this.refresh,
    this.isNewUser = false,
    this.phoneMissing = false,
  });

  factory LoginResponse.fromJson(Map<String, dynamic> json) {

    // Normal /login/ response me "token" hamesha present hota hai,
    // lekin defensive null-check rakha taaki backend kabhi shape change kare
    // to app crash na ho, sirf silently 401/exception flow me chala jaye.
    final tokenData = json["token"] as Map<String, dynamic>?;

    return LoginResponse(

      status: json["status"] as bool? ?? false,

      message: json["message"] as String? ?? "",

      access: tokenData?["access"] as String? ?? "",

      refresh: tokenData?["refresh"] as String? ?? "",

      // Ye dono fields sirf Google-auth response me aate hain
      // (/auth/google/), normal login/signup me nahi -> default false safe hai.
      isNewUser: json["is_new_user"] as bool? ?? false,

      phoneMissing: json["phone_missing"] as bool? ?? false,

    );

  }

}