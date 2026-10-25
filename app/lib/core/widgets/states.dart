import 'dart:async';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';

/// A pulsing placeholder block shown while content loads (static under reduced motion).
class Skeleton extends StatefulWidget {
  const Skeleton({this.height = 16, this.width, super.key});

  final double height;
  final double? width;

  @override
  State<Skeleton> createState() => _SkeletonState();
}

class _SkeletonState extends State<Skeleton> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  );

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (Motion.enabled(context)) {
      unawaited(_controller.repeat(reverse: true));
    } else {
      _controller
        ..stop()
        ..value = 0.5;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.surfaceContainerHighest;
    return FadeTransition(
      opacity: Tween<double>(begin: 0.45, end: 1).animate(_controller),
      child: Container(
        height: widget.height,
        width: widget.width,
        decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(Space.xs)),
      ),
    );
  }
}

/// A card-shaped stack of skeleton lines.
class SkeletonCard extends StatelessWidget {
  const SkeletonCard({this.lines = 3, super.key});

  final int lines;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'Loading',
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(Space.m),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Skeleton(height: 20, width: 160),
              for (var i = 0; i < lines; i++) ...[
                const SizedBox(height: Space.s),
                Skeleton(width: i.isEven ? double.infinity : 220),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// Empty state with a clear next step.
class EmptyState extends StatelessWidget {
  const EmptyState({required this.icon, required this.title, required this.message, this.action, super.key});

  final IconData icon;
  final String title;
  final String message;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(Space.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 48, color: theme.colorScheme.primary, semanticLabel: title),
            const SizedBox(height: Space.m),
            Text(title, style: theme.textTheme.titleMedium, textAlign: TextAlign.center),
            const SizedBox(height: Space.s),
            Text(message, style: theme.textTheme.bodyMedium, textAlign: TextAlign.center),
            if (action != null) ...[const SizedBox(height: Space.l), action!],
          ],
        ),
      ),
    );
  }
}

/// Error state with retry.
class ErrorState extends StatelessWidget {
  const ErrorState({required this.message, required this.onRetry, super.key});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return EmptyState(
      icon: Icons.cloud_off_outlined,
      title: 'Could not load',
      message: message,
      action: FilledButton.icon(onPressed: onRetry, icon: const Icon(Icons.refresh), label: const Text('Try again')),
    );
  }
}

/// Banner shown when data comes from the offline cache.
class StaleBanner extends StatelessWidget {
  const StaleBanner({required this.savedAt, super.key});

  final DateTime savedAt;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Semantics(
      liveRegion: true,
      child: Material(
        color: scheme.secondaryContainer,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: Space.m, vertical: Space.s),
          child: Row(
            children: [
              Icon(Icons.cloud_off, size: 18, color: scheme.onSecondaryContainer),
              const SizedBox(width: Space.s),
              Expanded(
                child: Text(
                  'Offline – showing the copy saved ${DateFormat('d MMM, HH:mm').format(savedAt)}',
                  style: TextStyle(color: scheme.onSecondaryContainer),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// A small coloured status label.
class StatusChip extends StatelessWidget {
  const StatusChip({required this.label, required this.background, required this.foreground, this.icon, super.key});

  final String label;
  final Color background;
  final Color foreground;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: Space.s, vertical: Space.xs),
      decoration: BoxDecoration(color: background, borderRadius: BorderRadius.circular(Radii.chip)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[Icon(icon, size: 14, color: foreground), const SizedBox(width: Space.xs)],
          // Long labels ("Confirm: …") wrap instead of overflowing at large text sizes.
          Flexible(child: Text(label, style: Theme.of(context).textTheme.labelMedium?.copyWith(color: foreground))),
        ],
      ),
    );
  }
}
