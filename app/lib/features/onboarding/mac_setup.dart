import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/features/profile/profile_screen.dart';

/// The Terminal command that installs WorkDigest on a Mac, with a copy button.
class MacInstallCommand extends ConsumerWidget {
  const MacInstallCommand({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final command = ref.watch(appConfigProvider).macInstallCommand;
    final theme = Theme.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest,
        borderRadius: const BorderRadius.all(Radius.circular(Radii.highlight)),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(Space.m, Space.xs, Space.xs, Space.xs),
        child: Row(
          children: [
            Expanded(
              child: SelectableText(command, style: theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace')),
            ),
            IconButton(
              tooltip: 'Copy the command',
              icon: const Icon(Icons.copy_rounded),
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: command));
                unawaited(Haptics.confirm());
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Command copied')));
                }
              },
            ),
          ],
        ),
      ),
    );
  }
}

/// Signed in, but no Mac has connected to this account yet: finish setup there. Turns into
/// Today by itself as soon as the Mac's first heartbeat arrives.
class FinishSetupScreen extends ConsumerWidget {
  const FinishSetupScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final account = ref.watch(accountProvider);
    final site = ref.watch(appConfigProvider).siteUrl;
    return Scaffold(
      body: SafeArea(
        child: ListView(
          padding: pagePadding(context, top: Space.xl),
          children: cascade([
            HeroCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.laptop_mac_rounded, size: 40, color: context.aurora.onHero),
                  const SizedBox(height: Space.m),
                  Text('Finish setup on your Mac', style: theme.textTheme.headlineSmall?.copyWith(color: context.aurora.onHero)),
                  const SizedBox(height: Space.s),
                  Text(
                    'WorkDigest drafts your digest from what happens on your Mac, so it needs to run there first.',
                    style: theme.textTheme.bodyMedium?.copyWith(color: context.aurora.onHeroMuted),
                  ),
                ],
              ),
            ),
            const SizedBox(height: Space.l),
            GlassCard(
              padding: const EdgeInsets.all(Space.l),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('On your Mac', style: theme.textTheme.titleMedium),
                  const SizedBox(height: Space.s),
                  const Text('1. Open Terminal and paste:'),
                  const SizedBox(height: Space.s),
                  const MacInstallCommand(),
                  const SizedBox(height: Space.m),
                  Text('2. Follow the setup page that opens. Sign in there as ${account?.email ?? 'this account'}.'),
                  const SizedBox(height: Space.s),
                  const Text('3. Press "Start WorkDigest" at the end.'),
                  if (site.isNotEmpty) ...[
                    const SizedBox(height: Space.m),
                    Text('Step-by-step guide: $site', style: theme.textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant)),
                  ],
                ],
              ),
            ),
            const SizedBox(height: Space.l),
            Semantics(
              liveRegion: true,
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  // A still icon when the user asks for less motion (the wait can be long).
                  if (Motion.enabled(context))
                    const SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  else
                    Icon(Icons.hourglass_top_rounded, size: 18, color: scheme.onSurfaceVariant),
                  const SizedBox(width: Space.s),
                  Text('Waiting for your Mac…', style: theme.textTheme.bodyMedium?.copyWith(color: scheme.onSurfaceVariant)),
                ],
              ),
            ),
            const SizedBox(height: Space.l),
            Center(
              child: TextButton(
                onPressed: () => ref.read(cloudSourceProvider).signOut(),
                child: const Text('Sign out'),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}
