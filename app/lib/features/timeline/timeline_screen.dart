import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/core/widgets/states.dart';
import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/data/sources/snapshot_cache.dart';
import 'package:work_tracker/features/asks_rules/asks_screen.dart';
import 'package:work_tracker/features/draft_editor/draft_controller.dart';

final AutoDisposeStateProvider<String?> timelineDayProvider = StateProvider.autoDispose<String?>((ref) => null);

/// Sessions hidden from digests (live).
final AutoDisposeFutureProvider<List<HiddenSession>> hiddenSessionsProvider = FutureProvider.autoDispose<List<HiddenSession>>((ref) {
  refreshOn(ref, const ['hidden_sessions']);
  return ref.watch(workRepositoryProvider).hiddenSessions();
});

/// The day's sessions without the hidden ones (live).
final AutoDisposeFutureProviderFamily<Snapshot<List<SessionSummary>>, String> sessionsProvider = FutureProvider.autoDispose.family<Snapshot<List<SessionSummary>>, String>(
  (ref, day) async {
    refreshOn(ref, const ['session_summaries', 'hidden_sessions']);
    final work = ref.watch(workRepositoryProvider);
    final snapshot = await work.sessions(day);
    var hidden = const <String>{};
    try {
      hidden = {for (final h in await work.hiddenSessions()) h.sessionKey};
    } on Object {
      // Offline: the cached list is shown as it was.
    }
    return Snapshot(
      snapshot.value.where((s) => !hidden.contains(s.sessionKey)).toList(growable: false),
      savedAt: snapshot.savedAt,
      stale: snapshot.stale,
    );
  },
);

/// My open PRs, live (the Mac refreshes them from Bitbucket every 30 minutes and on every draft).
final AutoDisposeFutureProvider<List<PullRequest>> openPullRequestsProvider = FutureProvider.autoDispose<List<PullRequest>>((ref) async {
  refreshOn(ref, const ['pull_requests']);
  final all = await ref.watch(workRepositoryProvider).pullRequests();
  return all.where((pr) => pr.state == 'OPEN').toList(growable: false);
});

/// The day's captured activity per session and repo, with source and repo filters.
class TimelineScreen extends ConsumerStatefulWidget {
  const TimelineScreen({super.key});

  @override
  ConsumerState<TimelineScreen> createState() => _TimelineScreenState();
}

class _TimelineScreenState extends ConsumerState<TimelineScreen> {
  String _source = 'all';
  String? _repo;

  /// Hidden on this screen before the cloud confirms (a dismissed row must leave at once).
  final Set<String> _hiddenNow = {};

  void _shift(int days) {
    final current = DateTime.parse(ref.read(timelineDayProvider) ?? ref.read(todayProvider));
    ref.read(timelineDayProvider.notifier).state = isoDay(current.add(Duration(days: days)));
    setState(() => _repo = null);
  }

  /// Hide a session from digests, with Undo. It disappears at once (and on every device).
  Future<void> _hide(SessionSummary session) async {
    final messenger = ScaffoldMessenger.of(context);
    final work = ref.read(workRepositoryProvider);
    setState(() => _hiddenNow.add(session.sessionKey));
    void showAgain() {
      if (mounted) setState(() => _hiddenNow.remove(session.sessionKey));
    }

    try {
      await work.hideSession(session);
      unawaited(Haptics.light());
      messenger.showSnackBar(
        SnackBar(
          content: const Text('Hidden from digests. Its data stays on your Mac.'),
          action: SnackBarAction(
            label: 'Undo',
            onPressed: () async {
              await work.restoreSession(session.sessionKey);
              showAgain();
            },
          ),
        ),
      );
    } on Object catch (error) {
      showAgain();
      messenger.showSnackBar(SnackBar(content: Text('Could not hide the session: $error')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final today = ref.watch(todayProvider);
    final day = ref.watch(timelineDayProvider) ?? today;
    final sessions = ref.watch(sessionsProvider(day));
    final theme = Theme.of(context);
    return Scaffold(
      body: Column(
        children: [
          const AuroraHeader(title: 'Timeline'),
          Expanded(
            child: sessions.when(
              loading: () => ListView(padding: pagePadding(context), children: const [SkeletonCard(), SizedBox(height: Space.m), SkeletonCard()]),
              error: (error, _) => ErrorState(message: '$error', onRetry: () => ref.invalidate(sessionsProvider(day))),
              data: (snapshot) {
                final repos = {for (final s in snapshot.value) ...s.repos}.toList()..sort();
                final visible = snapshot.value
                    .where((s) => _source == 'all' || s.source == _source)
                    .where((s) => _repo == null || s.repos.contains(_repo))
                    .where((s) => !_hiddenNow.contains(s.sessionKey))
                    .toList();
                return ListView(
                  padding: pagePadding(context),
                  children: [
                    // Day navigation lives in the body so it can shrink at large text sizes.
                    Row(
                      children: [
                        IconButton(tooltip: 'Previous day', icon: const Icon(Icons.chevron_left), onPressed: () => _shift(-1)),
                        Expanded(
                          child: Text(
                            DateFormat('EEEE d MMMM').format(DateTime.parse(day)),
                            style: theme.textTheme.titleMedium,
                            textAlign: TextAlign.center,
                          ),
                        ),
                        IconButton(
                          tooltip: 'Next day',
                          icon: const Icon(Icons.chevron_right),
                          onPressed: day == today ? null : () => _shift(1),
                        ),
                      ],
                    ),
                    const SizedBox(height: Space.s),
                    if (snapshot.stale) ...[StaleBanner(savedAt: snapshot.savedAt), const SizedBox(height: Space.m)],
                    _RedraftBanner(day: day),
                    Wrap(
                      spacing: Space.s,
                      runSpacing: Space.s,
                      children: [
                        for (final (value, label) in const [('all', 'All'), ('claude_code', 'Claude Code'), ('cursor', 'Cursor')])
                          ChoiceChip(label: Text(label), selected: _source == value, onSelected: (_) => setState(() => _source = value)),
                      ],
                    ),
                    if (repos.isNotEmpty)
                      // Full width with ellipsis: repo names are long and must not overflow at large text.
                      DropdownButton<String?>(
                        value: _repo,
                        isExpanded: true,
                        hint: const Text('All repos'),
                        onChanged: (value) => setState(() => _repo = value),
                        items: [
                          const DropdownMenuItem(child: Text('All repos')),
                          for (final repo in repos)
                            DropdownMenuItem(value: repo, child: Text(repo, overflow: TextOverflow.ellipsis)),
                        ],
                      ),
                    const SizedBox(height: Space.m),
                    if (visible.isEmpty)
                      const EmptyState(
                        icon: Icons.timeline,
                        title: 'No sessions',
                        message: 'Agent sessions summarised on the Mac appear here after the next sync.',
                      )
                    else
                      for (final (i, session) in visible.indexed) ...[
                        RevealIn(
                          key: ValueKey('reveal-${session.sessionKey}'),
                          delay: Motion.stagger * (i < 6 ? i : 6),
                          child: Dismissible(
                            key: ValueKey(session.sessionKey),
                            direction: DismissDirection.endToStart,
                            background: const _HideBackground(),
                            onDismissed: (_) => _hide(session),
                            child: _SessionCard(session: session, onHide: () => _hide(session)),
                          ),
                        ),
                        const SizedBox(height: Space.m),
                      ],
                    const _OpenPullRequests(),
                  ],
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _HideBackground extends StatelessWidget {
  const _HideBackground();

  @override
  Widget build(BuildContext context) {
    final colors = context.digestColors;
    return DecoratedBox(
      decoration: BoxDecoration(color: colors.missing, borderRadius: Radii.cardRadius),
      child: Align(
        alignment: Alignment.centerRight,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: Space.l),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.visibility_off_outlined, color: colors.onMissing),
              const SizedBox(width: Space.s),
              Text('Hide from digests', style: TextStyle(color: colors.onMissing, fontWeight: FontWeight.w700)),
            ],
          ),
        ),
      ),
    );
  }
}

/// Shown when the day's draft was written before a session was hidden.
class _RedraftBanner extends ConsumerWidget {
  const _RedraftBanner({required this.day});

  final String day;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final digest = ref.watch(draftProvider(day)).valueOrNull?.value;
    final hidden = ref.watch(hiddenSessionsProvider).valueOrNull ?? const [];
    final created = digest?.createdAt;
    if (digest == null || created == null || digest.isSent || !hidden.any((h) => h.hiddenAt.isAfter(created))) {
      return const SizedBox.shrink();
    }
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: Space.m),
      child: GlassCard(
        color: theme.colorScheme.primaryContainer,
        child: Row(
          children: [
            Expanded(
              child: Text(
                'This draft still includes a session you hid.',
                style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onPrimaryContainer),
              ),
            ),
            const SizedBox(width: Space.s),
            FilledButton(
              onPressed: () async {
                final job = await ref.read(draftActionsProvider).generate(day);
                if (context.mounted) context.go('/draft?day=$day&job=${job.id}');
              },
              child: const Text('Redraft'),
            ),
          ],
        ),
      ),
    );
  }
}

class _SessionCard extends StatelessWidget {
  const _SessionCard({required this.session, required this.onHide});

  final SessionSummary session;
  final VoidCallback onHide;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final time = DateFormat('HH:mm');
    final range = [session.firstTs, session.lastTs].whereType<DateTime>().map((t) => time.format(t.toLocal())).join('–');
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(Space.m),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(session.source == 'cursor' ? Icons.code : Icons.smart_toy_outlined, color: theme.colorScheme.primary),
                const SizedBox(width: Space.s),
                Expanded(child: Text(session.title ?? session.headline, style: theme.textTheme.titleMedium)),
                PopupMenuButton<String>(
                  tooltip: 'Session options',
                  onSelected: (_) => onHide(),
                  itemBuilder: (_) => const [
                    PopupMenuItem(
                      value: 'hide',
                      child: ListTile(leading: Icon(Icons.visibility_off_outlined), title: Text('Hide from digests'), subtitle: Text('e.g. personal work')),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: Space.xs),
            Text('${session.toolName} · $range${session.repos.isEmpty ? '' : ' · ${session.repos.join(', ')}'}', style: theme.textTheme.bodySmall),
            for (final item in session.done.take(5))
              Padding(padding: const EdgeInsets.only(top: Space.xs), child: Text('– $item', style: theme.textTheme.bodyMedium)),
            if (session.tests.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: Space.s),
                child: Text('Tests: ${session.tests.join('; ')}', style: theme.textTheme.bodySmall),
              ),
            const SizedBox(height: Space.xs),
            Text("Agent's own report; facts are checked in the draft", style: theme.textTheme.labelSmall),
          ],
        ),
      ),
    );
  }
}

class _OpenPullRequests extends ConsumerWidget {
  const _OpenPullRequests();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final prs = ref.watch(openPullRequestsProvider).valueOrNull ?? const <PullRequest>[];
    if (prs.isEmpty) return const SizedBox.shrink();
    final now = ref.watch(clockProvider)();
    final rules = PrExclusions.fromRules(ref.watch(rulesProvider).valueOrNull ?? const []);
    final shown = prs.where((pr) => !rules.excludes(pr, now)).toList();
    final hidden = prs.where((pr) => rules.excludes(pr, now)).toList();
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Open PRs', style: theme.textTheme.titleMedium),
        const SizedBox(height: Space.s),
        for (final pr in shown) ...[_PrCard(pr: pr, now: now), const SizedBox(height: Space.s)],
        if (shown.isEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: Space.s),
            child: Text('All your open PRs are hidden by your rules.', style: theme.textTheme.bodySmall),
          ),
        if (hidden.isNotEmpty)
          // Kept visible but folded away, so nothing disappears without a trace.
          Card(
            child: ExpansionTile(
              shape: const RoundedRectangleBorder(),
              leading: const Icon(Icons.visibility_off_outlined),
              title: Text('Hidden by your rules (${hidden.length})'),
              subtitle: const Text('Also left out of the digest'),
              children: [
                for (final pr in hidden)
                  Opacity(opacity: 0.6, child: _PrCard(pr: pr, now: now, flat: true)),
              ],
            ),
          ),
      ],
    );
  }
}

class _PrCard extends StatelessWidget {
  const _PrCard({required this.pr, required this.now, this.flat = false});

  final PullRequest pr;
  final DateTime now;
  final bool flat;

  @override
  Widget build(BuildContext context) {
    final tile = ListTile(
      title: Text('${pr.repo} #${pr.prId} ${pr.title}'),
      subtitle: Text(
        pr.firstApprovedAt == null
            ? 'Open ${now.difference(pr.openedAt).inDays} days, no approval yet'
            : 'Approved by ${pr.approvedBy.join(', ')}',
      ),
    );
    return flat ? tile : Card(child: tile);
  }
}
