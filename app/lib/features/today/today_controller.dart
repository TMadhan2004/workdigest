import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/providers.dart';

/// Everything the Today screen shows, gathered in one load.
@immutable
class TodayOverview {
  const TodayOverview({
    required this.day,
    required this.dayInfo,
    required this.digest,
    required this.openAsks,
    required this.realActionAsks,
    required this.activeJob,
    required this.notifications,
    required this.now,
    this.staleSince,
  });

  final String day;
  final DayInfo? dayInfo;
  final Digest? digest;
  final int openAsks;
  final int realActionAsks;
  final Job? activeJob;
  final List<AppNotification> notifications;
  final DateTime now;

  /// Set when some data came from the offline cache.
  final DateTime? staleSince;
}

final AutoDisposeFutureProvider<TodayOverview> todayOverviewProvider = FutureProvider.autoDispose<TodayOverview>((ref) async {
  refreshOn(ref, const ['days', 'digests', 'asks', 'notifications', 'generation_jobs']);
  final day = ref.watch(todayProvider);
  final now = ref.watch(clockProvider)();
  final work = ref.watch(workRepositoryProvider);
  final digests = ref.watch(digestRepositoryProvider);
  final asks = ref.watch(asksRepositoryProvider);

  final dayInfo = await work.day(day);
  final digest = await digests.latestForDay(day);
  final askList = await asks.asks();
  Job? job;
  var notifications = const <AppNotification>[];
  try {
    job = await work.activeJob();
    notifications = await work.unreadNotifications();
  } on Object {
    // Live-only details; the cached parts above still render.
  }
  final stale = [dayInfo, digest, askList].where((s) => s.stale).map((s) => s.savedAt).toList();
  final open = askList.value.where((a) => a.isOpen).toList();
  return TodayOverview(
    day: day,
    dayInfo: dayInfo.value,
    digest: digest.value,
    openAsks: open.length,
    realActionAsks: open.where((a) => a.needsRealAction).length,
    activeJob: job,
    notifications: notifications,
    now: now,
    staleSince: stale.isEmpty ? null : stale.reduce((a, b) => a.isBefore(b) ? a : b),
  );
});
