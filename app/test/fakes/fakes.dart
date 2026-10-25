import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:work_tracker/app.dart';
import 'package:work_tracker/core/router/router.dart';
import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/data/sources/cloud_source.dart';
import 'package:work_tracker/data/sources/snapshot_cache.dart';
import 'package:work_tracker/features/reminders/reminders.dart';
import 'package:work_tracker/features/share/share_intake.dart';
import 'package:work_tracker/features/share/share_service.dart';

import '../fixtures/embedded_fixtures.dart';

const userId = 'user-1';

/// An in-memory stand-in for Supabase with the same filters, ordering and RLS-style user scoping.
class FakeCloudSource implements CloudSource {
  FakeCloudSource({this.signedIn = true, DateTime Function()? clock}) : _clock = clock ?? DateTime.now;

  final DateTime Function() _clock;
  final Map<String, List<Json>> tables = {};
  final List<String> calls = [];
  final StreamController<String?> _users = StreamController.broadcast();
  bool signedIn;

  /// When true every call fails, like a phone with no network.
  bool offline = false;

  /// Hook run after each insert (simulates the Mac worker reacting to app requests).
  void Function(String table, Json row)? onInsert;
  int _seq = 0;

  @override
  String? get userId => signedIn ? 'user-1' : null;

  @override
  AccountInfo? get account => signedIn
      ? AccountInfo(
          userId: 'user-1',
          email: 'arjun.k@ei.study',
          createdAt: DateTime.utc(2026, 10, 24, 9, 30),
          lastSignInAt: DateTime.utc(2026, 10, 25, 8, 49),
        )
      : null;

  @override
  Stream<String?> get userChanges => _users.stream;

  @override
  Future<void> signIn({required String email, required String password}) async {
    calls.add('signIn:$email');
    if (password != 'right') throw StateError('Invalid login credentials');
    signedIn = true;
    _users.add(userId);
  }

  @override
  Future<void> signOut() async {
    signedIn = false;
    _users.add(null);
  }

  void _guard(String call) {
    calls.add(call);
    if (offline) throw const SocketException('offline');
  }

  static bool _matches(Json row, List<Filter> where) {
    for (final f in where) {
      final value = row[f.column];
      if (f.op == 'eq' && '$value' != '${f.value}') return false;
      if (f.op == 'gt' && (value == null || '$value'.compareTo('${f.value}') <= 0)) return false;
      if (f.op == 'gte' && (value == null || '$value'.compareTo('${f.value}') < 0)) return false;
      if (f.op == 'lte' && (value == null || '$value'.compareTo('${f.value}') > 0)) return false;
    }
    return true;
  }

  static int _compare(Object? a, Object? b) {
    if (a == null && b == null) return 0;
    if (a == null) return -1;
    if (b == null) return 1;
    if (a is num && b is num) return a.compareTo(b);
    return '$a'.compareTo('$b');
  }

  @override
  Future<List<Json>> select(String table, {List<Filter> where = const [], String? orderBy, bool descending = false, int? limit}) async {
    _guard('select:$table');
    var rows = (tables[table] ?? const []).where((r) => _matches(r, where)).map(Map<String, dynamic>.of).toList();
    if (orderBy != null) {
      rows.sort((a, b) => _compare(a[orderBy], b[orderBy]) * (descending ? -1 : 1));
    }
    if (limit != null && rows.length > limit) rows = rows.sublist(0, limit);
    // Deep copies, like rows decoded from a network response.
    return [for (final row in rows) jsonDecode(jsonEncode(row)) as Json];
  }

  @override
  Future<List<Json>> insert(String table, List<Json> rows) async {
    _guard('insert:$table');
    final created = <Json>[];
    for (final row in rows) {
      _seq++;
      final full = <String, dynamic>{
        'id': 'id-$_seq',
        'user_id': userId,
        'created_at': _clock().toUtc().add(Duration(microseconds: _seq)).toIso8601String(),
        'updated_at': _clock().toUtc().toIso8601String(),
        if (table == 'generation_jobs') 'status': 'queued',
        if (table == 'manager_replies') 'processed': false,
        ...row,
      };
      (tables[table] ??= []).add(full);
      created.add(Map.of(full));
    }
    for (final row in created) {
      emit(table, row);
      onInsert?.call(table, row);
    }
    return created;
  }

  @override
  Future<List<Json>> update(String table, Json values, {required List<Filter> where}) async {
    _guard('update:$table');
    final changed = <Json>[];
    for (final row in tables[table] ?? const <Json>[]) {
      if (_matches(row, where)) {
        row.addAll(values);
        changed.add(Map.of(row));
      }
    }
    for (final row in changed) {
      emit(table, row);
    }
    return changed;
  }

  @override
  Future<void> upsert(String table, List<Json> rows, {required String onConflict}) async {
    _guard('upsert:$table');
    final keys = onConflict.split(',').where((k) => k != 'user_id').toList();
    final existing = tables[table] ??= [];
    for (final row in rows) {
      final match = existing.where((r) => keys.every((k) => '${r[k]}' == '${row[k]}')).firstOrNull;
      if (match != null) {
        match.addAll(row);
      } else {
        existing.add({'user_id': userId, ...row});
      }
      emit(table, row);
    }
  }

  @override
  Future<void> delete(String table, {required List<Filter> where}) async {
    _guard('delete:$table');
    if (where.isEmpty) throw ArgumentError('delete needs at least one filter');
    final rows = tables[table] ?? <Json>[];
    final removed = rows.where((r) => _matches(r, where)).toList();
    rows.removeWhere(removed.contains);
    for (final row in removed) {
      emit(table, const {}, old: row);
    }
  }

  // ---------------------------------------------------------- realtime bus
  final StreamController<TableChange> _bus = StreamController.broadcast(sync: true);
  final StreamController<LiveState> _live = StreamController.broadcast(sync: true);
  LiveState _liveNow = LiveState.connected;
  final List<String> resyncs = [];

  /// Deliver a change as Realtime would (also used to simulate the Mac writing rows).
  void emit(String table, Json record, {Json old = const {}}) =>
      _bus.add(TableChange(table, record: Map.of(record), oldRecord: Map.of(old)));

  /// Write rows as the Mac would (no call recorded) and deliver the changes live.
  void macWrites(String table, List<Json> rows) {
    for (final row in rows) {
      final list = tables[table] ??= [];
      final id = row['device_id'] ?? row['id'];
      final match = id == null ? null : list.where((r) => (r['device_id'] ?? r['id']) == id).firstOrNull;
      if (match != null) {
        match.addAll(row);
      } else {
        list.add({'user_id': userId, ...row});
      }
      emit(table, row);
    }
  }

  void setLive(LiveState state) {
    _liveNow = state;
    _live.add(state);
  }

  @override
  Stream<TableChange> changes(String table) => _bus.stream.where((c) => c.table == table);

  @override
  Stream<LiveState> get liveState async* {
    yield _liveNow;
    yield* _live.stream;
  }

  @override
  Future<void> resync() async {
    resyncs.add('resync');
    for (final table in tables.keys.toList()) {
      _bus.add(TableChange(table, resync: true));
    }
  }

  /// Seed rows directly (no call recorded).
  void seed(String table, List<Json> rows) => (tables[table] ??= []).addAll(rows.map(Map<String, dynamic>.of));

  Future<void> dispose() async {
    await _users.close();
    await _bus.close();
    await _live.close();
  }
}

class FakeShareService implements ShareService {
  final List<String> shared = [];
  final List<String> copied = [];
  bool result = true;

  @override
  Future<bool> share(String text, {String? subject}) async {
    shared.add(text);
    return result;
  }

  @override
  Future<void> copy(String text) async => copied.add(text);
}

/// Fixture text from the generated embedded map, so it also loads on a device (no host file access).
String fixtureText(String name) =>
    embeddedFixtures[name] ?? (throw ArgumentError('unknown fixture $name; run scripts/embed_app_fixtures.py'));

Json fixture(String name) => jsonDecode(fixtureText(name)) as Json;

List<Json> fixtureList(String name) => (jsonDecode(fixtureText(name)) as List<dynamic>).cast<Json>();

/// A pinned "now": Fri 23 Oct 2026, 18:00 local.
final DateTime fixedNow = DateTime(2026, 10, 23, 18);

/// Seeds a realistic signed-in day: the real 23 Oct draft and its evidence, asks, the Mac online.
FakeCloudSource seededCloud({DateTime? now}) {
  final at = now ?? fixedNow;
  final cloud = FakeCloudSource(clock: () => at);
  final digest = fixture('digest_2026-10-23.json');
  cloud
    ..seed('digests', [
      // Created before the pinned clock so app-side edits sort as newer, as they would in real use.
      {...digest, 'user_id': userId, 'created_at': '2026-10-23T10:00:00.000Z'},
    ])
    ..seed('evidence', [
      for (final e in fixtureList('evidence_2026-10-23.json')) {...e, 'user_id': userId},
    ])
    ..seed('days', [
      {'work_date': '2026-10-23', 'status': 'open', 'counts': {'sessions': 4, 'commits': 98}, 'team_members': <String>[]},
    ])
    ..seed('asks', [
      {'ask_id': 'a-otp', 'text': 'Raise a Jira bug for the OTP fix', 'kind': 'one_time_action', 'status': 'open', 'opened_on': '2026-10-21', 'needs_real_action': true, 'times_raised': 3, 'related_tickets': ['EIT-8403']},
      {'ask_id': 'a-crew', 'text': 'Do you have a crew of at least 10 agents?', 'kind': 'question', 'status': 'open', 'opened_on': '2026-10-22', 'needs_real_action': false, 'times_raised': 1},
      {'ask_id': 'a-done', 'text': 'Add Activity comments on EIT-8403', 'kind': 'one_time_action', 'status': 'done', 'opened_on': '2026-10-21', 'closed_on': '2026-10-23', 'evidence': 'https://ei-study.atlassian.net/browse/EIT-8403'},
    ])
    ..seed('standing_rules', [
      {'rule_id': 'R01-structure', 'text': 'Follow the 11-point structure', 'check_kind': 'section_present', 'source': 'seed', 'active': true},
      {'rule_id': 'R10-send-time', 'text': 'Send before 9pm', 'check_kind': 'manual', 'source': 'seed', 'active': true},
    ])
    ..seed('devices', [
      {'device_id': 'mac-1', 'kind': 'mac', 'label': 'DEMO-MAC-01', 'last_seen_at': at.toUtc().subtract(const Duration(seconds: 40)).toIso8601String()},
    ])
    ..seed('session_summaries', [
      {'session_key': 'claude:S1', 'work_date': '2026-10-23', 'source': 'claude_code', 'title': 'Move billing profiles and credits to billing', 'repos': ['billing-service', 'portal-service-v4'], 'first_ts': '2026-10-23T03:44:00Z', 'last_ts': '2026-10-23T15:28:00Z', 'summary': {'headline': 'Billing move', 'done': ['Moved SIS milestones to portal'], 'tests': ['1096/1096']}},
      {'session_key': 'cursor:C1', 'work_date': '2026-10-23', 'source': 'cursor', 'title': 'UI tweaks', 'repos': ['portal-ui'], 'first_ts': '2026-10-23T08:00:00Z', 'last_ts': '2026-10-23T09:00:00Z', 'summary': {'headline': 'UI', 'done': <String>[], 'tests': <String>[]}},
    ])
    ..seed('pull_requests', [
      {'repo': 'identity-service', 'pr_id': 312, 'title': 'feat: Add browser-close logout type', 'url': 'https://bitbucket.org/ei-india-admin/identity-service/pull-requests/312', 'state': 'OPEN', 'opened_at': '2026-10-05T09:27:00Z', 'approved_by': <String>[]},
    ])
    ..seed('settings', [
      {
        'timezone': 'Asia/Kolkata',
        'nudge_time': '20:00:00',
        'send_deadline': '21:00:00',
        'auto_draft': true,
        'provider': 'cursor_cli',
        'transmit': <String, bool>{},
        'manager_name': 'Priya',
        'display_name': 'Arjun K',
        'capabilities': <String, Object?>{},
      },
    ]);
  return cloud;
}

/// Records the reminders the app would schedule instead of calling Android.
class FakeReminderPlugin implements ReminderPlugin {
  final Map<int, Reminder> scheduled = {};
  final List<String> zones = [];

  @override
  Future<void> cancel(int id) async => scheduled.remove(id);

  @override
  Future<void> schedule(int id, Reminder reminder, String timezone) async {
    scheduled[id] = reminder;
    zones.add(timezone);
  }

  List<Reminder> get sorted => scheduled.values.toList()..sort((a, b) => a.at.compareTo(b.at));
}

/// Pumps the whole app (router, theme, providers) on top of fakes and returns the fakes.
Future<({FakeCloudSource cloud, FakeShareService share, ProviderContainer container})> pumpApp(
  WidgetTester tester, {
  FakeCloudSource? cloud,
  String location = '/today',
  DateTime? now,
  Size size = const Size(412, 915),
  double textScale = 1,
  Brightness brightness = Brightness.light,
  bool disableAnimations = false,
  List<Override> overrides = const [],
  FakeReminderPlugin? reminders,
}) async {
  // "system" so each test's platform brightness picks the light or dark theme.
  // "asked_notifications": on a device, Android's permission dialog would pause the app mid-test.
  SharedPreferences.setMockInitialValues({'theme_mode': 'system', 'asked_notifications': true});
  final prefs = await SharedPreferences.getInstance();
  final at = now ?? fixedNow;
  final source = cloud ?? seededCloud(now: at);
  final share = FakeShareService();
  tester.view
    ..physicalSize = size * 2
    ..devicePixelRatio = 2
    ..platformDispatcher.textScaleFactorTestValue = textScale
    ..platformDispatcher.platformBrightnessTestValue = brightness;
  if (disableAnimations) {
    tester.platformDispatcher.accessibilityFeaturesTestValue = const FakeAccessibilityFeatures(disableAnimations: true);
  }
  addTearDown(() {
    tester.view.reset();
    tester.platformDispatcher.clearAllTestValues();
  });
  final container = ProviderContainer(
    overrides: [
      cloudSourceProvider.overrideWithValue(source),
      snapshotCacheProvider.overrideWithValue(SnapshotCache(prefs, clock: () => at)),
      preferencesProvider.overrideWithValue(prefs),
      clockProvider.overrideWithValue(() => at),
      shareServiceProvider.overrideWithValue(share),
      shareIntakeEnabledProvider.overrideWithValue(false),
      // No timers in widget tests: changes refresh at once, and "now" is pinned.
      liveDebounceProvider.overrideWithValue(Duration.zero),
      nowTickProvider.overrideWith((ref) => Stream.value(at)),
      reminderPluginProvider.overrideWithValue(reminders ?? FakeReminderPlugin()),
      ...overrides,
    ],
  );
  addTearDown(container.dispose);
  await tester.pumpWidget(UncontrolledProviderScope(container: container, child: const WorkDigestApp()));
  if (location != '/today') container.read(routerProvider).go(location);
  await tester.pumpAndSettle();
  return (cloud: source, share: share, container: container);
}
