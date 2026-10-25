import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/data/sources/cloud_source.dart';
import 'package:work_tracker/features/asks_rules/asks_screen.dart';
import 'package:work_tracker/features/history/history_screen.dart';
import 'package:work_tracker/features/update/update_service.dart';
import 'package:work_tracker/features/update/update_ui.dart';

/// The signed-in account (re-read when the user changes).
final AutoDisposeProvider<AccountInfo?> accountProvider = Provider.autoDispose<AccountInfo?>((ref) {
  ref.watch(userIdProvider);
  return ref.watch(cloudSourceProvider).account;
});

/// The profile: who is signed in, this month at a glance, the Mac, then Settings and Sign out.
class ProfileScreen extends ConsumerWidget {
  const ProfileScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final account = ref.watch(accountProvider);
    final version = ref.watch(appVersionProvider).valueOrNull;
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final date = DateFormat('d MMM yyyy');
    return Scaffold(
      body: Column(
        children: [
          const AuroraHeader(title: 'Profile'),
          Expanded(
            child: ListView(
              padding: pagePadding(context, top: Space.l),
              children: [
                Column(
                  children: [
                    Hero(tag: ProfileAvatar.heroTag, child: ProfileAvatar(initials: account?.initials ?? '?', size: 96)),
                    const SizedBox(height: Space.m),
                    Text(account?.displayName ?? 'Signed out', style: theme.textTheme.headlineSmall, textAlign: TextAlign.center),
                    if (account != null)
                      Text(account.email, style: theme.textTheme.bodyMedium?.copyWith(color: scheme.onSurfaceVariant)),
                  ],
                ),
                const SizedBox(height: Space.l),
                ...cascade([
                  const UpdateBanner(),
                  const _ThisMonth(),
                  const SizedBox(height: Space.m),
                  _Section(
                    title: 'Account',
                    children: [
                      _InfoRow(icon: Icons.alternate_email_rounded, label: 'Email', value: account?.email ?? '—'),
                      if (account?.createdAt != null)
                        _InfoRow(icon: Icons.event_available_rounded, label: 'Member since', value: date.format(account!.createdAt!.toLocal())),
                      if (account?.lastSignInAt != null)
                        _InfoRow(
                          icon: Icons.login_rounded,
                          label: 'Last sign-in',
                          value: DateFormat('d MMM, HH:mm').format(account!.lastSignInAt!.toLocal()),
                        ),
                      const _InfoRow(icon: Icons.lock_outline_rounded, label: 'Data', value: 'Only you can see it'),
                    ],
                  ),
                  const SizedBox(height: Space.m),
                  const _YourMac(),
                  const SizedBox(height: Space.m),
                  _Section(
                    title: 'Options',
                    children: [
                      _OptionRow(
                        icon: Icons.settings_outlined,
                        label: 'Settings',
                        detail: 'Evening draft, AI, appearance, hidden sessions',
                        onTap: () => context.go('/today/profile/settings'),
                      ),
                      _OptionRow(
                        icon: Icons.info_outline_rounded,
                        label: 'About WorkDigest',
                        detail: 'Version and licences',
                        onTap: () => showAboutDialog(
                          context: context,
                          applicationName: 'WorkDigest',
                          applicationVersion: version == null ? null : '${version.version} (build ${version.build})',
                          applicationLegalese: 'Your daily digest, drafted from evidence. Nothing is sent without you.',
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: Space.l),
                  const _SignOutButton(),
                ]),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ThisMonth extends ConsumerWidget {
  const _ThisMonth();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final now = ref.watch(clockProvider)();
    final digests = ref.watch(monthDigestsProvider(DateTime(now.year, now.month))).valueOrNull?.value ?? const [];
    final asks = ref.watch(asksProvider).valueOrNull?.value ?? const [];
    final rules = ref.watch(rulesProvider).valueOrNull ?? const [];
    final sent = digests.where((d) => DigestState.of(d).isSent).length;
    return Row(
      children: [
        Expanded(child: _Stat(value: sent, label: 'Sent this month')),
        const SizedBox(width: Space.s),
        Expanded(child: _Stat(value: asks.where((a) => a.isOpen).length, label: 'Open asks')),
        const SizedBox(width: Space.s),
        Expanded(child: _Stat(value: rules.where((r) => r.mine && r.status == 'active').length, label: 'My rules')),
      ],
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.value, required this.label});

  final int value;
  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return GlassCard(
      padding: const EdgeInsets.symmetric(horizontal: Space.s, vertical: Space.m),
      child: Column(
        children: [
          CountUp(value: value, style: theme.textTheme.headlineMedium),
          Text(
            label,
            textAlign: TextAlign.center,
            style: theme.textTheme.labelMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ],
      ),
    );
  }
}

class _YourMac extends ConsumerWidget {
  const _YourMac();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final mac = ref.watch(macStatusProvider).valueOrNull;
    final online = ref.watch(macOnlineProvider);
    final status = mac == null
        ? 'Never connected'
        : online
        ? switch (mac.status) {
            'drafting' => 'Online · writing a draft',
            'syncing' => 'Online · syncing',
            _ => 'Online',
          }
        : 'Offline since ${DateFormat('d MMM, HH:mm').format(mac.lastSeenAt.toLocal())}';
    return _Section(
      title: 'Your Mac',
      children: [
        _InfoRow(
          icon: Icons.laptop_mac_rounded,
          label: mac?.label ?? 'Mac',
          value: status,
          trailing: LiveDot(color: online ? context.aurora.online : Theme.of(context).colorScheme.error),
        ),
      ],
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.children});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.only(left: Space.s, bottom: Space.s),
          child: Text(title, style: theme.textTheme.titleSmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
        ),
        GlassCard(
          padding: const EdgeInsets.symmetric(vertical: Space.xs),
          child: Column(
            children: [
              for (var i = 0; i < children.length; i++) ...[
                if (i > 0) const Divider(indent: Space.xxl + Space.s, height: 1),
                children[i],
              ],
            ],
          ),
        ),
      ],
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.icon, required this.label, required this.value, this.trailing});

  final IconData icon;
  final String label;
  final String value;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Semantics(
      container: true,
      label: '$label: $value',
      excludeSemantics: true,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: Space.m, vertical: Space.m - 4),
        child: Row(
          children: [
            Icon(icon, color: theme.colorScheme.primary, size: 22),
            const SizedBox(width: Space.m),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(label, style: theme.textTheme.labelMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                  Text(value, style: theme.textTheme.bodyLarge),
                ],
              ),
            ),
            ?trailing,
          ],
        ),
      ),
    );
  }
}

class _OptionRow extends StatelessWidget {
  const _OptionRow({required this.icon, required this.label, required this.detail, required this.onTap});

  final IconData icon;
  final String label;
  final String detail;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: Space.m),
      leading: Icon(icon, color: theme.colorScheme.primary),
      title: Text(label, style: theme.textTheme.titleSmall),
      subtitle: Text(detail, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
      trailing: Icon(Icons.chevron_right_rounded, color: theme.colorScheme.onSurfaceVariant),
      onTap: () {
        unawaited(Haptics.selection());
        onTap();
      },
    );
  }
}

class _SignOutButton extends ConsumerWidget {
  const _SignOutButton();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final scheme = Theme.of(context).colorScheme;
    return OutlinedButton.icon(
      style: OutlinedButton.styleFrom(foregroundColor: scheme.error, side: BorderSide(color: scheme.error.withValues(alpha: 0.5))),
      icon: const Icon(Icons.logout_rounded),
      label: const Text('Sign out'),
      onPressed: () async {
        final sure = await showDialog<bool>(
          context: context,
          builder: (dialog) => AlertDialog(
            title: const Text('Sign out?'),
            content: const Text('Your digests stay in your account. The copy saved on this phone is cleared.'),
            actions: [
              TextButton(onPressed: () => Navigator.pop(dialog, false), child: const Text('Cancel')),
              FilledButton(onPressed: () => Navigator.pop(dialog, true), child: const Text('Sign out')),
            ],
          ),
        );
        if (sure != true) return;
        final source = ref.read(cloudSourceProvider);
        final uid = source.userId;
        if (uid != null) await ref.read(snapshotCacheProvider).clear(uid);
        await source.signOut();
      },
    );
  }
}
