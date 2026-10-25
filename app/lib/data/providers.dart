import 'dart:async';

import 'package:flutter/material.dart' show ThemeMode;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:work_tracker/core/config/app_config.dart';
import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/repositories/asks_repository.dart';
import 'package:work_tracker/data/repositories/digest_repository.dart';
import 'package:work_tracker/data/repositories/ids.dart';
import 'package:work_tracker/data/repositories/settings_repository.dart';
import 'package:work_tracker/data/repositories/work_repository.dart';
import 'package:work_tracker/data/sources/cloud_source.dart';
import 'package:work_tracker/data/sources/snapshot_cache.dart';
import 'package:work_tracker/features/share/share_service.dart';

/// Overridden in `main.dart` (Supabase) and in tests (an in-memory fake).
final cloudSourceProvider = Provider<CloudSource>((ref) => throw UnimplementedError('cloudSourceProvider'));

final snapshotCacheProvider = Provider<SnapshotCache>((ref) => throw UnimplementedError('snapshotCacheProvider'));

/// The current time; tests pin it.
final clockProvider = Provider<DateTime Function()>((ref) => DateTime.now);

final shareServiceProvider = Provider<ShareService>((ref) => const SystemShareService());

final Provider<DigestRepository> digestRepositoryProvider = Provider(
  (ref) => DigestRepository(ref.watch(cloudSourceProvider), ref.watch(snapshotCacheProvider), clock: ref.watch(clockProvider)),
);

final Provider<WorkRepository> workRepositoryProvider = Provider(
  (ref) => WorkRepository(ref.watch(cloudSourceProvider), ref.watch(snapshotCacheProvider), clock: ref.watch(clockProvider)),
);

final Provider<AsksRepository> asksRepositoryProvider = Provider(
  (ref) => AsksRepository(ref.watch(cloudSourceProvider), ref.watch(snapshotCacheProvider), clock: ref.watch(clockProvider)),
);

final Provider<SettingsRepository> settingsRepositoryProvider = Provider((ref) => SettingsRepository(ref.watch(cloudSourceProvider)));

/// The signed-in user id (null when signed out).
final userIdProvider = StreamProvider<String?>((ref) async* {
  final source = ref.watch(cloudSourceProvider);
  yield source.userId;
  yield* source.userChanges;
});

/// Today's work day as `YYYY-MM-DD` (before 04:00 it is still yesterday).
final todayProvider = Provider<String>((ref) => isoDay(workDay(ref.watch(clockProvider)())));

/// How long to wait for a burst of changes (e.g. the Mac pushing several rows) to settle.
final liveDebounceProvider = Provider<Duration>((ref) => const Duration(milliseconds: 300));

/// Re-runs the calling provider when any of [tables] changes, keeping the current data on
/// screen while it reloads (a refresh, not a reload, so there is no loading flicker).
void refreshOn(Ref ref, List<String> tables) {
  final source = ref.watch(cloudSourceProvider);
  final debounce = ref.watch(liveDebounceProvider);
  Timer? pending;
  final subscriptions = [
    for (final table in tables)
      source.changes(table).listen((_) {
        pending?.cancel();
        if (debounce == Duration.zero) {
          ref.invalidateSelf();
        } else {
          pending = Timer(debounce, ref.invalidateSelf);
        }
      }),
  ];
  ref.onDispose(() {
    pending?.cancel();
    for (final subscription in subscriptions) {
      unawaited(subscription.cancel());
    }
  });
}

/// "Now", re-emitted every 10 s so time-based states (Mac online, deadlines) age on their own.
final nowTickProvider = StreamProvider<DateTime>((ref) async* {
  final clock = ref.watch(clockProvider);
  yield clock();
  yield* Stream<DateTime>.periodic(const Duration(seconds: 10), (_) => clock());
});

/// Health of the realtime connection.
final liveStateProvider = StreamProvider<LiveState>((ref) => ref.watch(cloudSourceProvider).liveState);

/// The Mac's latest heartbeat, updated live.
final AutoDisposeFutureProvider<DeviceStatus?> macStatusProvider = FutureProvider.autoDispose<DeviceStatus?>((ref) {
  refreshOn(ref, const ['devices']);
  ref.watch(userIdProvider); // another account has another Mac
  return ref.watch(workRepositoryProvider).mac();
});

/// Whether the Mac is online right now (goes false by itself when heartbeats stop).
final AutoDisposeProvider<bool> macOnlineProvider = Provider.autoDispose<bool>((ref) {
  final mac = ref.watch(macStatusProvider).valueOrNull;
  final now = ref.watch(nowTickProvider).valueOrNull ?? ref.watch(clockProvider)();
  return mac?.isOnline(now) ?? false;
});

/// Build-time settings (site URL, company domain); tests override it.
final appConfigProvider = Provider<AppConfig>((ref) => AppConfig.fromEnvironment());

/// Device-local preferences (overridden in `main.dart` and in tests).
final preferencesProvider = Provider<SharedPreferences>((ref) => throw UnimplementedError('preferencesProvider'));

/// Dark (default, the Aurora look), light or follow the system; remembered on this device.
class ThemeModeController extends Notifier<ThemeMode> {
  static const _key = 'theme_mode';

  @override
  ThemeMode build() {
    final saved = ref.watch(preferencesProvider).getString(_key);
    return ThemeMode.values.where((m) => m.name == saved).firstOrNull ?? ThemeMode.dark;
  }

  Future<void> set(ThemeMode mode) async {
    state = mode;
    await ref.read(preferencesProvider).setString(_key, mode.name);
  }
}

final themeModeProvider = NotifierProvider<ThemeModeController, ThemeMode>(ThemeModeController.new);

/// Your settings, live (manager name, AI availability and the evening times).
final AutoDisposeFutureProvider<AppSettings> profileProvider = FutureProvider.autoDispose<AppSettings>((ref) {
  refreshOn(ref, const ['settings']);
  return ref.watch(settingsRepositoryProvider).load();
});

/// "Priya" → "Priya", "your manager's" → "Your manager's" (for the start of a sentence or a title).
String capitalizeFirst(String text) => text.isEmpty ? text : text[0].toUpperCase() + text.substring(1);

/// "Priya's" (or "your manager's" until the Mac setup names the manager).
final AutoDisposeProvider<String> managerPossessiveProvider = Provider.autoDispose<String>(
  (ref) => (ref.watch(profileProvider).valueOrNull ?? const AppSettings()).managerPossessive(),
);
