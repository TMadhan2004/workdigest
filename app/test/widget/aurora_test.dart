import 'dart:ui' show ImageFilter;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/data/providers.dart';

import '../fakes/fakes.dart';

Widget _host(Widget child, {bool highContrast = false}) => MaterialApp(
  theme: AppTheme.dark(),
  home: MediaQuery(
    data: MediaQueryData(highContrast: highContrast),
    child: Scaffold(body: Center(child: child)),
  ),
);

void main() {
  testWidgets('TC-APP-110: the appearance setting switches the theme and is remembered', (tester) async {
    // Given: the app opened with the theme following a light system setting.
    // When: Settings → Appearance → Dark is chosen.
    // Then: the app renders the dark Aurora theme and "dark" is saved on the device.
    final app = await pumpApp(tester, location: '/today/profile/settings');
    expect(Theme.of(tester.element(find.text('Appearance'))).brightness, Brightness.light);
    await tester.tap(find.text('Dark'));
    await tester.pumpAndSettle();
    expect(Theme.of(tester.element(find.text('Appearance'))).brightness, Brightness.dark);
    expect(app.container.read(preferencesProvider).getString('theme_mode'), 'dark');
  });

  testWidgets('TC-APP-111: glass turns solid when the user asks for less motion or more contrast', (tester) async {
    // Given: a blurred glass card.
    // When: it is shown normally, then with high contrast, then with animations disabled.
    // Then: only the normal case uses a backdrop blur; the others render a solid surface.
    const card = GlassCard(blur: true, child: Text('Glass'));
    await tester.pumpWidget(_host(card));
    expect(find.byType(BackdropFilter), findsOneWidget);
    expect(tester.widget<BackdropFilter>(find.byType(BackdropFilter)).filter, isA<ImageFilter>());
    await tester.pumpWidget(_host(card, highContrast: true));
    expect(find.byType(BackdropFilter), findsNothing);
    tester.platformDispatcher.accessibilityFeaturesTestValue = const FakeAccessibilityFeatures(disableAnimations: true);
    addTearDown(tester.platformDispatcher.clearAccessibilityFeaturesTestValue);
    await tester.pumpWidget(MaterialApp(theme: AppTheme.dark(), home: const Scaffold(body: Center(child: card))));
    expect(find.byType(BackdropFilter), findsNothing);
  });

  testWidgets('TC-APP-112: "Draft now" asks the Mac for a fresh draft and opens its progress', (tester) async {
    // Given: Today with no job running.
    // When: the "Draft now" quick action is tapped.
    // Then: a full job for the day is queued and the Draft screen shows the Mac working on it.
    final app = await pumpApp(tester);
    await tester.tap(find.text('Draft now'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    final job = app.cloud.tables['generation_jobs']!.single;
    expect(job['kind'], 'full');
    expect(job['params'], {'work_date': '2026-10-23'});
    expect(find.textContaining('Waiting for the Mac'), findsOneWidget);
    app.cloud.macWrites('generation_jobs', [{'id': job['id'], 'status': 'done'}]);
    await tester.pumpAndSettle();
  });

  testWidgets('TC-APP-113: the bell shows an unread dot and opens the notifications', (tester) async {
    // Given: one unread "Draft ready" notification.
    // When: Today opens and the bell is tapped.
    // Then: the notification is shown inline and in the sheet the bell opens.
    final cloud = seededCloud()
      ..seed('notifications', [
        {'id': 'n1', 'kind': 'draft_ready', 'title': 'Draft ready – send before 9pm', 'body': 'Fri 23 Oct: 3 gaps to confirm', 'created_at': '2026-10-23T14:31:00Z'},
      ]);
    await pumpApp(tester, cloud: cloud);
    expect(find.text('Draft ready – send before 9pm'), findsOneWidget);
    await tester.tap(find.byTooltip('Notifications'));
    await tester.pumpAndSettle();
    expect(find.text('Notifications'), findsWidgets);
    expect(find.text('Draft ready – send before 9pm'), findsNWidgets(2));
  });

  testWidgets('TC-APP-114: numbers count up to their exact value and announce it once', (tester) async {
    // Given: a CountUp for 98.
    // When: the animation runs to the end.
    // Then: it starts below 98, ends showing "98", and screen readers hear "98".
    final handle = tester.ensureSemantics();
    await tester.pumpWidget(_host(const CountUp(value: 98)));
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text('98'), findsNothing);
    await tester.pumpAndSettle();
    expect(find.text('98'), findsOneWidget);
    expect(find.bySemanticsLabel('98'), findsOneWidget);
    handle.dispose();
  });

  testWidgets('TC-APP-125: each quick action, tab and status is its own screen-reader item', (tester) async {
    // Given: Today with the Mac connected.
    // When: semantics are inspected.
    // Then: "Draft now", "Add Priya's reply" and "Close day" are three separate buttons (not one merged
    //       label), and the Mac status and the profile avatar are separate items.
    final handle = tester.ensureSemantics();
    await pumpApp(tester);
    for (final label in const ['Draft now', "Add Priya's reply", 'Close day']) {
      expect(find.bySemanticsLabel(label), findsOneWidget, reason: label);
      expect(tester.getSemantics(find.bySemanticsLabel(label)).label, label);
    }
    expect(find.bySemanticsLabel('Mac connected'), findsOneWidget);
    expect(find.bySemanticsLabel('Profile'), findsOneWidget);
    handle.dispose();
  });
}
