import 'package:flutter/widgets.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:work_tracker/features/update/update_service.dart';
import 'package:workmanager/workmanager.dart';

/// Every few hours, even while the app is closed, Android runs a tiny check: when a newer
/// release is out, one notification says so (once per release). Tapping it opens the app,
/// where the Update banner installs it.
const _notifiedKey = 'update_notified_build';
const _channel = AndroidNotificationDetails(
  'updates',
  'App updates',
  channelDescription: 'When a new version of WorkDigest is ready to install',
);

/// Whether to announce ``latest`` (newer than this install, and not announced before).
bool shouldNotify(Release latest, AppVersion current, int? alreadyNotified) =>
    latest.build > current.build && latest.build != alreadyNotified;

/// Title and text of the "new version" notification.
(String, String) updateNotice(Release latest) {
  final firstLine = latest.notes.split('\n').firstWhere((l) => l.trim().isNotEmpty, orElse: () => 'Tap to see what is new.');
  return ('WorkDigest ${latest.version} is live', firstLine.trim());
}

final notifications = FlutterLocalNotificationsPlugin();

Future<void> initNotifications() => notifications.initialize(
  settings: const InitializationSettings(android: AndroidInitializationSettings('@mipmap/ic_launcher')),
);

/// Runs in a background isolate started by Android.
@pragma('vm:entry-point')
void updateCallbackDispatcher() {
  Workmanager().executeTask((task, input) async {
    WidgetsFlutterBinding.ensureInitialized();
    final site = input?['site'];
    if (site is! String || site.isEmpty) return true;
    try {
      final latest = await UpdateSource(site).latest();
      if (latest == null) return true;
      final info = await PackageInfo.fromPlatform();
      final current = AppVersion(info.version, int.tryParse(info.buildNumber) ?? 0);
      final prefs = await SharedPreferences.getInstance();
      if (!shouldNotify(latest, current, prefs.getInt(_notifiedKey))) return true;
      await initNotifications();
      final (title, body) = updateNotice(latest);
      await notifications.show(
        id: 7001,
        title: title,
        body: body,
        notificationDetails: const NotificationDetails(android: _channel),
      );
      await prefs.setInt(_notifiedKey, latest.build);
    } on Object {
      return true; // offline: the next run tries again
    }
    return true;
  });
}
