import 'dart:async';

import 'package:flutter/foundation.dart';

import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:work_tracker/data/models/json.dart';

/// A row filter: column equals, is greater than, or is at least / at most a value.
class Filter {
  const Filter.eq(this.column, this.value) : op = 'eq';
  const Filter.gt(this.column, this.value) : op = 'gt';
  const Filter.gte(this.column, this.value) : op = 'gte';
  const Filter.lte(this.column, this.value) : op = 'lte';

  final String column;
  final Object? value;
  final String op;
}

/// Health of the live (realtime) connection.
enum LiveState { connecting, connected, reconnecting, offline }

/// A row in [table] was inserted, updated or deleted, or ([resync]) changes may have been
/// missed (after a reconnect or when the app returns to the foreground) and data should be re-read.
@immutable
class TableChange {
  const TableChange(this.table, {this.record = const {}, this.oldRecord = const {}, this.resync = false});

  final String table;
  final Json record;
  final Json oldRecord;
  final bool resync;
}

/// The signed-in account, for the profile page.
@immutable
class AccountInfo {
  const AccountInfo({required this.userId, required this.email, this.createdAt, this.lastSignInAt});

  final String userId;
  final String email;
  final DateTime? createdAt;
  final DateTime? lastSignInAt;

  /// "arjun.k@ei.study" → "Arjun K".
  String get displayName {
    final local = email.split('@').first;
    final parts = local.split(RegExp('[._-]+')).where((p) => p.isNotEmpty);
    final name = parts.map((p) => p[0].toUpperCase() + p.substring(1)).join(' ');
    return name.isEmpty ? email : name;
  }

  String get initials {
    final words = displayName.split(' ').where((w) => w.isNotEmpty).toList();
    if (words.isEmpty) return '?';
    return words.take(2).map((w) => w[0].toUpperCase()).join();
  }
}

/// Everything the repositories need from the backend (Supabase in the app, a fake in tests).
///
/// Every call runs as the signed-in user, so row-level security limits it to their own rows.
abstract class CloudSource {
  String? get userId;

  /// Email and dates of the signed-in account (null when signed out).
  AccountInfo? get account;

  Stream<String?> get userChanges;

  Future<void> signIn({required String email, required String password});

  Future<void> signOut();

  Future<List<Json>> select(
    String table, {
    List<Filter> where = const [],
    String? orderBy,
    bool descending = false,
    int? limit,
  });

  Future<List<Json>> insert(String table, List<Json> rows);

  Future<List<Json>> update(String table, Json values, {required List<Filter> where});

  Future<void> upsert(String table, List<Json> rows, {required String onConflict});

  Future<void> delete(String table, {required List<Filter> where});

  /// Live changes to one table (only the signed-in user's rows; RLS applies).
  Stream<TableChange> changes(String table);

  /// The live connection's health; emits the current state first.
  Stream<LiveState> get liveState;

  /// Reconnect if needed and tell every listener to re-read (app resumed).
  Future<void> resync();
}

/// Supabase-backed implementation.
class SupabaseCloudSource implements CloudSource {
  SupabaseCloudSource(this._client);

  final SupabaseClient _client;

  @override
  String? get userId => _client.auth.currentUser?.id;

  @override
  AccountInfo? get account {
    final user = _client.auth.currentUser;
    if (user == null) return null;
    return AccountInfo(
      userId: user.id,
      email: user.email ?? '',
      createdAt: DateTime.tryParse(user.createdAt),
      lastSignInAt: user.lastSignInAt == null ? null : DateTime.tryParse(user.lastSignInAt!),
    );
  }

  @override
  Stream<String?> get userChanges => _client.auth.onAuthStateChange.map((state) => state.session?.user.id);

  @override
  Future<void> signIn({required String email, required String password}) async {
    await _client.auth.signInWithPassword(email: email, password: password);
  }

  @override
  Future<void> signOut() => _client.auth.signOut();

  PostgrestFilterBuilder<T> _filtered<T>(PostgrestFilterBuilder<T> query, List<Filter> where) {
    var result = query;
    for (final filter in where) {
      result = switch (filter.op) {
        'eq' => result.eq(filter.column, filter.value!),
        'gt' => result.gt(filter.column, filter.value!),
        'gte' => result.gte(filter.column, filter.value!),
        _ => result.lte(filter.column, filter.value!),
      };
    }
    return result;
  }

  @override
  Future<List<Json>> select(
    String table, {
    List<Filter> where = const [],
    String? orderBy,
    bool descending = false,
    int? limit,
  }) async {
    PostgrestTransformBuilder<List<Json>> query = _filtered(_client.from(table).select(), where);
    if (orderBy != null) query = query.order(orderBy, ascending: !descending);
    if (limit != null) query = query.limit(limit);
    return query;
  }

  @override
  Future<List<Json>> insert(String table, List<Json> rows) async {
    final withUser = [for (final row in rows) {'user_id': userId, ...row}];
    return _client.from(table).insert(withUser).select();
  }

  @override
  Future<List<Json>> update(String table, Json values, {required List<Filter> where}) async {
    return _filtered(_client.from(table).update(values), where).select();
  }

  @override
  Future<void> upsert(String table, List<Json> rows, {required String onConflict}) async {
    final withUser = [for (final row in rows) {'user_id': userId, ...row}];
    await _client.from(table).upsert(withUser, onConflict: onConflict);
  }

  @override
  Future<void> delete(String table, {required List<Filter> where}) async {
    if (where.isEmpty) throw ArgumentError('delete needs at least one filter');
    await _filtered(_client.from(table).delete(), where);
  }

  // ------------------------------------------------------------------ realtime
  final Map<String, StreamController<TableChange>> _tables = {};
  final Map<String, RealtimeChannel> _channels = {};
  final Set<String> _joinedOnce = {};
  final Set<String> _joined = {};
  final StreamController<LiveState> _states = StreamController<LiveState>.broadcast();
  LiveState _state = LiveState.connecting;

  @override
  Stream<TableChange> changes(String table) {
    final controller = _tables.putIfAbsent(
      table,
      // Channels stay joined once opened: screens re-listen on every refresh, and rejoining
      // each time would churn the socket and miss changes in between.
      () => StreamController<TableChange>.broadcast(onListen: () => _join(table)),
    );
    return controller.stream;
  }

  @override
  Stream<LiveState> get liveState async* {
    yield _state;
    yield* _states.stream;
  }

  @override
  Future<void> resync() async {
    for (final entry in _tables.entries) {
      if (!entry.value.hasListener) continue;
      if (!_joined.contains(entry.key)) {
        await _leave(entry.key);
        _join(entry.key);
      }
      entry.value.add(TableChange(entry.key, resync: true));
    }
  }

  void _join(String table) {
    if (_channels.containsKey(table)) return;
    _channels[table] = _client
        .channel('workdigest:$table')
        .onPostgresChanges(
          event: PostgresChangeEvent.all,
          schema: 'public',
          table: table,
          callback: (payload) => _tables[table]?.add(TableChange(table, record: payload.newRecord, oldRecord: payload.oldRecord)),
        )
        .subscribe((status, _) {
          switch (status) {
            case RealtimeSubscribeStatus.subscribed:
              _joined.add(table);
              _setState(LiveState.connected);
              // Rejoined after a drop: changes may have been missed while disconnected.
              if (!_joinedOnce.add(table)) _tables[table]?.add(TableChange(table, resync: true));
            case RealtimeSubscribeStatus.channelError:
            case RealtimeSubscribeStatus.timedOut:
              _joined.remove(table);
              _setState(LiveState.reconnecting);
            case RealtimeSubscribeStatus.closed:
              _joined.remove(table);
              if (_joined.isEmpty) _setState(LiveState.offline);
          }
        });
  }

  Future<void> _leave(String table) async {
    final channel = _channels.remove(table);
    _joinedOnce.remove(table);
    _joined.remove(table);
    if (channel != null) await _client.removeChannel(channel);
  }

  void _setState(LiveState state) {
    if (state == _state) return;
    _state = state;
    _states.add(state);
  }
}
