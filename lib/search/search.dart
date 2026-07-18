
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'api_service.dart';
import '../utils/api.dart';
import '../profile/screens/target_profile.dart';

const Color bgColor = Color(0xFF030F27);

class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final TextEditingController _searchController = TextEditingController();
  List<dynamic> _searchResults = [];
  bool _isLoading = false;
  Timer? _debounce;

  void _onSearchChanged(String query) {
    if (_debounce?.isActive?? false) _debounce!.cancel();

    _debounce = Timer(const Duration(milliseconds: 500), () {
      if (query.trim().isEmpty) {
        setState(() {
          _searchResults = [];
          _isLoading = false;
        });
        return;
      }
      _fetchSearchResults(query);
    });
  }

  Future<void> _fetchSearchResults(String query) async {
    setState(() {
      _isLoading = true;
    });

    final results = await ApiService.searchUsers(query);

    setState(() {
      _searchResults = results;
      _isLoading = false;
    });
  }

  @override
  void dispose() {
    _searchController.dispose();
    _debounce?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        automaticallyImplyLeading: false,
        backgroundColor: bgColor,
        toolbarHeight: 80,
        title: Container(
          height: 48,
          decoration: BoxDecoration(
            color: Colors.white.withOpacity(0.15),
            borderRadius: BorderRadius.circular(12),
          ),
          child: TextField(
            controller: _searchController,
            onChanged: _onSearchChanged,
            style: const TextStyle(color: Colors.white),
            decoration: InputDecoration(
              hintText: "Search users...",
              hintStyle: const TextStyle(color: Colors.white60),
              prefixIcon: const Icon(Icons.search, color: Colors.white70),
              suffixIcon: _searchController.text.isNotEmpty
               ? IconButton(
                      icon: const Icon(Icons.clear, color: Colors.white70),
                      onPressed: () {
                        _searchController.clear();
                        _onSearchChanged('');
                      },
                    )
                  : null,
              border: InputBorder.none,
              contentPadding: const EdgeInsets.symmetric(vertical: 12),
            ),
          ),
        ),
      ),
      body: _isLoading
       ? const Center(child: CircularProgressIndicator(color: bgColor))
          : _searchController.text.trim().isEmpty
           ? Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(Icons.person_search_rounded, size: 80, color: Colors.grey.shade300),
                      const SizedBox(height: 10),
                      Text(
                        "Search for friends or creators",
                        style: TextStyle(color: Colors.grey.shade500, fontSize: 16),
                      ),
                    ],
                  ),
                )
              : _searchResults.isEmpty
               ? Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(Icons.search_off_rounded, size: 70, color: Colors.grey.shade400),
                          const SizedBox(height: 10),
                          const Text(
                            "No users found",
                            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: Colors.grey),
                          ),
                        ],
                      ),
                    )
                  : ListView.builder(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                      itemCount: _searchResults.length,
                      itemBuilder: (context, index) {
                        final user = _searchResults[index];
                        final String username = user['username']?? '';
                        final String firstName = user['first_name']?? '';
                        final String lastName = user['last_name']?? '';
                        final String? photoPath = user['profile_photo'];

                        String fullImageUrl = "";
                        if (photoPath!= null && photoPath.isNotEmpty) {
                          if (photoPath.startsWith('http')) {
                            fullImageUrl = photoPath;
                          } else {
                            fullImageUrl = "${Api.baseUrl}$photoPath";
                          }
                        }

                        return Card(
                          elevation: 0.5,
                          margin: const EdgeInsets.symmetric(vertical: 4), // Thoda patla
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(10),
                            side: BorderSide(color: Colors.grey.shade200),
                          ),
                          child: ListTile(
                            contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2), // Patla kiya
                            dense: true, // Aur compact
                            leading: CircleAvatar(
                              radius: 22, // 24 se 22 kiya
                              backgroundColor: Colors.grey.shade200,
                              child: ClipOval(
                                child: photoPath == null || photoPath.isEmpty
                                 ? const Icon(Icons.person, size: 26, color: Colors.grey)
                                    : CachedNetworkImage(
                                        imageUrl: fullImageUrl,
                                        width: 44,
                                        height: 44,
                                        fit: BoxFit.cover,
                                        placeholder: (c, u) => const CircularProgressIndicator(strokeWidth: 2),
                                        errorWidget: (c, u, e) => const Icon(Icons.person, color: Colors.grey),
                                      ),
                              ),
                            ),
                            title: Text(
                              username,
                              style: const TextStyle(
                                fontWeight: FontWeight.bold,
                                fontSize: 15, // 16 se 15
                              ),
                            ),
                            subtitle: Text(
                              (firstName.isEmpty && lastName.isEmpty)
                               ? "No name provided"
                                  : "$firstName $lastName".trim(),
                              style: TextStyle(
                                color: Colors.grey.shade600,
                                fontSize: 13, // 14 se 13
                              ),
                            ),
                            // 🔥 Follow button hata ke arrow sign laga diya
                            trailing: Icon(
                              Icons.arrow_forward_ios,
                              size: 16,
                              color: Colors.grey.shade400,
                            ),

                            // Poore ListTile pe tap hoga to profile khulegi
                            onTap: () {
                              debugPrint("======> CLICKED ON USERNAME: '$username'");

                              if (username.trim().isNotEmpty) {
                                Navigator.push(
                                  context,
                                  MaterialPageRoute(
                                    builder: (context) => TargetProfilePage(
                                      username: username,
                                    ),
                                  ),
                                );
                              } else {
                                ScaffoldMessenger.of(context).showSnackBar(
                                  const SnackBar(
                                    content: Text("Error: This user does not have a valid username!"),
                                    backgroundColor: Colors.redAccent,
                                  ),
                                );
                              }
                            },
                          ),
                        );
                      },
                    ),
    );
  }
}