import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:work_tracker/core/theme/tokens.g.dart';

/// Motion tokens that collapse to zero when the user asks for reduced motion.
///
/// Every animation in the app takes its duration from here, so "Reduce motion" (iOS) or
/// "Remove animations" (Android) turns all of them off at once.
abstract final class Motion {
  static const Duration quick = AuroraTokens.quick;
  static const Duration standard = AuroraTokens.standard;
  static const Duration emphasized = AuroraTokens.emphasized;
  static const Duration stagger = AuroraTokens.stagger;

  static const Curve curve = Curves.easeOutCubic;

  /// Fluid motion: a fast start that settles softly, like a flick coming to rest.
  static const Curve fluid = AuroraTokens.fluid;

  /// For things that should arrive with a slight overshoot (a "drop" into place).
  static const Curve settle = AuroraTokens.settle;

  /// Page pushes/pops and tab switches.
  static const Duration page = AuroraTokens.page;
  static const Duration tab = AuroraTokens.tab;

  /// Whether animations should run in this context.
  static bool enabled(BuildContext context) => !(MediaQuery.maybeDisableAnimationsOf(context) ?? false);

  /// ``duration``, or zero under reduced motion.
  static Duration of(BuildContext context, Duration duration) => enabled(context) ? duration : Duration.zero;
}

/// Subtle haptics on key actions only (share, copy, check-off, confirm).
abstract final class Haptics {
  static Future<void> confirm() => HapticFeedback.mediumImpact();

  static Future<void> light() => HapticFeedback.lightImpact();

  static Future<void> selection() => HapticFeedback.selectionClick();
}

/// Fades and slides a child in after ``delay``: the "streaming reveal" of a fresh draft.
///
/// The delay is part of one animation (an [Interval]), not a separate timer, so nothing is left
/// pending if the screen closes mid-reveal.
class RevealIn extends StatefulWidget {
  const RevealIn({required this.child, this.delay = Duration.zero, super.key});

  final Widget child;
  final Duration delay;

  @override
  State<RevealIn> createState() => _RevealInState();
}

class _RevealInState extends State<RevealIn> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: widget.delay + Motion.page,
  );
  late final Animation<double> _fade = CurvedAnimation(
    parent: _controller,
    curve: Interval(
      widget.delay.inMicroseconds / (widget.delay + Motion.page).inMicroseconds,
      1,
      curve: Motion.fluid,
    ),
  );

  bool _started = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    if (Motion.enabled(context)) {
      unawaited(_controller.forward());
    } else {
      _controller.value = 1;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // A fixed rise (not a fraction of the child's height): a tall section must not travel
    // hundreds of pixels on its way in.
    return FadeTransition(
      opacity: _fade,
      child: AnimatedBuilder(
        animation: _fade,
        child: widget.child,
        builder: (context, child) => Transform.translate(offset: Offset(0, 20 * (1 - _fade.value)), child: child),
      ),
    );
  }
}


/// Animated expand/collapse that renders instantly under reduced motion.
///
/// A zero-duration [AnimatedSize] can re-dirty itself during layout, so with animations off the
/// child is shown directly instead.
class MotionSize extends StatelessWidget {
  const MotionSize({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    if (!Motion.enabled(context)) return child;
    return AnimatedSize(duration: Motion.standard, curve: Motion.curve, alignment: Alignment.topCenter, child: child);
  }
}


/// Staggers a list's children in: each fades, rises and settles a beat after the previous one
/// (capped, so long lists do not keep the user waiting).
List<Widget> cascade(List<Widget> children, {int maxStaggered = 8}) => [
  for (var i = 0; i < children.length; i++)
    RevealIn(delay: Motion.stagger * (i < maxStaggered ? i : maxStaggered), child: children[i]),
];

/// Page transitions with depth: the new page grows into place from slightly below while the
/// page underneath recedes and dims. Pops play it in reverse. Nothing moves under reduced motion.
class FluidPageTransitionsBuilder extends PageTransitionsBuilder {
  const FluidPageTransitionsBuilder();

  @override
  Duration get transitionDuration => Motion.page;

  @override
  Duration get reverseTransitionDuration => const Duration(milliseconds: 380);

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) {
    if (!Motion.enabled(context)) return child;
    final enter = CurvedAnimation(parent: animation, curve: Motion.fluid, reverseCurve: Curves.easeInCubic);
    final leave = CurvedAnimation(parent: secondaryAnimation, curve: Motion.fluid, reverseCurve: Motion.fluid);
    return AnimatedBuilder(
      animation: Listenable.merge([enter, leave]),
      child: child,
      builder: (context, child) {
        final e = enter.value;
        final l = leave.value;
        return Opacity(
          opacity: (e * 1.4).clamp(0.0, 1.0) * (1 - 0.35 * l),
          child: Transform.translate(
            offset: Offset(0, 28 * (1 - e)),
            child: Transform.scale(scale: (0.94 + 0.06 * e) * (1 - 0.04 * l), child: child),
          ),
        );
      },
    );
  }
}
