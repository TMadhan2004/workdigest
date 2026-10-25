import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/features/update/update_service.dart';

/// "WorkDigest 1.4 is live" with an Update button; nothing when this install is the newest.
class UpdateBanner extends ConsumerWidget {
  const UpdateBanner({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(updateStatusProvider).valueOrNull;
    if (status == null || !status.available) return const SizedBox.shrink();
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final notes = status.latest.notes.split('\n').firstWhere((l) => l.trim().isNotEmpty, orElse: () => '');
    return Padding(
      padding: const EdgeInsets.only(bottom: Space.m),
      child: GlassCard(
        onTap: () => showUpdateSheet(context, status.latest),
        child: Row(
          children: [
            Icon(Icons.system_update_rounded, color: scheme.primary),
            const SizedBox(width: Space.m),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('WorkDigest ${status.latest.version} is live', style: theme.textTheme.titleSmall),
                  if (notes.isNotEmpty)
                    Text(notes.trim(), maxLines: 2, overflow: TextOverflow.ellipsis, style: theme.textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant)),
                ],
              ),
            ),
            const SizedBox(width: Space.s),
            FilledButton(onPressed: () => showUpdateSheet(context, status.latest), child: const Text('Update')),
          ],
        ),
      ),
    );
  }
}

Future<void> showUpdateSheet(BuildContext context, Release release) => showModalBottomSheet<void>(
  context: context,
  // Above the floating nav bar (the tab's own navigator sits underneath it).
  useRootNavigator: true,
  isScrollControlled: true,
  showDragHandle: true,
  builder: (_) => SafeArea(
    child: Padding(padding: const EdgeInsets.fromLTRB(Space.l, 0, Space.l, Space.l), child: UpdatePanel(release: release)),
  ),
);

/// What's new, then download (with progress) and Android's installer.
class UpdatePanel extends ConsumerStatefulWidget {
  const UpdatePanel({required this.release, this.required = false, super.key});

  final Release release;

  /// Shown full screen when this install is too old to keep working.
  final bool required;

  @override
  ConsumerState<UpdatePanel> createState() => _UpdatePanelState();
}

class _UpdatePanelState extends ConsumerState<UpdatePanel> {
  StreamSubscription<InstallProgress>? _subscription;
  InstallProgress? _progress;

  @override
  void dispose() {
    unawaited(_subscription?.cancel());
    super.dispose();
  }

  void _start() {
    unawaited(_subscription?.cancel());
    setState(() => _progress = const InstallProgress());
    _subscription = ref
        .read(updateInstallerProvider)(widget.release)
        .listen(
          (progress) => setState(() => _progress = progress),
          onError: (Object error) => setState(() => _progress = InstallProgress(error: 'The update could not be downloaded ($error).')),
        );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final release = widget.release;
    final progress = _progress;
    final busy = progress != null && progress.error == null && !progress.installing;
    final size = release.size == null ? '' : ' · ${(release.size! / (1024 * 1024)).toStringAsFixed(1)} MB';
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(widget.required ? 'Update required' : 'WorkDigest ${release.version}', style: theme.textTheme.headlineSmall),
        const SizedBox(height: Space.xs),
        Text(
          widget.required
              ? 'This version is too old to keep working with your Mac. Update to ${release.version} to continue$size.'
              : 'A new version is ready$size. Your drafts and settings stay as they are.',
          style: theme.textTheme.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
        ),
        if (release.notes.trim().isNotEmpty) ...[
          const SizedBox(height: Space.m),
          Text("What's new", style: theme.textTheme.titleSmall),
          const SizedBox(height: Space.xs),
          ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 240),
            child: SingleChildScrollView(child: Text(release.notes.trim(), style: theme.textTheme.bodyMedium)),
          ),
        ],
        const SizedBox(height: Space.l),
        AnimatedSwitcher(
          duration: Motion.of(context, Motion.standard),
          child: switch (progress) {
            null => const SizedBox.shrink(),
            InstallProgress(:final String error) => Text(
              error,
              key: const ValueKey('error'),
              style: theme.textTheme.bodyMedium?.copyWith(color: scheme.error),
            ),
            InstallProgress(installing: true) => Text(
              "Android's installer is open: tap Update there to finish.",
              key: const ValueKey('installing'),
              style: theme.textTheme.bodyMedium,
            ),
            InstallProgress(:final fraction) => Column(
              key: const ValueKey('downloading'),
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                ClipRRect(
                  borderRadius: Radii.pillRadius,
                  child: LinearProgressIndicator(value: fraction == null || fraction == 0 ? null : fraction, minHeight: 8),
                ),
                const SizedBox(height: Space.xs),
                Text(
                  fraction == null || fraction == 0 ? 'Starting the download…' : 'Downloading… ${(fraction * 100).round()}%',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
          },
        ),
        const SizedBox(height: Space.m),
        FilledButton.icon(
          onPressed: busy ? null : _start,
          icon: const Icon(Icons.download_rounded),
          label: Text(progress?.error != null ? 'Try again' : 'Download and install'),
        ),
        const SizedBox(height: Space.s),
        Text(
          'Android asks you to confirm the install. The first time, it may ask you to allow installs from WorkDigest.',
          style: theme.textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}

/// Replaces the whole app with "Update required" when this install is below the minimum build.
class UpdateGate extends ConsumerWidget {
  const UpdateGate({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(updateStatusProvider).valueOrNull;
    if (status == null || !status.required) return child;
    return AuroraBackground(
      child: Scaffold(
        body: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(Space.l),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 480),
                child: UpdatePanel(release: status.latest, required: true),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
