import 'dart:math' as math;
import 'dart:ui' show ImageFilter;

import 'package:flutter/material.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';

/// Page padding for scrolling content. Inside the tab shell the bottom padding already
/// includes the floating navigation bar (the shell's Scaffold uses `extendBody`), so content
/// scrolls under the glass bar and its last item still clears it.
EdgeInsets pagePadding(BuildContext context, {double top = Space.s}) =>
    EdgeInsets.fromLTRB(Space.m, top, Space.m, MediaQuery.paddingOf(context).bottom + Space.l);

/// Glass effects (blur, translucency) are skipped when the user asks for less motion or more
/// contrast; surfaces then render solid, which also keeps text contrast predictable.
bool glassEnabled(BuildContext context) => Motion.enabled(context) && !MediaQuery.highContrastOf(context);

/// The page backdrop: three soft, static glows over the base colour, plus faint contour lines.
///
/// Static on purpose: an endless background animation costs battery for little benefit.
class AuroraBackground extends StatelessWidget {
  const AuroraBackground({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final aurora = context.aurora;
    return DecoratedBox(
      decoration: BoxDecoration(color: aurora.background),
      child: Stack(
        fit: StackFit.expand,
        children: [
          Positioned.fill(child: IgnorePointer(child: CustomPaint(painter: _GlowPainter(aurora)))),
          child,
        ],
      ),
    );
  }
}

class _GlowPainter extends CustomPainter {
  const _GlowPainter(this.aurora);

  final AuroraColors aurora;

  @override
  void paint(Canvas canvas, Size size) {
    void glow(Offset center, double radius, Color color, double alpha) {
      final paint = Paint()
        ..shader = RadialGradient(colors: [color.withValues(alpha: alpha), color.withValues(alpha: 0)])
            .createShader(Rect.fromCircle(center: center, radius: radius));
      canvas.drawCircle(center, radius, paint);
    }

    final w = size.width;
    final h = size.height;
    glow(Offset(w * 0.9, h * 0.04), w * 0.85, aurora.glowB, 0.38);
    glow(Offset(w * 0.05, h * 0.22), w * 0.7, aurora.glowA, 0.30);
    glow(Offset(w * 0.75, h * 0.62), w * 0.75, aurora.glowC, 0.16);

    // Contour lines in the top-right, like a topographic map.
    final lines = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1
      ..color = aurora.hairline;
    for (var i = 0; i < 7; i++) {
      final path = Path()
        ..moveTo(w * 0.35, -10 + i * 14.0)
        ..cubicTo(w * 0.6, 40 + i * 18.0, w * 0.75, -20 + i * 22.0, w + 10, 60 + i * 20.0);
      canvas.drawPath(path, lines);
    }
  }

  @override
  bool shouldRepaint(_GlowPainter oldDelegate) => oldDelegate.aurora != aurora;
}

/// Scales its child down a little while pressed, springing back on release.
class PressScale extends StatefulWidget {
  const PressScale({required this.child, super.key});

  final Widget child;

  @override
  State<PressScale> createState() => _PressScaleState();
}

class _PressScaleState extends State<PressScale> {
  bool _down = false;

  void _set(bool value) {
    if (_down != value) setState(() => _down = value);
  }

  @override
  Widget build(BuildContext context) {
    return Listener(
      onPointerDown: (_) => _set(true),
      onPointerUp: (_) => _set(false),
      onPointerCancel: (_) => _set(false),
      child: AnimatedScale(
        scale: _down ? 0.97 : 1,
        duration: Motion.of(context, Motion.quick),
        curve: _down ? Curves.easeOut : Curves.easeOutBack,
        child: widget.child,
      ),
    );
  }
}

/// A frosted card: translucent fill, hairline border, optional backdrop blur and tap.
class GlassCard extends StatelessWidget {
  const GlassCard({
    required this.child,
    this.padding = const EdgeInsets.all(Space.m),
    this.onTap,
    this.blur = false,
    this.strong = false,
    this.radius = Radii.cardRadius,
    this.color,
    super.key,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;

  /// Blur what is behind (for floating surfaces); list cards stay unblurred for speed.
  final bool blur;
  final bool strong;
  final BorderRadius radius;

  /// Overrides the glass fill (e.g. a tinted notification card).
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final aurora = context.aurora;
    final scheme = Theme.of(context).colorScheme;
    final glass = glassEnabled(context);
    final fill = color ?? (glass ? (strong ? aurora.glassStrong : aurora.glass) : scheme.surfaceContainerHigh);
    // Ink and a tap target only for tappable cards; static cards are just padding.
    Widget content;
    if (onTap == null) {
      content = Padding(padding: padding, child: child);
    } else {
      content = Material(
        type: MaterialType.transparency,
        child: InkWell(onTap: onTap, borderRadius: radius, child: Padding(padding: padding, child: child)),
      );
    }
    content = DecoratedBox(
      decoration: BoxDecoration(color: fill, borderRadius: radius, border: Border.all(color: aurora.hairline)),
      child: content,
    );
    if (blur && glass) {
      content = BackdropFilter(filter: ImageFilter.blur(sigmaX: 24, sigmaY: 24), child: content);
    }
    content = ClipRRect(borderRadius: radius, child: content);
    return onTap == null ? content : PressScale(child: content);
  }
}

/// The violet gradient card for the day's headline, with a fine line pattern and a soft glow.
class HeroCard extends StatelessWidget {
  const HeroCard({required this.child, this.onTap, super.key});

  final Widget child;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final aurora = context.aurora;
    final card = DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: const BorderRadius.all(Radius.circular(28)),
        gradient: LinearGradient(colors: aurora.heroGradient, begin: Alignment.topLeft, end: Alignment.bottomRight),
        // A soft glow underneath (negative spread keeps it from reading as a second card).
        boxShadow: [BoxShadow(color: aurora.heroGradient[1].withValues(alpha: 0.30), blurRadius: 44, spreadRadius: -10, offset: const Offset(0, 18))],
      ),
      child: ClipRRect(
        borderRadius: const BorderRadius.all(Radius.circular(28)),
        child: Stack(
          children: [
            Positioned.fill(child: CustomPaint(painter: _HeroLinesPainter(aurora.onHero))),
            Material(
              type: MaterialType.transparency,
              child: InkWell(
                onTap: onTap,
                child: Padding(
                  padding: const EdgeInsets.all(Space.l - 4),
                  child: DefaultTextStyle.merge(
                    style: TextStyle(color: aurora.onHero),
                    child: IconTheme.merge(data: IconThemeData(color: aurora.onHero), child: child),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
    return onTap == null ? card : PressScale(child: card);
  }
}

class _HeroLinesPainter extends CustomPainter {
  const _HeroLinesPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1
      ..color = color.withValues(alpha: 0.09);
    for (var i = 0; i < 9; i++) {
      final r = 60.0 + i * 26;
      canvas.drawArc(Rect.fromCircle(center: Offset(size.width * 0.95, size.height * 1.1), radius: r), math.pi, math.pi / 1.6, false, paint);
    }
  }

  @override
  bool shouldRepaint(_HeroLinesPainter oldDelegate) => oldDelegate.color != color;
}

/// A round glass icon button (48 dp) with an optional unread dot.
class CircleIconButton extends StatelessWidget {
  const CircleIconButton({required this.icon, required this.tooltip, required this.onPressed, this.badge = false, super.key});

  final IconData icon;
  final String tooltip;
  final VoidCallback? onPressed;
  final bool badge;

  @override
  Widget build(BuildContext context) {
    final aurora = context.aurora;
    final scheme = Theme.of(context).colorScheme;
    return Stack(
      clipBehavior: Clip.none,
      children: [
        PressScale(
          child: Material(
            color: aurora.glass,
            shape: CircleBorder(side: BorderSide(color: aurora.hairline)),
            // The visible circle is the full 48 dp tap target, so it lines up with the 48 dp avatar.
            child: IconButton(
              tooltip: tooltip,
              icon: Icon(icon, color: scheme.onSurface),
              onPressed: onPressed,
              constraints: const BoxConstraints.tightFor(width: 48, height: 48),
              padding: EdgeInsets.zero,
              style: const ButtonStyle(tapTargetSize: MaterialTapTargetSize.shrinkWrap),
            ),
          ),
        ),
        if (badge)
          Positioned(
            right: 4,
            top: 4,
            child: IgnorePointer(
              child: Container(
                width: 10,
                height: 10,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: aurora.accentGradient.first,
                  border: Border.all(color: aurora.background, width: 2),
                ),
              ),
            ),
          ),
      ],
    );
  }
}

/// A large number that counts up to its value (instantly under reduced motion).
class CountUp extends StatelessWidget {
  const CountUp({required this.value, this.style, super.key});

  final int value;
  final TextStyle? style;

  @override
  Widget build(BuildContext context) {
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: value.toDouble()),
      duration: Motion.of(context, Motion.emphasized * 2),
      curve: Curves.easeOutCubic,
      builder: (context, current, _) => Text(
        '${current.round()}',
        style: style,
        semanticsLabel: '$value',
      ),
    );
  }
}

/// A small glowing dot for live states (connected, drafting).
class LiveDot extends StatelessWidget {
  const LiveDot({required this.color, this.size = 10, super.key});

  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: color,
        boxShadow: [BoxShadow(color: color.withValues(alpha: 0.55), blurRadius: 8, spreadRadius: 1)],
      ),
    );
  }
}

/// A round quick action with a label underneath (Draft now, Add reply, Close day).
class QuickAction extends StatelessWidget {
  const QuickAction({required this.icon, required this.label, required this.onPressed, this.primary = false, super.key});

  final IconData icon;
  final String label;
  final VoidCallback? onPressed;
  final bool primary;

  @override
  Widget build(BuildContext context) {
    final aurora = context.aurora;
    final scheme = Theme.of(context).colorScheme;
    final enabled = onPressed != null;
    final fill = primary && enabled
        ? BoxDecoration(shape: BoxShape.circle, gradient: LinearGradient(colors: aurora.accentGradient))
        : BoxDecoration(shape: BoxShape.circle, color: aurora.glass, border: Border.all(color: aurora.hairline));
    final iconColor = primary && enabled ? scheme.onPrimary : (enabled ? scheme.onSurface : scheme.onSurfaceVariant);
    return Semantics(
      container: true,
      button: true,
      enabled: enabled,
      label: label,
      onTap: onPressed,
      excludeSemantics: true,
      child: PressScale(
        child: InkWell(
          onTap: onPressed,
          customBorder: const StadiumBorder(),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: Space.xs, vertical: Space.xs),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                DecoratedBox(
                  decoration: fill,
                  child: SizedBox.square(dimension: 56, child: Icon(icon, color: iconColor)),
                ),
                const SizedBox(height: Space.s),
                Text(
                  label,
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.labelMedium?.copyWith(
                    color: enabled ? scheme.onSurface : scheme.onSurfaceVariant,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// A large page title with round actions (replaces the app bar on Aurora screens).
///
/// Shows a back button when the page was pushed (e.g. Settings, the reply screen).
class AuroraHeader extends StatelessWidget {
  const AuroraHeader({required this.title, this.subtitle, this.actions = const [], this.bottom, super.key});

  final String title;
  final String? subtitle;
  final List<Widget> actions;

  /// E.g. a TabBar under the title.
  final Widget? bottom;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final canPop = Navigator.of(context).canPop();
    return SafeArea(
      bottom: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(Space.m, Space.s, Space.m, 0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                if (canPop) ...[
                  CircleIconButton(icon: Icons.arrow_back_rounded, tooltip: 'Back', onPressed: () => Navigator.of(context).maybePop()),
                  const SizedBox(width: Space.m),
                ],
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Semantics(header: true, child: Text(title, style: theme.textTheme.headlineSmall, maxLines: 2, overflow: TextOverflow.ellipsis)),
                      if (subtitle != null)
                        Text(subtitle!, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                    ],
                  ),
                ),
                for (final action in actions) ...[const SizedBox(width: Space.s), action],
              ],
            ),
            if (bottom != null) ...[const SizedBox(height: Space.s), bottom!],
          ],
        ),
      ),
    );
  }
}

/// The user's initials on the violet gradient, with a soft glow (top-left of Today, profile header).
class ProfileAvatar extends StatelessWidget {
  const ProfileAvatar({required this.initials, this.size = 48, super.key});

  /// Shared by Today's avatar and the profile header, so it flies between them.
  static const heroTag = 'profile-avatar';

  final String initials;
  final double size;

  @override
  Widget build(BuildContext context) {
    final aurora = context.aurora;
    return DecoratedBox(
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: LinearGradient(colors: aurora.heroGradient, begin: Alignment.topLeft, end: Alignment.bottomRight),
        boxShadow: [BoxShadow(color: aurora.heroGradient[1].withValues(alpha: 0.35), blurRadius: size / 3)],
        border: Border.all(color: aurora.hairline, width: size / 24),
      ),
      child: SizedBox.square(
        dimension: size,
        child: Center(
          child: Text(
            initials,
            style: TextStyle(color: aurora.onHero, fontWeight: FontWeight.w800, fontSize: size * 0.36, letterSpacing: 0.5),
          ),
        ),
      ),
    );
  }
}
