import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// A cached copy of a query result, with when it was saved.
class Snapshot<T> {
  const Snapshot(this.value, {required this.savedAt, required this.stale});

  final T value;
  final DateTime savedAt;

  /// True when the network failed and this came from the cache.
  final bool stale;
}

/// Read-through JSON cache so the last digest, asks and today's state stay readable offline.
///
/// Only data already visible to the signed-in user is stored, on the device, under keys
/// namespaced by user id. Signing out clears it.
class SnapshotCache {
  SnapshotCache(this._prefs, {DateTime Function()? clock}) : _clock = clock ?? DateTime.now;

  final SharedPreferences _prefs;
  final DateTime Function() _clock;
  static const _prefix = 'wd.cache.';

  String _key(String userId, String name) => '$_prefix$userId.$name';

  /// Runs ``load``; on success caches the raw JSON, on failure returns the cached copy (or rethrows).
  Future<Snapshot<T>> read<T>({
    required String userId,
    required String name,
    required Future<Object?> Function() load,
    required T Function(Object? json) decode,
  }) async {
    try {
      final fresh = await load();
      await _prefs.setString(
        _key(userId, name),
        jsonEncode({'saved_at': _clock().toIso8601String(), 'data': fresh}),
      );
      return Snapshot(decode(fresh), savedAt: _clock(), stale: false);
    } on Object {
      final cached = _prefs.getString(_key(userId, name));
      if (cached == null) rethrow;
      final envelope = jsonDecode(cached) as Map<String, dynamic>;
      return Snapshot(
        decode(envelope['data']),
        savedAt: DateTime.tryParse('${envelope['saved_at']}') ?? _clock(),
        stale: true,
      );
    }
  }

  Future<void> clear(String userId) async {
    for (final key in _prefs.getKeys().where((k) => k.startsWith('$_prefix$userId.')).toList()) {
      await _prefs.remove(key);
    }
  }
}
