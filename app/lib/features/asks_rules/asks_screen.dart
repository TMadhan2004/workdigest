import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/core/widgets/states.dart';
import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/data/sources/snapshot_cache.dart';
import 'package:work_tracker/features/asks_rules/my_rules.dart';

final AutoDisposeFutureProvider<Snapshot<List<Ask>>> asksProvider = FutureProvider.autoDispose<Snapshot<List<Ask>>>((ref) {
  refreshOn(ref, const ['asks']);
  return ref.watch(asksRepositoryProvider).asks();
});

final AutoDisposeFutureProvider<List<StandingRule>> rulesProvider = FutureProvider.autoDispose<List<StandingRule>>((ref) {
  refreshOn(ref, const ['standing_rules']);
  return ref.watch(asksRepositoryProvider).rules();
});

class AsksScreen extends ConsumerWidget {
  const AsksScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const DefaultTabController(
      length: 3,
      child: Scaffold(
        body: Column(
          children: [
            AuroraHeader(
              title: 'Asks & rules',
              bottom: TabBar(tabs: [Tab(text: 'Open'), Tab(text: 'Closed'), Tab(text: 'Rules')]),
            ),
            Expanded(
              child: TabBarView(
                children: [
                  _AskList(open: true),
                  _AskList(open: false),
                  _RuleList(),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Asks deleted on this screen before the cloud confirms (a swiped card must leave at once).
final AutoDisposeStateProvider<Set<String>> deletingAsksProvider = StateProvider.autoDispose<Set<String>>((ref) => const {});

/// Delete one open ask (it is cleared: never reported), with Undo.
Future<void> deleteAsk(BuildContext context, WidgetRef ref, Ask ask) async {
  final messenger = ScaffoldMessenger.of(context);
  final repo = ref.read(asksRepositoryProvider);
  final deleting = ref.read(deletingAsksProvider.notifier);
  void showAgain() => deleting.state = {...deleting.state}..remove(ask.askId);
  deleting.state = {...deleting.state, ask.askId};
  try {
    await repo.clearAsk(ask);
    unawaited(Haptics.light());
    messenger.showSnackBar(
      SnackBar(
        content: const Text('Ask deleted. It will not appear in any digest.'),
        action: SnackBarAction(
          label: 'Undo',
          onPressed: () async {
            await repo.restoreAsk(ask);
            showAgain();
          },
        ),
      ),
    );
  } on Object catch (error) {
    showAgain();
    messenger.showSnackBar(SnackBar(content: Text('Could not delete the ask: $error')));
  }
}

/// The Closed tab's day filter (`YYYY-MM-DD`), or null for every day.
final AutoDisposeStateProvider<String?> closedDayProvider = StateProvider.autoDispose<String?>((ref) => null);

class _AskList extends ConsumerWidget {
  const _AskList({required this.open});

  final bool open;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final asks = ref.watch(asksProvider);
    final day = open ? null : ref.watch(closedDayProvider);
    final deleting = ref.watch(deletingAsksProvider);
    return RefreshIndicator(
      onRefresh: () => ref.refresh(asksProvider.future),
      child: asks.when(
        loading: () => ListView(padding: pagePadding(context), children: const [SkeletonCard(), SizedBox(height: Space.m), SkeletonCard()]),
        error: (error, _) => ListView(children: [ErrorState(message: '$error', onRetry: () => ref.invalidate(asksProvider))]),
        data: (snapshot) {
          // Closed = answered (Done or Disagree); cleared asks are gone for good.
          final all = snapshot.value
              .where((a) => open ? a.isOpen && !deleting.contains(a.askId) : const {'done', 'disagree'}.contains(a.status))
              .toList()
            ..sort((a, b) => open ? _openOrder(a, b) : (b.closedOn ?? '').compareTo(a.closedOn ?? ''));
          final list = day == null ? all : all.where((a) => a.closedOn == day).toList();
          return ListView(
            padding: pagePadding(context, top: Space.m),
            children: [
              if (snapshot.stale) ...[StaleBanner(savedAt: snapshot.savedAt), const SizedBox(height: Space.m)],
              if (open && all.isNotEmpty) _OpenHeader(count: all.length),
              if (!open && all.isNotEmpty) _ClosedDayFilter(closed: all),
              if (all.isNotEmpty) const SizedBox(height: Space.m),
              if (list.isEmpty)
                EmptyState(
                  icon: open ? Icons.task_alt : Icons.inbox_outlined,
                  title: open ? 'No open asks' : (day == null ? 'Nothing closed yet' : 'Nothing closed that day'),
                  message: open
                      ? '${capitalizeFirst(ref.watch(managerPossessiveProvider))} asks appear here after you add a reply.'
                      : 'Asks you mark Done or Disagree appear here.',
                )
              else
                for (final (i, ask) in list.indexed) ...[
                  RevealIn(
                    key: ValueKey(ask.askId),
                    delay: Motion.stagger * (i < 8 ? i : 8),
                    child: open
                        ? Dismissible(
                            key: ValueKey('swipe-${ask.askId}'),
                            direction: DismissDirection.endToStart,
                            background: const _DeleteBackground(),
                            onDismissed: (_) => deleteAsk(context, ref, ask),
                            child: AskCard(ask: ask),
                          )
                        : AskCard(ask: ask),
                  ),
                  const SizedBox(height: Space.m),
                ],
            ],
          );
        },
      ),
    );
  }

  /// Real actions and repeated asks first, then oldest.
  static int _openOrder(Ask a, Ask b) {
    int score(Ask x) => (x.needsRealAction ? 2 : 0) + (x.timesRaised > 1 ? 1 : 0);
    final byScore = score(b).compareTo(score(a));
    return byScore != 0 ? byScore : a.openedOn.compareTo(b.openedOn);
  }
}

class _DeleteBackground extends StatelessWidget {
  const _DeleteBackground();

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
              Icon(Icons.delete_outline_rounded, color: colors.onMissing),
              const SizedBox(width: Space.s),
              Text('Delete', style: TextStyle(color: colors.onMissing, fontWeight: FontWeight.w700)),
            ],
          ),
        ),
      ),
    );
  }
}

class _OpenHeader extends ConsumerWidget {
  const _OpenHeader({required this.count});

  final int count;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    return Row(
      children: [
        Expanded(
          child: Text(
            '$count open · only answered asks go into the digest',
            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ),
        TextButton.icon(
          icon: const Icon(Icons.clear_all_rounded),
          label: const Text('Clear all'),
          onPressed: () async {
            final sure = await _confirm(
              context,
              title: 'Clear $count open asks?',
              message: 'They are removed from your list and never reported in a digest. If they are asked again, they come back as new asks.',
            );
            if (!sure || !context.mounted) return;
            final messenger = ScaffoldMessenger.of(context);
            final cleared = await ref.read(asksRepositoryProvider).clearOpenAsks();
            unawaited(Haptics.confirm());
            messenger.showSnackBar(SnackBar(content: Text('$cleared asks cleared')));
          },
        ),
      ],
    );
  }
}

/// Filter the Closed tab by the day asks were closed on.
class _ClosedDayFilter extends ConsumerWidget {
  const _ClosedDayFilter({required this.closed});

  final List<Ask> closed;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final selected = ref.watch(closedDayProvider);
    final counts = <String, int>{};
    for (final ask in closed) {
      final day = ask.closedOn;
      if (day != null) counts[day] = (counts[day] ?? 0) + 1;
    }
    final days = counts.keys.toList()..sort((a, b) => b.compareTo(a));
    final today = ref.watch(todayProvider);
    String label(String day) => day == today ? 'Today' : DateFormat('EEE d MMM').format(DateTime.parse(day));
    void pick(String? day) {
      unawaited(Haptics.selection());
      ref.read(closedDayProvider.notifier).state = day;
    }

    return Row(
      children: [
        Expanded(
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                ChoiceChip(label: Text('All (${closed.length})'), selected: selected == null, onSelected: (_) => pick(null)),
                for (final day in days.take(7)) ...[
                  const SizedBox(width: Space.s),
                  ChoiceChip(label: Text('${label(day)} (${counts[day]})'), selected: selected == day, onSelected: (_) => pick(day)),
                ],
                if (selected != null && !days.take(7).contains(selected)) ...[
                  const SizedBox(width: Space.s),
                  ChoiceChip(label: Text('${label(selected)} (${counts[selected] ?? 0})'), selected: true, onSelected: (_) => pick(null)),
                ],
              ],
            ),
          ),
        ),
        IconButton(
          tooltip: 'Pick a day',
          icon: const Icon(Icons.calendar_month_outlined),
          onPressed: days.isEmpty
              ? null
              : () async {
                  final last = DateTime.parse(days.first);
                  final first = DateTime.parse(days.last);
                  final chosen = await showDatePicker(
                    context: context,
                    initialDate: DateTime.parse(selected ?? days.first),
                    firstDate: first,
                    lastDate: last,
                    selectableDayPredicate: (d) => counts.containsKey(isoDay(d)),
                    helpText: 'Asks closed on',
                  );
                  if (chosen != null) pick(isoDay(chosen));
                },
        ),
      ],
    );
  }
}

Future<bool> _confirm(BuildContext context, {required String title, required String message}) async {
  final sure = await showDialog<bool>(
    context: context,
    builder: (dialog) => AlertDialog(
      title: Text(title),
      content: Text(message),
      actions: [
        TextButton(onPressed: () => Navigator.pop(dialog, false), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(dialog, true), child: const Text('Clear')),
      ],
    ),
  );
  return sure ?? false;
}

/// One ask with its actions and an animated check-off.
class AskCard extends ConsumerStatefulWidget {
  const AskCard({required this.ask, super.key});

  final Ask ask;

  @override
  ConsumerState<AskCard> createState() => _AskCardState();
}

class _AskCardState extends ConsumerState<AskCard> {
  bool _justDone = false;
  bool _busy = false;

  Future<void> _change(String status) async {
    final note = await showDialog<String>(context: context, builder: (_) => _NoteDialog(status: status));
    if (note == null || !mounted) return;
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref
          .read(asksRepositoryProvider)
          .setStatus(widget.ask, status, evidence: status == 'done' ? note : null, reason: status == 'done' ? null : note);
      if (status == 'done' && mounted) {
        unawaited(Haptics.confirm());
        final pause = Motion.of(context, Motion.emphasized) * 2;
        setState(() => _justDone = true);
        await Future<void>.delayed(pause);
      }
      ref.invalidate(asksProvider);
    } on Object catch (error) {
      messenger.showSnackBar(SnackBar(content: Text('$error')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.digestColors;
    final ask = widget.ask;
    final done = _justDone || ask.status == 'done';
    return AnimatedOpacity(
      opacity: _justDone ? 0.55 : 1,
      duration: Motion.of(context, Motion.standard),
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(Space.m),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  AnimatedSwitcher(
                    duration: Motion.of(context, Motion.standard),
                    transitionBuilder: (child, animation) => ScaleTransition(scale: animation, child: child),
                    child: Icon(
                      done ? Icons.check_circle : ask.kind == 'question' ? Icons.help_outline : Icons.radio_button_unchecked,
                      key: ValueKey(done),
                      color: done ? colors.onVerified : theme.colorScheme.primary,
                      semanticLabel: done ? 'Done' : ask.status,
                    ),
                  ),
                  const SizedBox(width: Space.s),
                  Expanded(
                    child: AnimatedDefaultTextStyle(
                      duration: Motion.of(context, Motion.standard),
                      style: (theme.textTheme.bodyLarge ?? const TextStyle()).copyWith(
                        decoration: done ? TextDecoration.lineThrough : TextDecoration.none,
                        color: theme.colorScheme.onSurface,
                      ),
                      child: Text(ask.text),
                    ),
                  ),
                  if (ask.isOpen)
                    PopupMenuButton<String>(
                      tooltip: 'Ask options',
                      onSelected: (_) => deleteAsk(context, ref, ask),
                      itemBuilder: (_) => const [
                        PopupMenuItem(
                          value: 'delete',
                          child: ListTile(leading: Icon(Icons.delete_outline_rounded), title: Text('Delete'), subtitle: Text('Never reported in a digest')),
                        ),
                      ],
                    ),
                ],
              ),
              const SizedBox(height: Space.s),
              Wrap(
                spacing: Space.s,
                runSpacing: Space.s,
                children: [
                  if (ask.needsRealAction)
                    StatusChip(label: 'Needs real action', background: colors.realAction, foreground: colors.onRealAction, icon: Icons.task_alt),
                  if (ask.timesRaised > 1)
                    StatusChip(label: 'Raised ${ask.timesRaised}×', background: colors.unverified, foreground: colors.onUnverified),
                  StatusChip(
                    label: 'Since ${ask.openedOn}',
                    background: theme.colorScheme.surfaceContainerHighest,
                    foreground: theme.colorScheme.onSurfaceVariant,
                  ),
                  for (final key in ask.relatedTickets)
                    StatusChip(label: key, background: theme.colorScheme.secondaryContainer, foreground: theme.colorScheme.onSecondaryContainer),
                ],
              ),
              if (ask.evidence != null || ask.reason != null) ...[
                const SizedBox(height: Space.s),
                Text(ask.evidence != null ? 'Evidence: ${ask.evidence}' : 'Reason: ${ask.reason}', style: theme.textTheme.bodySmall),
              ],
              if (ask.isOpen) ...[
                const SizedBox(height: Space.s),
                if (_busy)
                  const LinearProgressIndicator()
                else
                  Wrap(
                    spacing: Space.s,
                    children: [
                      FilledButton.tonal(onPressed: () => _change('done'), child: const Text('Done')),
                      OutlinedButton(onPressed: () => _change('disagree'), child: const Text('Disagree')),
                    ],
                  ),
              ] else if (ask.status != 'open')
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton(onPressed: () => _change('open'), child: const Text('Reopen')),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _NoteDialog extends StatefulWidget {
  const _NoteDialog({required this.status});

  final String status;

  @override
  State<_NoteDialog> createState() => _NoteDialogState();
}

class _NoteDialogState extends State<_NoteDialog> {
  final _text = TextEditingController();

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final (String title, String label, bool required) = switch (widget.status) {
      'done' => ('Mark as done', 'Evidence (link, ticket or commit)', true),
      'disagree' => ('Disagree', 'Your reason', true),
      _ => ('Reopen', 'Note (optional)', false),
    };
    return AlertDialog(
      title: Text(title),
      content: TextField(controller: _text, autofocus: true, decoration: InputDecoration(labelText: label)),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        ListenableBuilder(
          listenable: _text,
          builder: (context, _) => FilledButton(
            onPressed: required && _text.text.trim().isEmpty ? null : () => Navigator.pop(context, _text.text.trim()),
            child: const Text('Save'),
          ),
        ),
      ],
    );
  }
}

class _RuleList extends ConsumerWidget {
  const _RuleList();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final rules = ref.watch(rulesProvider);
    final theme = Theme.of(context);
    return rules.when(
      loading: () => ListView(padding: pagePadding(context), children: const [SkeletonCard()]),
      error: (error, _) => ErrorState(message: '$error', onRetry: () => ref.invalidate(rulesProvider)),
      data: (list) {
        final mine = list.where((r) => r.mine).toList();
        final manager = list.where((r) => !r.mine && r.active).toList();
        return ListView(
          padding: pagePadding(context),
          children: [
            Row(
              children: [
                Expanded(child: Text('My rules', style: theme.textTheme.titleMedium)),
                if (mine.any((r) => r.status == 'active')) const ApplyRulesButton(),
              ],
            ),
            const SizedBox(height: Space.xs),
            Text(
              'Your own rules for the digest. Exclusions (PRs, tickets, repos) are enforced in code.',
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
            const SizedBox(height: Space.m),
            const MyRuleComposer(),
            for (final (i, rule) in mine.indexed) ...[
              const SizedBox(height: Space.s),
              RevealIn(key: ValueKey(rule.ruleId), delay: Motion.stagger * (i < 6 ? i : 6), child: MyRuleCard(rule: rule)),
            ],
            const SizedBox(height: Space.l),
            Row(
              children: [
                Expanded(child: Text('${capitalizeFirst(ref.watch(managerPossessiveProvider))} rules', style: theme.textTheme.titleMedium)),
                if (manager.isNotEmpty)
                  TextButton.icon(
                    icon: const Icon(Icons.clear_all_rounded),
                    label: const Text('Clear all'),
                    onPressed: () async {
                      final sure = await _confirm(
                        context,
                        title: 'Clear ${ref.read(managerPossessiveProvider)} rules?',
                        message: 'Drafts stop being checked against them. Rules in his future replies are added again.',
                      );
                      if (!sure || !context.mounted) return;
                      final messenger = ScaffoldMessenger.of(context);
                      final cleared = await ref.read(asksRepositoryProvider).clearManagerRules();
                      unawaited(Haptics.confirm());
                      messenger.showSnackBar(SnackBar(content: Text('$cleared rules cleared')));
                    },
                  ),
              ],
            ),
            const SizedBox(height: Space.s),
            if (manager.isEmpty)
              const EmptyState(icon: Icons.rule, title: 'No rules yet', message: 'Standing rules from replies appear here.')
            else
              for (final rule in manager) ...[
                Card(
                  child: ListTile(
                    leading: Icon(rule.automatic ? Icons.fact_check_outlined : Icons.person_outline),
                    title: Text(rule.text),
                    subtitle: Text(rule.automatic ? 'Checked automatically on every draft' : 'Checked by you'),
                    trailing: IconButton(
                      tooltip: 'Delete rule',
                      icon: const Icon(Icons.delete_outline_rounded),
                      onPressed: () async {
                        final messenger = ScaffoldMessenger.of(context);
                        final repo = ref.read(asksRepositoryProvider);
                        try {
                          await repo.setManagerRuleOn(rule, on: false);
                          unawaited(Haptics.light());
                          messenger.showSnackBar(
                            SnackBar(
                              content: const Text('Rule deleted. Drafts are no longer checked against it.'),
                              action: SnackBarAction(label: 'Undo', onPressed: () => unawaited(repo.setManagerRuleOn(rule, on: true))),
                            ),
                          );
                        } on Object catch (error) {
                          messenger.showSnackBar(SnackBar(content: Text('Could not delete the rule: $error')));
                        }
                      },
                    ),
                  ),
                ),
                const SizedBox(height: Space.s),
              ],
          ],
        );
      },
    );
  }
}
