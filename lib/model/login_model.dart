class LoginResponse {

  bool status;
  String message;
  String access;
  String refresh;

  LoginResponse({
    required this.status,
    required this.message,
    required this.access,
    required this.refresh,
  });

  factory LoginResponse.fromJson(Map<String,dynamic> json){

    return LoginResponse(

      status: json["status"],

      message: json["message"],

      access: json["token"]["access"],

      refresh: json["token"]["refresh"],

    );

  }

}

