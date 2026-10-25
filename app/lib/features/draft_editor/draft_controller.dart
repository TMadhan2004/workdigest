import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:work_tracker/data/models/digest_text.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/data/sources/snapshot_cache.dart';

/// The newest digest version for a day.
final AutoDisposeFutureProviderFamily<Snapshot<Digest?>, String> draftProvider = FutureProvider.autoDispose.family<Snapshot<Digest?>, String>(
  (ref, day) {
    refreshOn(ref, const ['digests']);
    return ref.watch(digestRepositoryProvider).latestForDay(day);
  },
);

/// A Mac job in progress, followed live until it finishes.
final AutoDisposeStreamProviderFamily<Job, String> jobProvider = StreamProvider.autoDispose.family<Job, String>(
  (ref, jobId) => ref.watch(workRepositoryProvider).watchJob(jobId),
);

/// Evidence lines behind a digest, loaded when the drawer first opens.
final AutoDisposeFutureProviderFamily<Map<String, EvidenceItem>, Digest> evidenceProvider =
    FutureProvider.autoDispose.family<Map<String, EvidenceItem>, Digest>((ref, digest) async {
  final items = await ref.watch(digestRepositoryProvider).evidence(digest);
  return {for (final item in items) item.evidenceId: item};
});

/// Actions on the draft. Each one returns what the UI needs to show next.
class DraftActions {
  DraftActions(this._ref);

  final Ref _ref;

  /// Replace one section's text and save it as a new edited version.
  Future<Digest> saveSection(Digest digest, int number, String block) async {
    final text = DigestText.parse(digest.text).replaceSection(number, block).join();
    return _ref.read(digestRepositoryProvider).saveEdited(digest, text);
  }

  /// Replace the closing paragraph and save it as a new edited version.
  Future<Digest> saveClosing(Digest digest, String closing) async {
    final text = DigestText.parse(digest.text).replaceClosing(closing).join();
    return _ref.read(digestRepositoryProvider).saveEdited(digest, text);
  }

  /// Ask the Mac to rewrite one section; returns the job to watch.
  Future<Job> regenerate(Digest digest, String sectionKey) =>
      _ref.read(workRepositoryProvider).requestJob('section', {'work_date': digest.workDate, 'section': sectionKey});

  /// Ask the Mac for a fresh draft of ``day`` (no rating change).
  Future<Job> generate(String day) => _ref.read(workRepositoryProvider).requestJob('full', {'work_date': day});

  Future<bool> share(Digest digest) =>
      _ref.read(shareServiceProvider).share(digest.text, subject: 'Daily digest – ${digest.header.dateText}');

  Future<void> copy(Digest digest) => _ref.read(shareServiceProvider).copy(digest.text);

  Future<void> markSent(Digest digest) => _ref.read(digestRepositoryProvider).markSent(digest);
}

final Provider<DraftActions> draftActionsProvider = Provider(DraftActions.new);
