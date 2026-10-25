import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:work_tracker/features/update/background.dart';
import 'package:work_tracker/features/update/update_service.dart';
import 'package:workmanager/workmanager.dart';

const updateTask = 'workdigest-update-check';

/// Android 13+ asks the user once; earlier versions allow notifications by default.
Future<void> askNotificationPermission() async {
  try {
    await notifications.resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()?.requestNotificationsPermission();
  } on Object {
    // No notification support here (tests, other platforms): updates still show in the app.
  }
}

/// Schedule the background check (kept across app restarts; replaced when the site changes).
Future<void> scheduleUpdateChecks(String siteUrl) async {
  if (siteUrl.isEmpty) return;
  await Workmanager().initialize(updateCallbackDispatcher);
  await Workmanager().registerPeriodicTask(
    updateTask,
    updateTask,
    frequency: updateCheckEvery,
    inputData: {'site': siteUrl},
    constraints: Constraints(networkType: NetworkType.connected),
    existingWorkPolicy: ExistingPeriodicWorkPolicy.update,
  );
}
