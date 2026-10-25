import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/core/widgets/states.dart';
import 'package:work_tracker/data/models/digest_text.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/data/sources/snapshot_cache.dart';
import 'package:work_tracker/features/draft_editor/draft_controller.dart';
import 'package:work_tracker/features/draft_editor/draft_widgets.dart';

/// Review, edit and share the digest for a day.
class DraftScreen extends ConsumerStatefulWidget {
  const DraftScreen({this.day, this.jobId, super.key});

  /// Work day `YYYY-MM-DD` (default: today).
  final String? day;

  /// A Mac job to wait for before showing the result (Close day, Regenerate).
  final String? jobId;

  @override
  ConsumerState<DraftScreen> createState() => _DraftScreenState();
}

class _DraftScreenState extends ConsumerState<DraftScreen> {
  String? _jobId;
  String? _busySection;
  bool _reveal = false;

  @override
  void initState() {
    super.initState();
    _jobId = widget.jobId;
  }

  @override
  void didUpdateWidget(DraftScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.jobId != oldWidget.jobId && widget.jobId != null) _jobId = widget.jobId;
  }

  String get _day => widget.day ?? ref.read(todayProvider);

  void _watchJob(String jobId, {String? section}) => setState(() {
    _jobId = jobId;
    _busySection = section;
  });

  void _onJobFinished(Job job) {
    if (!mounted) return;
    setState(() {
      _jobId = null;
      _busySection = null;
      _reveal = job.status == 'done';
    });
    ref.invalidate(draftProvider(_day));
    final messenger = ScaffoldMessenger.of(context);
    if (job.status == 'failed') {
      messenger.showSnackBar(SnackBar(content: Text('The Mac could not finish: ${job.error ?? 'unknown error'}')));
    } else {
      unawaited(Haptics.light());
    }
  }

  @override
  Widget build(BuildContext context) {
    final day = _day;
    final draft = ref.watch(draftProvider(day));
    if (_jobId != null) {
      ref.listen(jobProvider(_jobId!), (_, next) {
        final job = next.valueOrNull;
        if (job != null && job.finished) _onJobFinished(job);
      });
    }
    final job = _jobId == null ? null : ref.watch(jobProvider(_jobId!)).valueOrNull;
    final waitingForWholeDraft = _jobId != null && _busySection == null;

    return Scaffold(
      body: Column(
        children: [
          AuroraHeader(
            title: 'Draft · $day',
            subtitle: draft.valueOrNull?.value == null ? null : 'Tap a line to see its evidence',
            actions: [
              CircleIconButton(icon: Icons.refresh_rounded, tooltip: 'Refresh', onPressed: () => ref.invalidate(draftProvider(day))),
            ],
          ),
          Expanded(child: _body(day, draft, job, waitingForWholeDraft)),
        ],
      ),
      bottomNavigationBar: draft.valueOrNull?.value == null || waitingForWholeDraft
          ? null
          : _ShareBar(digest: draft.valueOrNull!.value!),
    );
  }

  Widget _body(String day, AsyncValue<Snapshot<Digest?>> draft, Job? job, bool waitingForWholeDraft) {
    return waitingForWholeDraft
          ? _GeneratingView(status: job?.status ?? 'queued')
          : draft.when(
              loading: () => ListView(
                padding: pagePadding(context),
                children: const [SkeletonCard(lines: 4), SizedBox(height: Space.m), SkeletonCard(), SizedBox(height: Space.m), SkeletonCard()],
              ),
              error: (error, _) => ErrorState(message: '$error', onRetry: () => ref.invalidate(draftProvider(day))),
              data: (snapshot) {
                final digest = snapshot.value;
                if (digest == null) {
                  return EmptyState(
                    icon: Icons.edit_note,
                    title: 'No draft for $day yet',
                    message: 'Close the day on the Today tab, or ask the Mac for a draft now.',
                    action: FilledButton.icon(
                      onPressed: () async {
                        final job = await ref.read(draftActionsProvider).generate(day);
                        _watchJob(job.id);
                      },
                      icon: const Icon(Icons.auto_awesome),
                      label: const Text('Draft it now'),
                    ),
                  );
                }
                return _DraftBody(
                  digest: digest,
                  savedAt: snapshot.stale ? snapshot.savedAt : null,
                  busySection: _busySection,
                  reveal: _reveal,
                  onRegenerate: (key) async {
                    final job = await ref.read(draftActionsProvider).regenerate(digest, key);
                    _watchJob(job.id, section: key);
                  },
                );
              },
            );
  }
}

class _GeneratingView extends StatelessWidget {
  const _GeneratingView({required this.status});

  final String status;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListView(
      padding: pagePadding(context),
      children: [
        Semantics(
          liveRegion: true,
          child: Card(
            color: theme.colorScheme.primaryContainer,
            child: ListTile(
              leading: const SizedBox(height: 24, width: 24, child: CircularProgressIndicator(strokeWidth: 2.5)),
              title: Text(status == 'running' ? 'The Mac is writing your draft…' : 'Waiting for the Mac to pick this up…'),
              subtitle: const Text('Usually about 3 minutes. You can leave this screen; the draft will be here.'),
            ),
          ),
        ),
        const SizedBox(height: Space.m),
        for (var i = 0; i < 4; i++) ...[SkeletonCard(lines: i.isEven ? 4 : 2), const SizedBox(height: Space.m)],
      ],
    );
  }
}

class _DraftBody extends ConsumerWidget {
  const _DraftBody({required this.digest, required this.busySection, required this.reveal, required this.onRegenerate, this.savedAt});

  final Digest digest;
  final DateTime? savedAt;
  final String? busySection;
  final bool reveal;
  final Future<void> Function(String key) onRegenerate;

  Future<void> _openEvidence(BuildContext context, Statement statement) => showModalBottomSheet<void>(
    context: context,
    // Above the floating nav bar (the tab's own navigator sits underneath it).
    useRootNavigator: true,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (_) => EvidenceSheet(digest: digest, statement: statement),
  );

  Future<void> _edit(BuildContext context, WidgetRef ref, DraftSection section) async {
    final blocks = DigestText.parse(digest.text);
    final current = blocks.sections[section.number];
    if (current == null) return;
    final edited = await showModalBottomSheet<String>(
      context: context,
      // Above the floating nav bar (the tab's own navigator sits underneath it).
      useRootNavigator: true,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => SectionEditorSheet(title: section.title, initial: current),
    );
    if (edited == null || edited.trim() == current.trim() || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(draftActionsProvider).saveSection(digest, section.number, edited);
      ref.invalidate(draftProvider(digest.workDate));
      messenger.showSnackBar(const SnackBar(content: Text('Saved as your version')));
    } on Object catch (error) {
      messenger.showSnackBar(SnackBar(content: Text('Could not save: $error')));
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final header = digest.header;
    var index = 0;
    Widget staggered(Widget child) {
      // A fresh draft streams in section by section; an opened one still cascades in briefly.
      final position = index++;
      final delay = reveal ? Motion.stagger * position : Motion.stagger * (position < 6 ? position : 6) ~/ 2;
      return RevealIn(delay: delay, child: child);
    }

    final problems = WhatsAppText.problems(digest.text);
    return ListView(
      padding: pagePadding(context),
      children: [
        if (savedAt != null) ...[StaleBanner(savedAt: savedAt!), const SizedBox(height: Space.m)],
        if (digest.origin == 'edited')
          Padding(
            padding: const EdgeInsets.only(bottom: Space.s),
            child: Text('Your edited version', style: theme.textTheme.labelLarge?.copyWith(color: theme.colorScheme.primary)),
          ),
        staggered(GapPanel(gaps: digest.gaps)),
        const SizedBox(height: Space.m),
        if (problems.isNotEmpty) ...[
          Card(
            color: theme.colorScheme.errorContainer,
            child: ListTile(
              leading: const Icon(Icons.warning_amber),
              title: const Text('WhatsApp formatting'),
              subtitle: Text(problems.join('\n')),
            ),
          ),
          const SizedBox(height: Space.m),
        ],
        staggered(
          Card(
            child: Padding(
              padding: const EdgeInsets.all(Space.m),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(header.dateText, style: theme.textTheme.titleMedium),
                  const SizedBox(height: Space.s),
                  for (final line in header.jira)
                    Padding(
                      padding: const EdgeInsets.only(bottom: Space.xs),
                      child: Text('${line.key} – ${line.title ?? '[title: confirm]'}', style: theme.textTheme.bodyMedium),
                    ),
                  const SizedBox(height: Space.s),
                  Text('Project: ${header.project ?? '—'}', style: theme.textTheme.bodySmall),
                  Text('Team: ${header.teamMembers.isEmpty ? '—' : header.teamMembers.join(', ')}', style: theme.textTheme.bodySmall),
                  Text('Day rating: ${header.rating ?? 'not given'}', style: theme.textTheme.bodySmall),
                  const Divider(height: Space.l),
                  Text('In short', style: theme.textTheme.labelLarge),
                  for (final statement in header.inShort)
                    StatementTile(statement: statement, onTap: () => _openEvidence(context, statement)),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(height: Space.m),
        for (final section in digest.sections) ...[
          staggered(
            SectionCard(
              section: section,
              busy: busySection == section.key,
              onStatementTap: (statement) => _openEvidence(context, statement),
              onEdit: () => _edit(context, ref, section),
              onRegenerate: section.regenerable && !digest.isSent ? () => onRegenerate(section.key) : null,
            ),
          ),
          const SizedBox(height: Space.m),
        ],
        if (digest.closing.isNotEmpty)
          staggered(
            Card(
              child: Padding(
                padding: const EdgeInsets.all(Space.m),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Closing', style: theme.textTheme.titleMedium),
                    for (final statement in digest.closing)
                      StatementTile(statement: statement, onTap: () => _openEvidence(context, statement)),
                  ],
                ),
              ),
            ),
          ),
        const SizedBox(height: Space.xxl),
      ],
    );
  }
}

class _ShareBar extends ConsumerWidget {
  const _ShareBar({required this.digest});

  final Digest digest;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final actions = ref.read(draftActionsProvider);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(Space.m, Space.s, Space.m, Space.s),
        child: Row(
          children: [
            IconButton.outlined(
              tooltip: 'Copy text',
              icon: const Icon(Icons.copy),
              onPressed: () async {
                await actions.copy(digest);
                unawaited(Haptics.light());
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Copied')));
                }
              },
            ),
            const SizedBox(width: Space.s),
            Expanded(
              child: FilledButton.icon(
                icon: const Icon(Icons.send),
                label: const Text('Share to WhatsApp'),
                onPressed: () async {
                  unawaited(Haptics.confirm());
                  final shared = await actions.share(digest);
                  if (!shared || !context.mounted || digest.isSent) return;
                  final mark = await showDialog<bool>(
                    context: context,
                    builder: (context) => AlertDialog(
                      title: const Text('Did you send it?'),
                      content: const Text('Mark this version as sent so History shows exactly what you sent.'),
                      actions: [
                        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Not yet')),
                        FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Mark as sent')),
                      ],
                    ),
                  );
                  if (mark ?? false) {
                    await actions.markSent(digest);
                    ref.invalidate(draftProvider(digest.workDate));
                  }
                },
              ),
            ),
            const SizedBox(width: Space.s),
            IconButton.outlined(
              tooltip: 'History',
              icon: const Icon(Icons.history),
              onPressed: () => context.go('/more'),
            ),
          ],
        ),
      ),
    );
  }
}
