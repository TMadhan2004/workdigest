import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:work_tracker/data/models/digest_text.dart';
import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/repositories/asks_repository.dart';
import 'package:work_tracker/data/repositories/digest_repository.dart';
import 'package:work_tracker/data/repositories/ids.dart';
import 'package:work_tracker/data/repositories/work_repository.dart';
import 'package:work_tracker/data/sources/snapshot_cache.dart';

import '../fakes/fakes.dart';

Future<SnapshotCache> _cache({DateTime? now}) async {
  SharedPreferences.setMockInitialValues({});
  return SnapshotCache(await SharedPreferences.getInstance(), clock: () => now ?? fixedNow);
}

void main() {
  group('models', () {
    test('TC-APP-001: the real 23 Oct draft parses into header, 11 sections, statements and ranked gaps', () {
      // Given: the digest row the Mac pushed for 23 Oct (real data, redacted).
      // When: Digest.fromJson parses it.
      // Then: 11 sections in template order, Jira lines with URLs, gaps sorted most important first.
      final digest = Digest.fromJson(fixture('digest_2026-10-23.json'));
      expect(digest.sections.map((s) => s.number), List.generate(11, (i) => i + 1));
      expect(digest.sections.first.key, 'done_today');
      expect(digest.header.jira, isNotEmpty);
      expect(digest.header.jira.every((j) => j.url.startsWith('https://ei-study.atlassian.net/browse/')), isTrue);
      expect(digest.gaps.first.rank, lessThanOrEqualTo(digest.gaps.last.rank));
      expect(digest.text, startsWith('Greetings Priya,'));
      expect(digest.sections.firstWhere((s) => s.key == 'prs').regenerable, isFalse);
    });

    test('TC-APP-002: statement, ask, device and settings helpers behave', () {
      // Given: hand-built model values.
      // When: their derived getters are read.
      // Then: placeholder/unverified flags, open asks, online window and transmit defaults are correct.
      const placeholder = Statement(text: 'Atomic-commit check: [missing: result]');
      const unverified = Statement(text: 'UI 999/999', problems: ['number 999']);
      expect(placeholder.isPlaceholder, isTrue);
      expect(unverified.isUnverified, isTrue);
      const ask = Ask(askId: 'a', text: 't', kind: 'question', status: 'waiting', openedOn: '2026-10-21');
      expect(ask.isOpen, isTrue);
      final mac = DeviceStatus(deviceId: 'm', kind: 'mac', lastSeenAt: fixedNow.subtract(const Duration(seconds: 30)));
      expect(mac.isOnline(fixedNow), isTrue);
      expect(mac.isOnline(fixedNow.add(const Duration(minutes: 5))), isFalse);
      final settings = AppSettings.fromJson(const {'nudge_time': '20:00:00', 'transmit': {'file_paths': false}});
      expect(settings.nudgeTime, '20:00');
      expect(settings.transmitOn('file_paths'), isFalse);
      expect(settings.transmitOn('prompt_excerpts'), isTrue);
      expect(Gap.fromJson(const {'kind': 'real_action', 'message': 'm'}).label, 'Needs real action');
    });

    test('TC-APP-003: JSON helpers tolerate missing and mistyped fields', () {
      // Given: a row with nulls, numbers as strings and wrong types.
      // When: the helpers read it.
      // Then: they return the documented fallbacks instead of throwing.
      final row = <String, dynamic>{'a': null, 'n': '7', 'l': 'not a list', 'o': 3};
      expect(str(row, 'a', 'x'), 'x');
      expect(intOf(row, 'n'), 7);
      expect(strings(row, 'l'), isEmpty);
      expect(object(row, 'o'), isEmpty);
      expect(isoDay(DateTime(2026, 3, 4)), '2026-03-04');
    });
  });

  group('DigestText', () {
    final text = str(fixture('digest_2026-10-23.json'), 'text');

    test('TC-APP-004: the real WhatsApp text splits into header, 11 sections and the closing', () {
      // Given: the real 23 Oct WhatsApp text.
      // When: DigestText.parse runs.
      // Then: 11 numbered blocks, a header with the date line, and a non-empty closing.
      final blocks = DigestText.parse(text);
      expect(blocks.sections.keys, List.generate(11, (i) => i + 1));
      expect(blocks.header, contains('Daily digest – Fri, 23 Oct 2026'));
      expect(blocks.sections[7], startsWith('7. Releases and deployment'));
      expect(blocks.closing, isNotEmpty);
    });

    test('TC-APP-005: replacing one section leaves every other block byte-for-byte unchanged', () {
      // Given: the real text.
      // When: section 5 is replaced and the text is joined.
      // Then: only section 5 differs; header, other sections and closing are identical.
      final before = DigestText.parse(text);
      final after = DigestText.parse(before.replaceSection(5, '5. Risks\n   1. New risk – owner: me, by 27 Oct').join());
      expect(after.sections[5], '5. Risks\n   1. New risk – owner: me, by 27 Oct');
      for (final n in before.sections.keys.where((n) => n != 5)) {
        expect(after.sections[n], before.sections[n], reason: 'section $n changed');
      }
      expect(after.header.trimRight(), before.header.trimRight());
      expect(after.closing, before.closing);
      expect(() => before.replaceSection(42, 'x'), throwsArgumentError);
    });

    test('TC-APP-006: WhatsApp formatting problems are detected', () {
      // Given: text with a # heading, **bold**, a table row and a Markdown link; then clean text.
      // When: WhatsAppText.problems runs.
      // Then: four problems for the first, none for the second.
      expect(WhatsAppText.problems('# Title\n**b**\n| a | b |\n[pr](https://x)'), hasLength(4));
      expect(WhatsAppText.problems('*bold* and https://x\n1. item'), isEmpty);
    });
  });

  test('TC-APP-007: the work day keeps late-night work on the previous day', () {
    // Given: 01:30 and 04:30 on 24 Oct.
    // When: workDay() runs with the 04:00 boundary.
    // Then: 23 Oct and 24 Oct; ids are unique and prefixed.
    expect(workDay(DateTime(2026, 10, 24, 1, 30)), DateTime(2026, 10, 23));
    expect(workDay(DateTime(2026, 10, 24, 4, 30)), DateTime(2026, 10, 24));
    final a = newId('edit', fixedNow);
    final b = newId('edit', fixedNow);
    expect(a, startsWith('edit-'));
    expect(a, isNot(b));
  });

  group('SnapshotCache', () {
    test('TC-APP-008: fresh reads are cached; offline reads return the cached copy marked stale', () async {
      // Given: one successful load, then a failing one.
      // When: read() runs twice.
      // Then: the first is fresh, the second returns the same value with stale=true and the save time.
      final cache = await _cache();
      final fresh = await cache.read<int>(userId: 'u', name: 'n', load: () async => 5, decode: (j) => j! as int);
      final offline = await cache.read<int>(userId: 'u', name: 'n', load: () async => throw StateError('offline'), decode: (j) => j! as int);
      expect((fresh.value, fresh.stale), (5, false));
      expect((offline.value, offline.stale, offline.savedAt), (5, true, fixedNow));
    });

    test('TC-APP-009: offline with nothing cached rethrows; clear() removes only that user', () async {
      // Given: an empty cache, then cached data for two users.
      // When: an offline read runs, then clear('u') is called.
      // Then: the read throws, and only user u's entries are gone.
      final cache = await _cache();
      expect(() => cache.read<int>(userId: 'u', name: 'x', load: () async => throw StateError('offline'), decode: (j) => j! as int), throwsStateError);
      await cache.read<int>(userId: 'u', name: 'a', load: () async => 1, decode: (j) => j! as int);
      await cache.read<int>(userId: 'v', name: 'a', load: () async => 2, decode: (j) => j! as int);
      await cache.clear('u');
      expect(() => cache.read<int>(userId: 'u', name: 'a', load: () async => throw StateError('offline'), decode: (j) => j! as int), throwsStateError);
      final other = await cache.read<int>(userId: 'v', name: 'a', load: () async => throw StateError('offline'), decode: (j) => j! as int);
      expect(other.value, 2);
    });
  });

  group('repositories', () {
    test('TC-APP-010: closing the day stores rating/team/project and queues one full draft job', () async {
      // Given: a cloud with no day row.
      // When: closeDay(rating 7, team [Rahul], project) runs; then with rating 5.
      // Then: the day row and a queued 'full' job with the same params exist; rating 5 is rejected.
      final cloud = FakeCloudSource(clock: () => fixedNow);
      final repo = WorkRepository(cloud, await _cache(), clock: () => fixedNow);
      final job = await repo.closeDay('2026-10-23', rating: 7, team: ['Rahul'], project: 'Consolidation');
      expect(cloud.tables['days']!.single, containsPair('rating', 7));
      expect(job.kind, 'full');
      expect(job.params, {'work_date': '2026-10-23', 'rating': 7, 'team_members': ['Rahul'], 'project': 'Consolidation'});
      expect(() => repo.closeDay('2026-10-23', rating: 5, team: const []), throwsArgumentError);
    });

    test('TC-APP-011: watchJob follows the job live, with a slow safety re-read, and emits each status once', () {
      // Given: a queued job.
      // When: the Mac marks it running (delivered live), then done without a live event (a missed change).
      // Then: running appears at once, done appears after the 20 s safety re-read, and the stream completes.
      fakeAsync((async) {
        final cloud = FakeCloudSource(clock: () => fixedNow)..seed('generation_jobs', [{'id': 'j1', 'kind': 'full', 'status': 'queued'}]);
        final repo = WorkRepository(cloud, SnapshotCache(_MemoryPrefs.instance), clock: () => fixedNow);
        final statuses = <String>[];
        var done = false;
        repo.watchJob('j1').listen((j) => statuses.add(j.status), onDone: () => done = true);
        async.flushMicrotasks();
        cloud.macWrites('generation_jobs', [{'id': 'j1', 'status': 'running'}]);
        async.flushMicrotasks();
        expect(statuses, ['queued', 'running']);
        cloud.tables['generation_jobs']!.single['status'] = 'done';
        async.elapse(const Duration(seconds: 19));
        expect(statuses, ['queued', 'running']);
        async.elapse(const Duration(seconds: 1));
        expect(statuses, ['queued', 'running', 'done']);
        expect(done, isTrue);
      });
    });

    test('TC-APP-105: the Mac is online only with a fresh heartbeat and not after it says offline', () {
      // Given: heartbeats at various ages and statuses.
      // When: isOnline is evaluated at 18:00.
      // Then: 59 s old is online, 61 s old is offline, and status "offline" is offline even if fresh.
      DeviceStatus mac(Duration age, [String status = 'idle']) =>
          DeviceStatus(deviceId: 'm', kind: 'mac', lastSeenAt: fixedNow.subtract(age), status: status);
      expect(mac(const Duration(seconds: 59)).isOnline(fixedNow), isTrue);
      expect(mac(const Duration(seconds: 61)).isOnline(fixedNow), isFalse);
      expect(mac(Duration.zero, 'offline').isOnline(fixedNow), isFalse);
      expect(mac(Duration.zero, 'drafting').busy, isTrue);
    });

    test('TC-APP-012: ask status changes enforce evidence and reason, and are marked as app edits', () async {
      // Given: an open ask.
      // When: done without evidence, disagree without reason, then done with evidence.
      // Then: the first two throw; the last stores status, evidence, closed_on and updated_by=app.
      final cloud = seededCloud();
      final repo = AsksRepository(cloud, await _cache(), clock: () => fixedNow);
      final ask = (await repo.asks()).value.firstWhere((a) => a.askId == 'a-otp');
      expect(() => repo.setStatus(ask, 'done'), throwsArgumentError);
      expect(() => repo.setStatus(ask, 'disagree', reason: ' '), throwsArgumentError);
      final done = await repo.setStatus(ask, 'done', evidence: 'https://ei-study.atlassian.net/browse/EIT-9999');
      expect(done.status, 'done');
      final row = cloud.tables['asks']!.firstWhere((r) => r['ask_id'] == 'a-otp');
      expect((row['updated_by'], row['closed_on']), ('app', '2026-10-23'));
    });

    test('TC-APP-013: a pasted reply is stored for the Mac; reviewed items are written back as app edits', () async {
      // Given: an empty cloud.
      // When: submitReply runs, the "Mac" adds items, and reviewItems confirms one and dismisses one.
      // Then: the reply is source=app and unprocessed; items carry the chosen statuses and updated_by=app.
      final cloud = FakeCloudSource(clock: () => fixedNow);
      final repo = AsksRepository(cloud, await _cache(), clock: () => fixedNow);
      expect(() => repo.submitReply('   '), throwsArgumentError);
      final reply = await repo.submitReply('Hi Arjun, please raise the OTP bug.');
      expect(cloud.tables['manager_replies']!.single, allOf(containsPair('source', 'app'), containsPair('processed', false)));
      cloud.seed('reply_items', [
        {'item_id': 'i0', 'reply_id': reply.replyId, 'idx': 0, 'type': 'one_time_action', 'text': 'Raise the OTP bug', 'status': 'proposed'},
        {'item_id': 'i1', 'reply_id': reply.replyId, 'idx': 1, 'type': 'feedback', 'text': 'Good work', 'status': 'proposed'},
      ]);
      final items = await repo.items(reply.replyId);
      await repo.reviewItems([items[0].copyWith(status: 'confirmed'), items[1].copyWith(status: 'dismissed', type: 'info_only')]);
      final stored = cloud.tables['reply_items']!;
      expect(stored.map((r) => (r['status'], r['updated_by'])), [('confirmed', 'app'), ('dismissed', 'app')]);
      expect(stored[1]['type'], 'info_only');
    });

    test('TC-APP-014: an edited version keeps the structure and points at the source draft for evidence', () async {
      // Given: the real 23 Oct draft.
      // When: saveEdited stores new text, and the edited version is edited again.
      // Then: both versions are origin=edited with source_digest_id = the original draft id;
      //       latestForDay returns the newest; the month view shows one entry per day.
      final cloud = seededCloud();
      final repo = DigestRepository(cloud, await _cache(), clock: () => fixedNow);
      final original = (await repo.latestForDay('2026-10-23')).value!;
      final first = await repo.saveEdited(original, '${original.text}\nEdited once');
      final second = await repo.saveEdited(first, '${original.text}\nEdited twice');
      expect(first.evidenceDigestId, original.digestId);
      expect(second.evidenceDigestId, original.digestId);
      final latest = (await repo.latestForDay('2026-10-23')).value!;
      expect(latest.text, endsWith('Edited twice'));
      expect(latest.origin, 'edited');
      expect((await repo.month(2026, 10)).value, hasLength(1));
      expect(await repo.evidence(latest), hasLength(176));
    });
  });
}

/// SharedPreferences cannot be awaited inside fakeAsync, so this test uses a tiny in-memory store.
class _MemoryPrefs implements SharedPreferences {
  static final _MemoryPrefs instance = _MemoryPrefs();
  final Map<String, Object> _data = {};

  @override
  String? getString(String key) => _data[key] as String?;

  @override
  Future<bool> setString(String key, String value) async {
    _data[key] = value;
    return true;
  }

  @override
  Set<String> getKeys() => _data.keys.toSet();

  @override
  Future<bool> remove(String key) async => _data.remove(key) != null;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
