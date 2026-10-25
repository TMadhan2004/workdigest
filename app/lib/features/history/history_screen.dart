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

/// The month shown in the calendar (first day of the month); null means the current month.
final AutoDisposeStateProvider<DateTime?> historyMonthProvider = StateProvider.autoDispose<DateTime?>((ref) => null);

/// One month of digests (newest version per day), live.
final AutoDisposeFutureProviderFamily<Snapshot<List<Digest>>, DateTime> monthDigestsProvider =
    FutureProvider.autoDispose.family<Snapshot<List<Digest>>, DateTime>((ref, month) {
      refreshOn(ref, const ['digests']);
      return ref.watch(digestRepositoryProvider).month(month.year, month.month);
    });

final AutoDisposeFutureProvider<List<ManagerReply>> repliesProvider = FutureProvider.autoDispose<List<ManagerReply>>((ref) {
  refreshOn(ref, const ['manager_replies']);
  return ref.watch(asksRepositoryProvider).replies();
});

/// What a day's digest is: sent, an edited draft, or a draft.
enum DigestState {
  sent('Sent'),
  importedSent('Sent (imported)'),
  edited('Edited draft'),
  draft('Draft');

  const DigestState(this.label);

  final String label;

  static DigestState of(Digest digest) => switch ((digest.origin, digest.status)) {
    (_, 'sent_marked') => sent,
    ('imported', _) => importedSent,
    ('edited', _) => edited,
    _ => draft,
  };

  bool get isSent => this == sent || this == importedSent;

  Color color(BuildContext context) => switch (this) {
    sent || importedSent => context.aurora.online,
    edited => Theme.of(context).colorScheme.primary,
    draft => context.digestColors.onUnverified,
  };
}

/// Previous digests on a month calendar, and the manager's replies.
class HistoryScreen extends ConsumerWidget {
  const HistoryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const DefaultTabController(
      length: 2,
      child: Scaffold(
        body: Column(
          children: [
            AuroraHeader(
              title: 'History',
              bottom: TabBar(tabs: [Tab(text: 'Digests'), Tab(text: 'Replies')]),
            ),
            Expanded(child: TabBarView(children: [_Digests(), _Replies()])),
          ],
        ),
      ),
    );
  }
}

class _Digests extends ConsumerWidget {
  const _Digests();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final now = ref.watch(clockProvider)();
    final current = DateTime(now.year, now.month);
    final month = ref.watch(historyMonthProvider) ?? current;
    final digests = ref.watch(monthDigestsProvider(month));
    final theme = Theme.of(context);
    void shift(int months) {
      final next = DateTime(month.year, month.month + months);
      if (next.isAfter(current)) return;
      ref.read(historyMonthProvider.notifier).state = next;
    }

    final list = digests.valueOrNull?.value ?? const <Digest>[];
    return ListView(
      padding: pagePadding(context, top: Space.m),
      children: [
        if (digests.valueOrNull?.stale ?? false) ...[StaleBanner(savedAt: digests.value!.savedAt), const SizedBox(height: Space.m)],
        GestureDetector(
          // Swipe left for the next month, right for the previous one.
          onHorizontalDragEnd: (details) {
            final velocity = details.primaryVelocity ?? 0;
            if (velocity.abs() > 200) shift(velocity < 0 ? 1 : -1);
          },
          child: GlassCard(
            padding: const EdgeInsets.fromLTRB(Space.s, Space.s, Space.s, Space.m),
            child: Column(
              children: [
                Row(
                  children: [
                    IconButton(tooltip: 'Previous month', icon: const Icon(Icons.chevron_left_rounded), onPressed: () => shift(-1)),
                    Expanded(
                      child: Text(DateFormat('MMMM yyyy').format(month), textAlign: TextAlign.center, style: theme.textTheme.titleMedium),
                    ),
                    IconButton(
                      tooltip: 'Next month',
                      icon: const Icon(Icons.chevron_right_rounded),
                      onPressed: month == current ? null : () => shift(1),
                    ),
                  ],
                ),
                const SizedBox(height: Space.s),
                AnimatedSwitcher(
                  duration: Motion.of(context, Motion.standard),
                  child: digests.isLoading && digests.valueOrNull == null
                      ? const SizedBox(height: 280, child: Center(child: CircularProgressIndicator()))
                      : digests.hasError && digests.valueOrNull == null
                      ? ErrorState(message: '${digests.error}', onRetry: () => ref.invalidate(monthDigestsProvider(month)))
                      : MonthCalendar(key: ValueKey(month), month: month, today: now, digests: list),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: Space.m),
        const _Legend(),
        const SizedBox(height: Space.l),
        if (list.isEmpty && !digests.isLoading)
          EmptyState(icon: Icons.calendar_month_outlined, title: 'No digests in ${DateFormat('MMMM').format(month)}', message: 'Imported and generated digests appear here.')
        else
          for (final (i, digest) in list.indexed) ...[
            RevealIn(key: ValueKey(digest.digestId), delay: Motion.stagger * (i < 6 ? i : 6), child: _DigestTile(digest: digest)),
            const SizedBox(height: Space.s),
          ],
      ],
    );
  }
}

/// A month grid (Monday first) with a dot for each day that has a digest.
class MonthCalendar extends StatelessWidget {
  const MonthCalendar({required this.month, required this.today, required this.digests, super.key});

  final DateTime month;
  final DateTime today;
  final List<Digest> digests;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final byDay = {for (final d in digests) d.workDate: d};
    final first = DateTime(month.year, month.month);
    final days = DateTime(month.year, month.month + 1, 0).day;
    final leading = first.weekday - 1; // Monday = 0
    final cells = leading + days;
    final rows = (cells / 7).ceil();
    final weekdays = DateFormat.E().dateSymbols.STANDALONENARROWWEEKDAYS;
    return Column(
      children: [
        Row(
          children: [
            for (var i = 0; i < 7; i++)
              Expanded(
                child: Text(
                  weekdays[(i + 1) % 7],
                  textAlign: TextAlign.center,
                  style: theme.textTheme.labelMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
              ),
          ],
        ),
        const SizedBox(height: Space.s),
        for (var row = 0; row < rows; row++)
          Row(
            children: [
              for (var col = 0; col < 7; col++)
                Expanded(
                  child: Builder(
                    builder: (context) {
                      final index = row * 7 + col - leading;
                      if (index < 0 || index >= days) return const SizedBox(height: 52);
                      final date = DateTime(month.year, month.month, index + 1);
                      return _DayCell(
                        date: date,
                        digest: byDay[isoDay(date)],
                        isToday: isoDay(date) == isoDay(today),
                      );
                    },
                  ),
                ),
            ],
          ),
      ],
    );
  }
}

class _DayCell extends StatelessWidget {
  const _DayCell({required this.date, required this.digest, required this.isToday});

  final DateTime date;
  final Digest? digest;
  final bool isToday;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final aurora = context.aurora;
    final digest = this.digest;
    final state = digest == null ? null : DigestState.of(digest);
    final label = DateFormat('EEE d MMM').format(date) + (state == null ? ', no digest' : ', ${state.label.toLowerCase()}');
    final number = Text(
      '${date.day}',
      style: theme.textTheme.bodyMedium?.copyWith(
        fontWeight: state != null || isToday ? FontWeight.w800 : FontWeight.w500,
        color: state == null ? theme.colorScheme.onSurfaceVariant : theme.colorScheme.onSurface,
      ),
    );
    final cell = SizedBox(
      height: 52,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          DecoratedBox(
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              border: isToday ? Border.all(color: aurora.accentGradient.first, width: 1.5) : null,
              color: state?.color(context).withValues(alpha: 0.16),
            ),
            child: SizedBox.square(dimension: 34, child: Center(child: number)),
          ),
          const SizedBox(height: 3),
          Container(
            width: 5,
            height: 5,
            decoration: BoxDecoration(shape: BoxShape.circle, color: state?.color(context) ?? Colors.transparent),
          ),
        ],
      ),
    );
    // container: each day is its own node (otherwise all days merge into the swipeable card).
    if (digest == null) return Semantics(container: true, label: label, excludeSemantics: true, child: cell);
    return Semantics(
      container: true,
      button: true,
      label: label,
      excludeSemantics: true,
      onTap: () => context.go('/draft?day=${digest.workDate}'),
      child: InkResponse(onTap: () => context.go('/draft?day=${digest.workDate}'), radius: 26, child: cell),
    );
  }
}

class _Legend extends StatelessWidget {
  const _Legend();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    Widget item(DigestState state, String label) => Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        LiveDot(color: state.color(context), size: 8),
        const SizedBox(width: Space.xs + 2),
        Text(label, style: theme.textTheme.labelMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
      ],
    );
    return Wrap(
      alignment: WrapAlignment.center,
      spacing: Space.l,
      runSpacing: Space.s,
      children: [item(DigestState.sent, 'Sent'), item(DigestState.edited, 'Edited'), item(DigestState.draft, 'Draft')],
    );
  }
}

class _DigestTile extends StatelessWidget {
  const _DigestTile({required this.digest});

  final Digest digest;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.digestColors;
    final state = DigestState.of(digest);
    return GlassCard(
      onTap: () => context.go('/draft?day=${digest.workDate}'),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(DateFormat('EEE d MMM yyyy').format(DateTime.parse(digest.workDate)), style: theme.textTheme.titleSmall),
                Text(
                  '${digest.sections.length} sections · ${digest.blockingGaps} gaps',
                  style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
              ],
            ),
          ),
          StatusChip(
            label: state.label,
            background: state.isSent ? colors.verified : theme.colorScheme.secondaryContainer,
            foreground: state.isSent ? colors.onVerified : theme.colorScheme.onSecondaryContainer,
          ),
        ],
      ),
    );
  }
}

class _Replies extends ConsumerWidget {
  const _Replies();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final replies = ref.watch(repliesProvider);
    final theme = Theme.of(context);
    return replies.when(
      loading: () => ListView(padding: pagePadding(context, top: Space.m), children: const [SkeletonCard()]),
      error: (error, _) => ErrorState(message: '$error', onRetry: () => ref.invalidate(repliesProvider)),
      data: (list) => list.isEmpty
          ? EmptyState(
              icon: Icons.forum_outlined,
              title: 'No replies yet',
              message: '${capitalizeFirst(ref.watch(managerPossessiveProvider))} replies appear here once added.',
            )
          : ListView.separated(
              padding: pagePadding(context, top: Space.m),
              itemCount: list.length,
              separatorBuilder: (_, _) => const SizedBox(height: Space.s),
              itemBuilder: (context, index) {
                final reply = list[index];
                return Card(
                  child: ExpansionTile(
                    shape: const RoundedRectangleBorder(),
                    title: Text(reply.forWorkDate == null ? 'Reply' : 'Reply to ${reply.forWorkDate}'),
                    subtitle: Text(reply.rawText.split('\n').firstWhere((l) => l.trim().isNotEmpty, orElse: () => '')),
                    children: [
                      Padding(
                        padding: const EdgeInsets.fromLTRB(Space.m, 0, Space.m, Space.m),
                        child: SelectableText(reply.rawText, style: theme.textTheme.bodyMedium),
                      ),
                    ],
                  ),
                );
              },
            ),
    );
  }
}
