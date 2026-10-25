import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/core/widgets/states.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/features/draft_editor/draft_controller.dart';
import 'package:work_tracker/features/today/close_day_sheet.dart';
import 'package:work_tracker/features/today/today_controller.dart';
import 'package:work_tracker/features/update/update_ui.dart';

class TodayScreen extends ConsumerWidget {
  const TodayScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final overview = ref.watch(todayOverviewProvider);
    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: RefreshIndicator(
          onRefresh: () => ref.refresh(todayOverviewProvider.future),
          child: overview.when(
            loading: () => ListView(
              padding: pagePadding(context),
              children: const [SkeletonCard(), SizedBox(height: Space.m), SkeletonCard(lines: 2), SizedBox(height: Space.m), SkeletonCard()],
            ),
            error: (error, _) => ListView(
              padding: pagePadding(context),
              children: [ErrorState(message: '$error', onRetry: () => ref.invalidate(todayOverviewProvider))],
            ),
            data: (data) => _TodayBody(data: data),
          ),
        ),
      ),
    );
  }
}

class _TodayBody extends ConsumerWidget {
  const _TodayBody({required this.data});

  final TodayOverview data;

  static String _greeting(DateTime now) => switch (now.hour) {
    < 12 => 'Good morning',
    < 17 => 'Good afternoon',
    _ => 'Good evening',
  };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final date = DateTime.parse(data.day);
    final closed = data.dayInfo?.closed ?? false;
    var index = 0;
    Widget reveal(Widget child) => RevealIn(delay: Motion.stagger * index++, child: child);
    return ListView(
      padding: pagePadding(context),
      children: [
        Row(
          children: [
            const _AvatarButton(),
            const Spacer(),
            CircleIconButton(
              icon: Icons.notifications_none_rounded,
              tooltip: 'Notifications',
              badge: data.notifications.isNotEmpty,
              onPressed: () => _showNotifications(context, ref),
            ),
          ],
        ),
        const SizedBox(height: Space.l),
        if (data.staleSince != null) ...[StaleBanner(savedAt: data.staleSince!), const SizedBox(height: Space.m)],
        const UpdateBanner(),
        Text(_greeting(data.now), style: theme.textTheme.titleSmall?.copyWith(color: scheme.onSurfaceVariant)),
        const SizedBox(height: Space.xs),
        Text(DateFormat('EEEE, d MMMM').format(date), style: theme.textTheme.headlineMedium),
        const SizedBox(height: Space.m),
        const _MacStatus(),
        const SizedBox(height: Space.l),
        if (data.notifications.isNotEmpty) ...[
          reveal(_NotificationCard(note: data.notifications.first)),
          const SizedBox(height: Space.m),
        ],
        if (data.activeJob != null) ...[
          reveal(
            GlassCard(
              onTap: () => context.go('/draft?job=${data.activeJob!.id}'),
              child: Row(
                children: [
                  const SizedBox.square(dimension: 24, child: CircularProgressIndicator(strokeWidth: 2.5)),
                  const SizedBox(width: Space.m),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(data.activeJob!.kind == 'full' ? 'The Mac is writing your draft' : 'The Mac is working', style: theme.textTheme.titleSmall),
                        Text(
                          ref.watch(macOnlineProvider) ? 'Status: ${data.activeJob!.status}' : 'Queued – it runs when the Mac is back online',
                          style: theme.textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: Space.m),
        ],
        reveal(Hero(tag: 'draft-card', child: _DraftHero(digest: data.digest, now: data.now))),
        const SizedBox(height: Space.l),
        reveal(
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: QuickAction(
                  icon: Icons.auto_awesome_rounded,
                  label: 'Draft now',
                  primary: true,
                  onPressed: data.activeJob == null ? () => _draftNow(context, ref) : null,
                ),
              ),
              Expanded(
                child: QuickAction(
                  icon: Icons.forum_outlined,
                  label: 'Add ${ref.watch(managerPossessiveProvider)} reply',
                  onPressed: () => context.push('/reply'),
                ),
              ),
              Expanded(
                child: QuickAction(
                  icon: Icons.nightlight_round,
                  label: closed ? 'Day closed' : 'Close day',
                  onPressed: closed ? null : () => _closeDay(context, ref),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: Space.l),
        reveal(
          Row(
            children: [
              Expanded(child: _Metric(label: 'Sessions', value: data.dayInfo?.counts['sessions'] ?? 0, icon: Icons.smart_toy_outlined)),
              const SizedBox(width: Space.m),
              Expanded(child: _Metric(label: 'Commits', value: data.dayInfo?.counts['commits'] ?? 0, icon: Icons.commit_rounded)),
            ],
          ),
        ),
        const SizedBox(height: Space.m),
        reveal(
          GlassCard(
            onTap: () => context.go('/asks'),
            child: Row(
              children: [
                _IconBadge(icon: Icons.checklist_rounded, color: context.digestColors.realAction, foreground: context.digestColors.onRealAction),
                const SizedBox(width: Space.m),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('${data.openAsks} open asks', style: theme.textTheme.titleMedium),
                      Text(
                        data.realActionAsks == 0 ? 'Nothing needs a real action' : '${data.realActionAsks} need a real action, not just wording',
                        style: theme.textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                      ),
                    ],
                  ),
                ),
                Icon(Icons.chevron_right_rounded, color: scheme.onSurfaceVariant),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Future<void> _draftNow(BuildContext context, WidgetRef ref) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final job = await ref.read(draftActionsProvider).generate(data.day);
      unawaited(Haptics.confirm());
      if (context.mounted) context.go('/draft?job=${job.id}');
    } on Object catch (error) {
      messenger.showSnackBar(SnackBar(content: Text('Could not ask the Mac for a draft: $error')));
    }
  }

  Future<void> _showNotifications(BuildContext context, WidgetRef ref) async {
    await showModalBottomSheet<void>(
      context: context,
      // Above the floating nav bar (the tab's own navigator sits underneath it).
      useRootNavigator: true,
      builder: (sheet) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(Space.m, 0, Space.m, Space.m),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('Notifications', style: Theme.of(sheet).textTheme.titleLarge),
              const SizedBox(height: Space.m),
              if (data.notifications.isEmpty) const Text('Nothing new.'),
              for (final note in data.notifications) ...[
                _NotificationCard(note: note, onOpened: () => Navigator.of(sheet).pop()),
                const SizedBox(height: Space.s),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _closeDay(BuildContext context, WidgetRef ref) async {
    final result = await showModalBottomSheet<CloseDayResult>(
      context: context,
      // Above the floating nav bar (the tab's own navigator sits underneath it).
      useRootNavigator: true,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => CloseDaySheet(
        initialTeam: data.dayInfo?.teamMembers.isNotEmpty ?? false ? data.dayInfo!.teamMembers : data.digest?.header.teamMembers ?? const [],
        initialProject: data.dayInfo?.project ?? data.digest?.header.project,
      ),
    );
    if (result == null || !context.mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      final job = await ref
          .read(workRepositoryProvider)
          .closeDay(data.day, rating: result.rating, team: result.team, project: result.project);
      unawaited(Haptics.confirm());
      if (context.mounted) context.go('/draft?job=${job.id}');
    } on Object catch (error) {
      messenger.showSnackBar(SnackBar(content: Text('Could not close the day: $error')));
    }
  }
}

/// The day's headline: gap count, status and the send deadline, on the violet hero card.
class _DraftHero extends StatelessWidget {
  const _DraftHero({required this.digest, required this.now});

  final Digest? digest;
  final DateTime now;

  static const int _deadlineHour = 21;

  String _countdown() {
    final deadline = DateTime(now.year, now.month, now.day, _deadlineHour);
    final left = deadline.difference(now);
    if (left.isNegative) return 'past 21:00';
    final hours = left.inHours;
    final minutes = left.inMinutes % 60;
    return hours > 0 ? '${hours}h ${minutes}m left' : '${minutes}m left';
  }

  @override
  Widget build(BuildContext context) {
    final aurora = context.aurora;
    final theme = Theme.of(context);
    final digest = this.digest;
    final muted = theme.textTheme.bodyMedium?.copyWith(color: aurora.onHeroMuted);
    return HeroCard(
      onTap: () => context.go('/draft'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.edit_note_rounded),
              const SizedBox(width: Space.s),
              Expanded(child: Text("Today's draft", style: theme.textTheme.titleMedium?.copyWith(color: aurora.onHero))),
              if (digest != null) _HeroChip(label: digest.isSent ? 'Sent' : 'Draft'),
            ],
          ),
          const SizedBox(height: Space.l),
          if (digest == null)
            Text('No draft yet. Close the day to generate one, or wait for the 8 pm draft.', style: theme.textTheme.titleMedium?.copyWith(color: aurora.onHero))
          else ...[
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                CountUp(value: digest.blockingGaps, style: theme.textTheme.displayMedium?.copyWith(color: aurora.onHero, fontWeight: FontWeight.w800, letterSpacing: -2)),
                const SizedBox(width: Space.s),
                Flexible(child: Text('gaps to confirm', style: theme.textTheme.titleMedium?.copyWith(color: aurora.onHeroMuted))),
              ],
            ),
            const SizedBox(height: Space.xs),
            Text('${digest.sections.length} sections', style: muted),
          ],
          const SizedBox(height: Space.m),
          Row(
            children: [
              Icon(Icons.schedule_rounded, size: 18, color: aurora.onHeroMuted),
              const SizedBox(width: Space.xs),
              Expanded(child: Text('Send before 21:00 · ${_countdown()}', style: muted)),
              Text('Open', style: theme.textTheme.labelLarge?.copyWith(color: aurora.onHero)),
              Icon(Icons.arrow_forward_rounded, size: 18, color: aurora.onHero),
            ],
          ),
        ],
      ),
    );
  }
}

class _HeroChip extends StatelessWidget {
  const _HeroChip({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(color: Color(0x33FFFFFF), borderRadius: Radii.pillRadius),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: Space.m - 4, vertical: Space.xs),
        child: Text(label, style: Theme.of(context).textTheme.labelLarge?.copyWith(color: Colors.white)),
      ),
    );
  }
}

class _NotificationCard extends ConsumerWidget {
  const _NotificationCard({required this.note, this.onOpened});

  final AppNotification note;
  final VoidCallback? onOpened;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    return GlassCard(
      color: theme.colorScheme.primaryContainer,
      onTap: () async {
        onOpened?.call();
        await ref.read(workRepositoryProvider).markRead(note);
        if (context.mounted) context.go('/draft');
      },
      child: Row(
        children: [
          Icon(Icons.notifications_active_rounded, color: theme.colorScheme.onPrimaryContainer),
          const SizedBox(width: Space.m),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(note.title, style: theme.textTheme.titleSmall?.copyWith(color: theme.colorScheme.onPrimaryContainer)),
                Text(note.body, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onPrimaryContainer)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Your initials; opens the profile (the avatar flies into the profile header).
class _AvatarButton extends ConsumerWidget {
  const _AvatarButton();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.watch(userIdProvider);
    final account = ref.watch(cloudSourceProvider).account;
    return Semantics(
      container: true,
      button: true,
      label: 'Profile',
      onTap: () => context.go('/today/profile'),
      excludeSemantics: true,
      child: Tooltip(
        message: 'Profile',
        child: PressScale(
          child: InkWell(
            customBorder: const CircleBorder(),
            onTap: () {
              unawaited(Haptics.selection());
              context.go('/today/profile');
            },
            child: Hero(tag: ProfileAvatar.heroTag, child: ProfileAvatar(initials: account?.initials ?? '?')),
          ),
        ),
      ),
    );
  }
}

/// Live: follows the Mac's heartbeat and what it is doing, and ages to "offline" on its own.
class _MacStatus extends ConsumerWidget {
  const _MacStatus();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final scheme = Theme.of(context).colorScheme;
    final mac = ref.watch(macStatusProvider).valueOrNull;
    final online = ref.watch(macOnlineProvider);
    final lastSeen = mac?.lastSeenAt;
    final text = online
        ? switch (mac?.status) {
            'drafting' => 'Mac connected · writing your draft…',
            'syncing' => 'Mac connected · syncing…',
            _ => 'Mac connected',
          }
        : lastSeen == null || mac == null
        ? 'Mac never connected – run `digest cloud worker` on the Mac'
        : 'Mac offline since ${DateFormat('d MMM, HH:mm').format(lastSeen.toLocal())} – drafts wait until it is back';
    return Semantics(
      container: true,
      label: text,
      excludeSemantics: true,
      child: GlassCard(
        radius: Radii.pillRadius,
        padding: const EdgeInsets.symmetric(horizontal: Space.m, vertical: Space.m - 4),
        child: Row(
          children: [
            Icon(Icons.laptop_mac_rounded, size: 20, color: scheme.onSurfaceVariant),
            const SizedBox(width: Space.s + 4),
            Expanded(child: Text(text, style: Theme.of(context).textTheme.bodyMedium)),
            const SizedBox(width: Space.s),
            LiveDot(color: online ? context.aurora.online : scheme.error),
          ],
        ),
      ),
    );
  }
}

class _IconBadge extends StatelessWidget {
  const _IconBadge({required this.icon, required this.color, required this.foreground});

  final IconData icon;
  final Color color;
  final Color foreground;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(color: color, shape: BoxShape.circle),
      child: SizedBox.square(dimension: 44, child: Icon(icon, color: foreground, size: 22)),
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric({required this.label, required this.value, required this.icon});

  final String label;
  final int value;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _IconBadge(icon: icon, color: theme.colorScheme.primaryContainer, foreground: theme.colorScheme.onPrimaryContainer),
          const SizedBox(height: Space.m),
          CountUp(value: value, style: theme.textTheme.headlineMedium),
          Text(label, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
        ],
      ),
    );
  }
}
