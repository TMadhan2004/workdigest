import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/features/draft_editor/draft_widgets.dart';

import '../fakes/fakes.dart';

const _screens = ['/today', '/draft', '/asks', '/timeline', '/more', '/today/profile/settings', '/reply'];

void main() {
  for (final brightness in Brightness.values) {
    for (final location in _screens) {
      testWidgets('TC-APP-080: $location meets tap-target, label and contrast guidelines (${brightness.name})', (tester) async {
        // Given: the screen at `location` with real data, in the given theme.
        // When: Flutter's accessibility guidelines are evaluated.
        // Then: tap targets are at least 48 dp (Android) and 44 pt (iOS), every tappable element has a
        //       label, and text contrast meets WCAG AA.
        final handle = tester.ensureSemantics();
        await pumpApp(tester, location: location, brightness: brightness);
        await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
        await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
        await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
        await expectLater(tester, meetsGuideline(textContrastGuideline));
        handle.dispose();
      });
    }
  }

  testWidgets('TC-APP-081: large text (1.6×) lays out every main screen without overflow', (tester) async {
    // Given: the system text size at 160 %.
    // When: each main screen is shown on a small phone (360 × 740).
    // Then: no layout overflow or other exception is reported.
    for (final location in _screens) {
      await pumpApp(tester, location: location, textScale: 1.6, size: const Size(360, 740));
      expect(tester.takeException(), isNull, reason: location);
      // Unmount and flush before the next screen so auto-disposed providers finish cleanly.
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pump(const Duration(seconds: 1));
    }
  });

  testWidgets('TC-APP-082: reduced motion turns animations off (sections expand instantly)', (tester) async {
    // Given: "remove animations" enabled.
    // When: the draft opens and a section is collapsed.
    // Then: the collapse completes in a single frame (no size animation).
    await pumpApp(tester, location: '/draft', disableAnimations: true);
    final header = find.text('2. Plan vs actual');
    await tester.scrollUntilVisible(header, 300, scrollable: find.byType(Scrollable).first);
    await tester.ensureVisible(header);
    await tester.pump();
    final before = tester.getSize(find.ancestor(of: header, matching: find.byType(SectionCard))).height;
    await tester.tap(header);
    await tester.pump();
    // One frame later the section has already collapsed (only the tap's ink ripple may still run).
    final after = tester.getSize(find.ancestor(of: header, matching: find.byType(SectionCard))).height;
    expect(after, lessThan(before));
  });

  testWidgets('TC-APP-083: statements announce their state to screen readers', (tester) async {
    // Given: the draft with verified, unverified and placeholder lines.
    // When: semantics are inspected.
    // Then: each statement is a button whose label says verified, unverified or missing and how to open evidence.
    final handle = tester.ensureSemantics();
    await pumpApp(tester, location: '/draft');
    expect(find.bySemanticsLabel(RegExp(r'\. (verified|unverified|missing|no evidence needed)\. Double tap to see evidence\.$')), findsWidgets);
    handle.dispose();
  });
}
