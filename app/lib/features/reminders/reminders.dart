import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:timezone/data/latest_all.dart' as tz_data;
import 'package:timezone/timezone.dart' as tz;
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/features/update/background.dart';

/// "Send your digest" reminders on the phone: Monday to Friday, an hour and a half before the
/// time you send it (Settings → Send your digest by). A day you already marked sent is skipped.
///
/// Android only fires notifications that were scheduled beforehand, so the next two weeks are
/// scheduled whenever the app starts, comes back, or the time or today's status changes.
const reminderLead = Duration(hours: 1, minutes: 30);
const reminderHorizonDays = 14;
const _firstId = 9100;
const _remindersKey = 'send_reminders';

const _channel = AndroidNotificationDetails(
  'send_reminders',
  'Send reminders',
  channelDescription: 'A reminder to review and send your digest, on weekdays',
  importance: Importance.high,
  priority: Priority.high,
);

/// "18:00" → "16:00": the draft is prepared two hours before the send time (never before 00:00).
String draftTimeFor(String sendBy) => _shift(sendBy, const Duration(hours: 2));

/// "18:00" → "16:30": when the phone reminds you.
String reminderTimeFor(String sendBy) => _shift(sendBy, reminderLead);

String _shift(String hhmm, Duration earlier) {
  final parts = hhmm.split(':');
  final minutes = (int.tryParse(parts.first) ?? 0) * 60 + (parts.length > 1 ? int.tryParse(parts[1]) ?? 0 : 0) - earlier.inMinutes;
  final total = minutes < 0 ? 0 : minutes;
  return '${(total ~/ 60).toString().padLeft(2, '0')}:${(total % 60).toString().padLeft(2, '0')}';
}

/// One reminder: when, and what it says.
@immutable
class Reminder {
  const Reminder(this.at, this.title, this.body);

  /// Wall-clock time in your time zone.
  final DateTime at;
  final String title;
  final String body;
}

/// The reminders to schedule from [now] (wall-clock time in your zone): weekdays only, the
/// reminder time still ahead, today left out once the digest is marked sent.
List<Reminder> plannedReminders({
  required DateTime now,
  required String sendBy,
  required String manager,
  bool sentToday = false,
  int days = reminderHorizonDays,
}) {
  final parts = sendBy.split(':');
  final hour = int.tryParse(parts.first) ?? 21;
  final minute = parts.length > 1 ? int.tryParse(parts[1]) ?? 0 : 0;
  final to = manager.isEmpty ? 'your manager' : manager;
  final out = <Reminder>[];
  for (var i = 0; i < days; i++) {
    final day = DateTime(now.year, now.month, now.day + i);
    if (day.weekday == DateTime.saturday || day.weekday == DateTime.sunday) continue;
    if (i == 0 && sentToday) continue;
    final send = DateTime(day.year, day.month, day.day, hour, minute);
    final at = send.subtract(reminderLead);
    if (!at.isAfter(now)) continue;
    out.add(Reminder(at, 'Time to send your digest', "Review today's draft and send it to $to by $sendBy."));
  }
  return out;
}

/// What the phone's notification system is asked to do (replaced in tests).
abstract class ReminderPlugin {
  Future<void> cancel(int id);
  Future<void> schedule(int id, Reminder reminder, String timezone);
}

class LocalReminderPlugin implements ReminderPlugin {
  bool _zonesLoaded = false;

  @override
  Future<void> cancel(int id) => notifications.cancel(id: id);

  @override
  Future<void> schedule(int id, Reminder reminder, String timezone) async {
    if (!_zonesLoaded) {
      tz_data.initializeTimeZones();
      _zonesLoaded = true;
    }
    final zone = tz.getLocation(timezone);
    final at = reminder.at;
    await notifications.zonedSchedule(
      id: id,
      title: reminder.title,
      body: reminder.body,
      scheduledDate: tz.TZDateTime(zone, at.year, at.month, at.day, at.hour, at.minute),
      notificationDetails: const NotificationDetails(android: _channel),
      // Inexact alarms need no special permission; Android may deliver a few minutes late.
      androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
    );
  }
}

final reminderPluginProvider = Provider<ReminderPlugin>((ref) => LocalReminderPlugin());

/// Whether phone reminders are on (this phone only; on by default).
class RemindersEnabled extends Notifier<bool> {
  @override
  bool build() => ref.watch(preferencesProvider).getBool(_remindersKey) ?? true;

  Future<void> set({required bool on}) async {
    state = on;
    await ref.read(preferencesProvider).setBool(_remindersKey, on);
  }
}

final remindersEnabledProvider = NotifierProvider<RemindersEnabled, bool>(RemindersEnabled.new);

/// Replaces the scheduled reminders with the current plan.
Future<void> syncReminders(Ref ref) async {
  final plugin = ref.read(reminderPluginProvider);
  for (var i = 0; i < reminderHorizonDays; i++) {
    await plugin.cancel(_firstId + i);
  }
  if (!ref.read(remindersEnabledProvider) || ref.read(cloudSourceProvider).userId == null) return;
  final settings = await ref.read(settingsRepositoryProvider).load();
  final today = ref.read(todayProvider);
  final digest = (await ref.read(digestRepositoryProvider).latestForDay(today)).value;
  tz_data.initializeTimeZones();
  final zone = tz.getLocation(settings.timezone);
  final local = tz.TZDateTime.from(ref.read(clockProvider)(), zone);
  final plan = plannedReminders(
    now: DateTime(local.year, local.month, local.day, local.hour, local.minute),
    sendBy: settings.sendDeadline,
    manager: settings.managerName,
    sentToday: digest?.isSent ?? false,
  );
  for (var i = 0; i < plan.length; i++) {
    await plugin.schedule(_firstId + i, plan[i], settings.timezone);
  }
}

/// Keeps the reminders in step: on start, on sign-in or out, when the send time or the switch
/// changes, and when today's digest is marked sent.
final reminderSyncProvider = Provider<void>((ref) {
  ref
    ..listen(userIdProvider, (_, _) => unawaited(_safeSync(ref)))
    ..listen(remindersEnabledProvider, (_, _) => unawaited(_safeSync(ref)))
    ..listen(profileProvider, (previous, next) {
      final before = previous?.valueOrNull;
      final after = next.valueOrNull;
      if (after != null && (before == null || before.sendDeadline != after.sendDeadline || before.timezone != after.timezone)) {
        unawaited(_safeSync(ref));
      }
    });
  final source = ref.read(cloudSourceProvider);
  final sub = source.changes('digests').listen((_) => unawaited(_safeSync(ref)));
  ref.onDispose(() => unawaited(sub.cancel()));
  unawaited(_safeSync(ref));
});

Future<void> _safeSync(Ref ref) async {
  try {
    await syncReminders(ref);
  } on Object catch (error) {
    debugPrint('reminders not updated: $error'); // offline: the next start or change tries again
  }
}
