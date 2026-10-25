import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/repositories/ids.dart';
import 'package:work_tracker/data/sources/cloud_source.dart';
import 'package:work_tracker/data/sources/snapshot_cache.dart';

/// Digest versions: the latest draft per day, history, edits, and the evidence behind statements.
class DigestRepository {
  DigestRepository(this._source, this._cache, {DateTime Function()? clock}) : _clock = clock ?? DateTime.now;

  final CloudSource _source;
  final SnapshotCache _cache;
  final DateTime Function() _clock;

  String get _uid => _source.userId ?? 'anonymous';

  /// The newest version for ``day`` (an edit beats the draft it came from), or null.
  Future<Snapshot<Digest?>> latestForDay(String day) {
    return _cache.read<Digest?>(
      userId: _uid,
      name: 'digest.$day',
      load: () => _source.select(
        'digests',
        where: [Filter.eq('work_date', day)],
        orderBy: 'created_at',
        descending: true,
        limit: 1,
      ),
      decode: (json) {
        final rows = (json as List?)?.cast<Json>() ?? const [];
        return rows.isEmpty ? null : Digest.fromJson(rows.first);
      },
    );
  }

  /// The newest version of each day in one month (for the History calendar), newest day first.
  Future<Snapshot<List<Digest>>> month(int year, int month) {
    final first = DateTime(year, month);
    final last = DateTime(year, month + 1, 0);
    return _cache.read<List<Digest>>(
      userId: _uid,
      name: 'history.${isoDay(first).substring(0, 7)}',
      load: () => _source.select(
        'digests',
        where: [Filter.gte('work_date', isoDay(first)), Filter.lte('work_date', isoDay(last))],
        orderBy: 'created_at',
        descending: true,
      ),
      decode: (json) {
        final byDay = <String, Digest>{};
        for (final row in (json as List?)?.cast<Json>() ?? const <Json>[]) {
          final digest = Digest.fromJson(row);
          byDay.putIfAbsent(digest.workDate, () => digest);
        }
        return byDay.values.toList()..sort((a, b) => b.workDate.compareTo(a.workDate));
      },
    );
  }

  /// Evidence lines for a digest (edited versions use their source draft's evidence).
  Future<List<EvidenceItem>> evidence(Digest digest) async {
    final rows = await _source.select('evidence', where: [Filter.eq('digest_id', digest.evidenceDigestId)]);
    return rows.map(EvidenceItem.fromJson).toList(growable: false);
  }

  /// Save an edited version; the Mac pulls it and keeps it as the user's version.
  Future<Digest> saveEdited(Digest base, String text) async {
    final structure = {...base.structureJson, 'source_digest_id': base.evidenceDigestId};
    final rows = await _source.insert('digests', [
      {
        'digest_id': newId('edit', _clock()),
        'work_date': base.workDate,
        'origin': 'edited',
        'status': 'draft',
        'text': text,
        'structure': structure,
        'gaps': [for (final gap in base.gaps) {'kind': gap.kind, 'message': gap.message, 'section': gap.section, 'ref': gap.ref}],
        'provider': base.provider,
      },
    ]);
    return Digest.fromJson(rows.first);
  }

  /// Record that this version was sent on WhatsApp (sending itself is always manual).
  Future<void> markSent(Digest digest) async {
    await _source.update('digests', {'status': 'sent_marked'}, where: [Filter.eq('digest_id', digest.digestId)]);
  }
}
