import 'dart:async';

import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/sources/cloud_source.dart';
import 'package:work_tracker/data/sources/snapshot_cache.dart';

/// Days, jobs for the Mac, the Mac's heartbeat, notifications and the activity timeline.
class WorkRepository {
  WorkRepository(this._source, this._cache, {DateTime Function()? clock}) : _clock = clock ?? DateTime.now;

  final CloudSource _source;
  final SnapshotCache _cache;
  final DateTime Function() _clock;

  String get _uid => _source.userId ?? 'anonymous';

  Future<Snapshot<DayInfo?>> day(String day) {
    return _cache.read<DayInfo?>(
      userId: _uid,
      name: 'day.$day',
      load: () => _source.select('days', where: [Filter.eq('work_date', day)], limit: 1),
      decode: (json) {
        final rows = (json as List?)?.cast<Json>() ?? const [];
        return rows.isEmpty ? null : DayInfo.fromJson(rows.first);
      },
    );
  }

  /// Close the day: store rating, team and project, then ask the Mac for the final draft.
  Future<Job> closeDay(String day, {required int rating, required List<String> team, String? project}) async {
    if (!const {1, 3, 7, 9}.contains(rating)) throw ArgumentError('rating must be 1, 3, 7 or 9');
    await _source.upsert('days', [
      {
        'work_date': day,
        'rating': rating,
        'team_members': team,
        if (project != null && project.isNotEmpty) 'project': project,
      },
    ], onConflict: 'user_id,work_date');
    return requestJob('full', {
      'work_date': day,
      'rating': rating,
      'team_members': team,
      if (project != null && project.isNotEmpty) 'project': project,
    });
  }

  Future<Job> requestJob(String kind, Json params) async {
    final rows = await _source.insert('generation_jobs', [
      {'kind': kind, 'params': params, 'requested_by': 'app'},
    ]);
    return Job.fromJson(rows.first);
  }

  /// Follows a job live until it finishes (or [timeout] passes), emitting each status change.
  ///
  /// Realtime wakes it as soon as the Mac updates the row; [fallback] re-reads it anyway in
  /// case a change was missed while the connection was down.
  Stream<Job> watchJob(String jobId, {Duration fallback = const Duration(seconds: 20), Duration timeout = const Duration(minutes: 8)}) async* {
    final deadline = _clock().add(timeout);
    final wake = StreamController<void>();
    final changes = _source
        .changes('generation_jobs')
        .where((change) => change.resync || change.record['id'] == jobId || change.oldRecord['id'] == jobId)
        .listen((_) => wake.add(null));
    final wakes = StreamIterator<void>(wake.stream);
    Timer? safety;
    String? lastStatus;
    try {
      while (true) {
        final rows = await _source.select('generation_jobs', where: [Filter.eq('id', jobId)], limit: 1);
        if (rows.isEmpty) throw StateError('job $jobId not found');
        final job = Job.fromJson(rows.first);
        if (job.status != lastStatus) {
          lastStatus = job.status;
          yield job;
        }
        if (job.finished) return;
        if (_clock().isAfter(deadline)) throw TimeoutException('the Mac did not finish in time', timeout);
        safety = Timer(fallback, () => wake.add(null));
        await wakes.moveNext();
        safety.cancel();
      }
    } finally {
      safety?.cancel();
      // Fire-and-forget: nothing depends on these finishing, and awaiting them would delay
      // (or, for close() without a listener, block) the end of the stream.
      unawaited(changes.cancel());
      unawaited(wakes.cancel());
      unawaited(wake.close());
    }
  }

  /// The newest unfinished job, so a draft in progress survives an app restart.
  Future<Job?> activeJob() async {
    final queued = await _source.select('generation_jobs', where: [const Filter.eq('status', 'queued')], orderBy: 'created_at', descending: true, limit: 1);
    final running = await _source.select('generation_jobs', where: [const Filter.eq('status', 'running')], orderBy: 'created_at', descending: true, limit: 1);
    final rows = [...running, ...queued];
    return rows.isEmpty ? null : Job.fromJson(rows.first);
  }

  /// The Mac's latest heartbeat, or null if it never connected.
  Future<DeviceStatus?> mac() async {
    final rows = await _source.select('devices', where: [const Filter.eq('kind', 'mac')], orderBy: 'last_seen_at', descending: true, limit: 1);
    return rows.isEmpty ? null : DeviceStatus.fromJson(rows.first);
  }

  Future<List<AppNotification>> unreadNotifications() async {
    final rows = await _source.select('notifications', orderBy: 'created_at', descending: true, limit: 20);
    return rows.map(AppNotification.fromJson).where((n) => n.readAt == null).toList(growable: false);
  }

  Future<void> markRead(AppNotification notification) async {
    await _source.update('notifications', {'read_at': _clock().toUtc().toIso8601String()}, where: [Filter.eq('id', notification.id)]);
  }

  Future<Snapshot<List<SessionSummary>>> sessions(String day) {
    return _cache.read<List<SessionSummary>>(
      userId: _uid,
      name: 'sessions.$day',
      load: () => _source.select('session_summaries', where: [Filter.eq('work_date', day)], orderBy: 'first_ts'),
      decode: (json) => ((json as List?)?.cast<Json>() ?? const []).map(SessionSummary.fromJson).toList(growable: false),
    );
  }

  /// Sessions hidden from digests (newest first).
  Future<List<HiddenSession>> hiddenSessions() async {
    final rows = await _source.select('hidden_sessions', orderBy: 'hidden_at', descending: true);
    return rows.map(HiddenSession.fromJson).toList(growable: false);
  }

  /// Keep a session (e.g. personal work) out of every digest; its data stays on the Mac.
  Future<void> hideSession(SessionSummary session) async {
    await _source.upsert('hidden_sessions', [
      {
        'session_key': session.sessionKey,
        'hidden_at': _clock().toUtc().toIso8601String(),
        'reason': '${session.title ?? session.headline} · ${session.workDate}',
      },
    ], onConflict: 'user_id,session_key');
  }

  Future<void> restoreSession(String sessionKey) =>
      _source.delete('hidden_sessions', where: [Filter.eq('session_key', sessionKey)]);

  Future<List<PullRequest>> pullRequests() async {
    final rows = await _source.select('pull_requests', orderBy: 'opened_at', descending: true, limit: 50);
    return rows.map(PullRequest.fromJson).toList(growable: false);
  }

  Future<List<Ticket>> tickets() async {
    final rows = await _source.select('tickets', orderBy: 'key', limit: 200);
    return rows.map(Ticket.fromJson).toList(growable: false);
  }
}
