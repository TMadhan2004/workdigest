import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:ota_update/ota_update.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:work_tracker/data/providers.dart';

/// The newest Android release, from the website's `version.json`:
///
/// ```json
/// {"android": {"version": "1.4.0", "build": 14, "apk_url": "…/WorkDigest.apk",
///              "sha256": "…", "min_build": 9, "notes": "What's new…", "size": 31457280}}
/// ```
@immutable
class Release {
  const Release({required this.version, required this.build, required this.apkUrl, this.sha256, this.minBuild = 0, this.notes = '', this.size});

  /// Null when the file has no usable Android entry.
  static Release? fromVersionJson(Map<String, dynamic> json) {
    final android = json['android'];
    if (android is! Map<String, dynamic>) return null;
    final build = android['build'];
    final url = android['apk_url'];
    if (build is! int || url is! String || !url.startsWith('https://')) return null;
    return Release(
      version: '${android['version'] ?? json['version'] ?? build}',
      build: build,
      apkUrl: url,
      sha256: android['sha256'] as String?,
      minBuild: (android['min_build'] as int?) ?? 0,
      notes: (android['notes'] as String?) ?? (json['notes'] as String?) ?? '',
      size: android['size'] as int?,
    );
  }

  final String version;
  final int build;
  final String apkUrl;

  /// Checked after download: a tampered or truncated file is never installed.
  final String? sha256;

  /// Installs older than this must update before they can be used.
  final int minBuild;
  final String notes;
  final int? size;
}

/// The installed app's version.
@immutable
class AppVersion {
  const AppVersion(this.version, this.build);

  final String version;
  final int build;
}

/// The installed version compared with the newest release.
@immutable
class UpdateStatus {
  const UpdateStatus({required this.current, required this.latest});

  final AppVersion current;
  final Release latest;

  bool get available => latest.build > current.build;

  /// Too old to keep working with the cloud: update first.
  bool get required => current.build < latest.minBuild;
}

/// Reads `version.json` from the website.
class UpdateSource {
  UpdateSource(this.siteUrl, {http.Client? client}) : _client = client ?? http.Client();

  final String siteUrl;
  final http.Client _client;

  Future<Release?> latest() async {
    final base = siteUrl.endsWith('/') ? siteUrl.substring(0, siteUrl.length - 1) : siteUrl;
    final response = await _client.get(Uri.parse('$base/version.json')).timeout(const Duration(seconds: 20));
    if (response.statusCode != 200) return null;
    final json = jsonDecode(utf8.decode(response.bodyBytes));
    return json is Map<String, dynamic> ? Release.fromVersionJson(json) : null;
  }
}

/// Download progress (0–1, or null while unknown) and the outcome.
@immutable
class InstallProgress {
  const InstallProgress({this.fraction, this.installing = false, this.error});

  final double? fraction;

  /// Android's installer is open; the user confirms the update there.
  final bool installing;
  final String? error;
}

/// Downloads the APK (checking its checksum) and opens Android's installer.
typedef UpdateInstaller = Stream<InstallProgress> Function(Release release);

Stream<InstallProgress> otaInstall(Release release) {
  return OtaUpdate()
        .execute(release.apkUrl, destinationFilename: 'WorkDigest-${release.build}.apk', sha256checksum: release.sha256)
        .map(
          (event) => switch (event.status) {
            OtaStatus.DOWNLOADING => InstallProgress(fraction: (double.tryParse(event.value ?? '') ?? 0) / 100),
            OtaStatus.INSTALLING || OtaStatus.INSTALLATION_DONE => const InstallProgress(fraction: 1, installing: true),
            OtaStatus.PERMISSION_NOT_GRANTED_ERROR => const InstallProgress(
              error: 'Allow WorkDigest to install updates (Android asks once), then try again.',
            ),
            OtaStatus.CHECKSUM_ERROR => const InstallProgress(error: 'The download was damaged, so it was not installed. Try again.'),
            OtaStatus.DOWNLOAD_ERROR => const InstallProgress(error: 'The download failed. Check your connection and try again.'),
            _ => InstallProgress(error: 'The update could not be installed (${event.value ?? event.status.name}).'),
          },
        );
}

/// The installed version (from the APK); tests override it.
final appVersionProvider = FutureProvider<AppVersion>((ref) async {
  final info = await PackageInfo.fromPlatform();
  return AppVersion(info.version, int.tryParse(info.buildNumber) ?? 0);
});

/// Whether this build may update itself: only release builds, which are signed with the
/// release key. A debug or profile build is signed with another key, so Android would refuse
/// the release APK ("package conflicts with an existing package").
const bool updatesSupported = kReleaseMode;

/// Null when the app was built without a website, or is not a release build (no update checks).
final updateSourceProvider = Provider<UpdateSource?>((ref) {
  final site = ref.watch(appConfigProvider).siteUrl;
  return site.isEmpty || !updatesSupported ? null : UpdateSource(site);
});

final updateInstallerProvider = Provider<UpdateInstaller>((ref) => otaInstall);

/// How often the open app looks for a new release (it also looks on start and on resume).
const updateCheckEvery = Duration(hours: 6);

/// The update state; null when there is nothing to compare (no website, offline, bad file).
final updateStatusProvider = FutureProvider<UpdateStatus?>((ref) async {
  final source = ref.watch(updateSourceProvider);
  if (source == null) return null;
  final timer = Timer(updateCheckEvery, ref.invalidateSelf);
  ref.onDispose(timer.cancel);
  try {
    final latest = await source.latest();
    if (latest == null) return null;
    return UpdateStatus(current: await ref.watch(appVersionProvider.future), latest: latest);
  } on Object {
    return null; // offline or the site is down: try again later, never block the app
  }
});
