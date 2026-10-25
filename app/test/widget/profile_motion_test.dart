import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/features/profile/profile_screen.dart';
import 'package:work_tracker/features/shell/app_shell.dart';

import '../fakes/fakes.dart';

void main() {
  group('Profile', () {
    testWidgets('TC-APP-126: the avatar opens the profile; Settings and back return the right way; sign out asks first', (tester) async {
      // Given: Today, signed in as arjun.k@ei.study.
      // When: the avatar is tapped, then Settings, then back, then Sign out (cancel, then confirm).
      // Then: the profile shows name, email, this month and the Mac; Settings opens and back returns to the
      //       profile (not History); cancelling keeps the user signed in; confirming signs out to the sign-in page.
      final app = await pumpApp(tester);
      expect(find.text('AK'), findsOneWidget);
      await tester.tap(find.byTooltip('Profile'));
      await tester.pumpAndSettle();
      expect(find.byType(ProfileScreen), findsOneWidget);
      expect(find.text('Arjun K'), findsOneWidget);
      expect(find.text('arjun.k@ei.study'), findsWidgets);
      expect(find.text('Sent this month'), findsOneWidget);
      expect(find.text('Your Mac'), findsOneWidget);
      expect(find.text('Online'), findsOneWidget);

      await tester.tap(find.text('Settings'));
      await tester.pumpAndSettle();
      expect(find.text('Appearance'), findsOneWidget);
      await tester.tap(find.byTooltip('Back'));
      await tester.pumpAndSettle();
      expect(find.byType(ProfileScreen), findsOneWidget);

      await tester.scrollUntilVisible(find.text('Sign out'), 300, scrollable: find.byType(Scrollable).first);
      await tester.tap(find.text('Sign out'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(app.cloud.signedIn, isTrue);
      await tester.tap(find.text('Sign out'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Sign out'));
      await tester.pumpAndSettle();
      expect(app.cloud.signedIn, isFalse);
      expect(find.text('Sign in'), findsWidgets);
    });

    testWidgets('TC-APP-127: Today has the bell top-right and no sync pill; History has no Settings button', (tester) async {
      // Given: Today on a 412 dp wide phone.
      // When: the header is laid out, then History is opened.
      // Then: the bell's circle mirrors the avatar (same size, 16 dp from the right edge, same top), there
      //       is no "Live" pill, and History shows no Settings button.
      await pumpApp(tester);
      final avatar = tester.getRect(find.byType(ProfileAvatar));
      final bell = tester.getRect(find.ancestor(of: find.byTooltip('Notifications'), matching: find.byType(Material)).first);
      expect(bell.size, avatar.size);
      expect(412 - bell.right, moreOrLessEquals(Space.m, epsilon: 0.5));
      expect(bell.top, avatar.top);
      expect(find.text('Live'), findsNothing);
      await tester.tap(find.byTooltip('History'));
      await tester.pumpAndSettle();
      expect(find.byTooltip('Settings'), findsNothing);
    });
  });

  group('Motion', () {
    testWidgets('TC-APP-128: tabs cross over with depth and the nav bubble stretches like a liquid, then settles', (tester) async {
      // Given: Today.
      // When: Timeline is tapped and the transition is sampled half-way and at the end.
      // Then: half-way both tabs are on screen and the bubble is wider than a circle; at the end only Timeline
      //       remains and the bubble is a 56 dp circle again.
      await pumpApp(tester);
      Finder bubble() => find.descendant(
        of: find.byType(FloatingNavBar),
        matching: find.byWidgetPredicate((w) => w is Container && w.constraints?.maxHeight == FloatingNavBar.bubble),
      );
      expect(tester.getSize(bubble()).width, FloatingNavBar.bubble);
      await tester.tap(find.byTooltip('Timeline'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 160));
      expect(tester.getSize(bubble()).width, greaterThan(FloatingNavBar.bubble + 20));
      expect(find.text('Timeline'), findsWidgets);
      expect(find.textContaining('gaps to confirm'), findsOneWidget);
      await tester.pumpAndSettle();
      expect(tester.getSize(bubble()).width, FloatingNavBar.bubble);
      expect(find.textContaining('gaps to confirm'), findsNothing);
    });

    testWidgets('TC-APP-129: under reduced motion tabs and pages switch instantly', (tester) async {
      // Given: "Remove animations" on.
      // When: Timeline is tapped and one frame is pumped; then a page transition is built.
      // Then: Timeline is already the only tab on screen; the page transition returns the page untouched.
      await pumpApp(tester, disableAnimations: true);
      await tester.tap(find.byTooltip('Timeline'));
      await tester.pump();
      expect(find.textContaining('gaps to confirm'), findsNothing);
      final context = tester.element(find.byType(FloatingNavBar));
      const page = SizedBox(key: ValueKey('page'));
      final route = MaterialPageRoute<void>(builder: (_) => page);
      final built = const FluidPageTransitionsBuilder().buildTransitions(route, context, kAlwaysCompleteAnimation, kAlwaysDismissedAnimation, page);
      expect(built, same(page));
    });
  });
}
