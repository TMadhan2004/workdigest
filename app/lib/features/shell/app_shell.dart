import 'dart:async';
import 'dart:math' as math;
import 'dart:ui' show ImageFilter, lerpDouble;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/features/reminders/reminders.dart';
import 'package:work_tracker/features/update/update_schedule.dart';

/// One tab of the floating navigation bar.
@immutable
class NavItem {
  const NavItem(this.label, this.icon, this.selectedIcon);

  final String label;
  final IconData icon;
  final IconData selectedIcon;
}

/// The five main areas, with a floating glass navigation bar over the content.
class AppShell extends StatelessWidget {
  const AppShell({required this.shell, required this.children, super.key});

  final StatefulNavigationShell shell;

  /// One navigator per tab (from `navigatorContainerBuilder`).
  final List<Widget> children;

  static const items = [
    NavItem('Today', Icons.home_outlined, Icons.home_rounded),
    NavItem('Draft', Icons.edit_note_outlined, Icons.edit_note_rounded),
    NavItem('Asks', Icons.checklist_outlined, Icons.checklist_rounded),
    NavItem('Timeline', Icons.timeline_outlined, Icons.timeline_rounded),
    NavItem('History', Icons.calendar_month_outlined, Icons.calendar_month_rounded),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      extendBody: true,
      body: _AskForNotificationsOnce(child: FluidTabSwitcher(index: shell.currentIndex, children: children)),
      bottomNavigationBar: FloatingNavBar(
        items: items,
        selectedIndex: shell.currentIndex,
        onSelected: (index) {
          unawaited(Haptics.selection());
          shell.goBranch(index, initialLocation: index == shell.currentIndex);
        },
      ),
    );
  }
}

/// The first time the app opens signed in, Android 13+ asks whether WorkDigest may notify
/// ("Send your digest", "a new version is live"). Asked once; the answer is Android's to keep.
/// It also keeps the weekday send reminders scheduled while the app is in use.
class _AskForNotificationsOnce extends ConsumerStatefulWidget {
  const _AskForNotificationsOnce({required this.child});

  final Widget child;

  @override
  ConsumerState<_AskForNotificationsOnce> createState() => _AskForNotificationsOnceState();
}

class _AskForNotificationsOnceState extends ConsumerState<_AskForNotificationsOnce> {
  static const _key = 'asked_notifications';

  @override
  void initState() {
    super.initState();
    final prefs = ref.read(preferencesProvider);
    if (prefs.getBool(_key) ?? false) return;
    unawaited(prefs.setBool(_key, true));
    WidgetsBinding.instance.addPostFrameCallback((_) => unawaited(askNotificationPermission()));
  }

  @override
  Widget build(BuildContext context) {
    ref.watch(reminderSyncProvider); // weekday "send your digest" reminders stay scheduled
    return widget.child;
  }
}

/// Switches tabs with depth: the new tab drifts in from the side it lives on and settles,
/// while the old one drifts the other way and fades. Tabs keep their state; tabs that are not
/// involved in a switch are offstage (no painting, no tickers).
class FluidTabSwitcher extends StatefulWidget {
  const FluidTabSwitcher({required this.index, required this.children, super.key});

  final int index;
  final List<Widget> children;

  @override
  State<FluidTabSwitcher> createState() => _FluidTabSwitcherState();
}

class _FluidTabSwitcherState extends State<FluidTabSwitcher> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(vsync: this, duration: Motion.tab, value: 1);
  late final Animation<double> _t = CurvedAnimation(parent: _controller, curve: Motion.fluid);
  int? _previous;

  @override
  void didUpdateWidget(FluidTabSwitcher old) {
    super.didUpdateWidget(old);
    if (old.index == widget.index) return;
    if (!Motion.enabled(context)) {
      _previous = null;
      _controller.value = 1;
      return;
    }
    _previous = old.index;
    unawaited(
      _controller.forward(from: 0).whenComplete(() {
        if (mounted) setState(() => _previous = null);
      }),
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final previous = _previous;
    final direction = previous == null ? 0.0 : (widget.index > previous ? 1.0 : -1.0);
    return Stack(
      fit: StackFit.expand,
      children: [
        for (var i = 0; i < widget.children.length; i++)
          if (i == widget.index)
            AnimatedBuilder(
              key: ValueKey('tab-$i'),
              animation: _t,
              child: widget.children[i],
              builder: (context, child) {
                if (previous == null) return child!;
                final t = _t.value;
                return Opacity(
                  opacity: t.clamp(0.0, 1.0),
                  child: Transform.translate(
                    offset: Offset(36 * direction * (1 - t), 0),
                    child: Transform.scale(scale: 0.97 + 0.03 * t, child: child),
                  ),
                );
              },
            )
          else if (i == previous)
            IgnorePointer(
              key: ValueKey('tab-$i'),
              child: ExcludeSemantics(
                child: AnimatedBuilder(
                  animation: _t,
                  child: widget.children[i],
                  builder: (context, child) => Opacity(
                    opacity: (1 - _t.value).clamp(0.0, 1.0),
                    child: Transform.translate(offset: Offset(-24 * direction * _t.value, 0), child: child),
                  ),
                ),
              ),
            )
          else
            Offstage(key: ValueKey('tab-$i'), child: TickerMode(enabled: false, child: widget.children[i])),
      ],
    );
  }
}

/// A glass pill floating above the content. The selected tab sits in a glowing bubble that
/// flows to the next tab like a liquid: its leading edge stretches ahead, the trailing edge
/// catches up, and it settles back into a circle.
class FloatingNavBar extends StatefulWidget {
  const FloatingNavBar({required this.items, required this.selectedIndex, required this.onSelected, super.key});

  final List<NavItem> items;
  final int selectedIndex;
  final ValueChanged<int> onSelected;

  static const double height = 72;
  static const double bubble = 56;

  @override
  State<FloatingNavBar> createState() => _FloatingNavBarState();
}

class _FloatingNavBarState extends State<FloatingNavBar> with SingleTickerProviderStateMixin {
  late final AnimationController _flow = AnimationController(vsync: this, duration: const Duration(milliseconds: 560), value: 1);
  late int _from = widget.selectedIndex;

  @override
  void didUpdateWidget(FloatingNavBar old) {
    super.didUpdateWidget(old);
    if (old.selectedIndex == widget.selectedIndex) return;
    _from = old.selectedIndex;
    if (Motion.enabled(context)) {
      unawaited(_flow.forward(from: 0));
    } else {
      _flow.value = 1;
    }
  }

  @override
  void dispose() {
    _flow.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final aurora = context.aurora;
    final scheme = Theme.of(context).colorScheme;
    final glass = glassEnabled(context);
    final bottom = MediaQuery.paddingOf(context).bottom;
    const height = FloatingNavBar.height;
    const bubble = FloatingNavBar.bubble;
    Widget bar = DecoratedBox(
      decoration: BoxDecoration(
        color: glass ? aurora.glassStrong : scheme.surfaceContainerHigh,
        borderRadius: Radii.pillRadius,
        border: Border.all(color: aurora.hairline),
        boxShadow: const [BoxShadow(color: Color(0x40000000), blurRadius: 24, offset: Offset(0, 10))],
      ),
      child: SizedBox(
        height: height,
        child: LayoutBuilder(
          builder: (context, constraints) {
            final slot = constraints.maxWidth / widget.items.length;
            double centre(int i) => slot * i + slot / 2;
            return Stack(
              children: [
                AnimatedBuilder(
                  animation: _flow,
                  builder: (context, _) {
                    final t = _flow.value;
                    final to = widget.selectedIndex;
                    final forward = to >= _from;
                    // The leading edge moves first and fast; the trailing edge follows and settles.
                    final lead = Motion.fluid.transform(const Interval(0, 0.75).transform(t));
                    final trail = Motion.settle.transform(const Interval(0.18, 1).transform(t));
                    final a = lerpDouble(centre(_from), centre(to), forward ? trail : lead)!;
                    final b = lerpDouble(centre(_from), centre(to), forward ? lead : trail)!;
                    return Positioned(
                      left: math.min(a, b) - bubble / 2,
                      top: (height - bubble) / 2,
                      child: IgnorePointer(
                        child: Container(
                          width: (b - a).abs() + bubble,
                          height: bubble,
                          decoration: BoxDecoration(
                            borderRadius: Radii.pillRadius,
                            gradient: LinearGradient(colors: aurora.accentGradient, begin: Alignment.topLeft, end: Alignment.bottomRight),
                            boxShadow: [BoxShadow(color: aurora.accentGradient.first.withValues(alpha: 0.5), blurRadius: 18)],
                          ),
                        ),
                      ),
                    );
                  },
                ),
                Row(
                  children: [
                    for (var i = 0; i < widget.items.length; i++)
                      Expanded(
                        child: _NavButton(
                          item: widget.items[i],
                          selected: i == widget.selectedIndex,
                          onTap: () => widget.onSelected(i),
                        ),
                      ),
                  ],
                ),
              ],
            );
          },
        ),
      ),
    );
    if (glass) {
      bar = ClipRRect(
        borderRadius: Radii.pillRadius,
        child: BackdropFilter(filter: ImageFilter.blur(sigmaX: 24, sigmaY: 24), child: bar),
      );
    }
    return Padding(
      padding: EdgeInsets.fromLTRB(Space.m, 0, Space.m, Space.s + (bottom > 0 ? bottom : Space.s)),
      child: bar,
    );
  }
}

class _NavButton extends StatelessWidget {
  const _NavButton({required this.item, required this.selected, required this.onTap});

  final NavItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    // One node per tab: its label, selected state and tap (the icon and ink add nothing).
    return Semantics(
      container: true,
      button: true,
      enabled: true,
      selected: selected,
      label: item.label,
      onTap: onTap,
      excludeSemantics: true,
      child: Tooltip(
        message: item.label,
        child: InkResponse(
          onTap: onTap,
          radius: 32,
          child: SizedBox.expand(
            child: AnimatedScale(
              // The new tab's icon pops slightly as the bubble arrives.
              scale: selected ? 1.08 : 1,
              duration: Motion.of(context, Motion.emphasized),
              curve: Motion.settle,
              child: AnimatedSwitcher(
                duration: Motion.of(context, Motion.quick),
                child: Icon(
                  selected ? item.selectedIcon : item.icon,
                  key: ValueKey(selected),
                  size: 26,
                  color: selected ? scheme.onPrimary : scheme.onSurfaceVariant,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
