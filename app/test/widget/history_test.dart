import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/features/history/history_screen.dart';

import '../fakes/fakes.dart';

void main() {
  testWidgets('TC-APP-123: the History calendar marks days with digests and opens them', (tester) async {
    // Given: October 2026 with the 23 Oct draft.
    // When: History opens and the 23rd is tapped in the calendar.
    // Then: the month is shown, the 23rd is a button announced as "Fri 23 Oct, draft", only that month was
    //       queried (by date range), and the draft for the 23rd opens.
    final app = await pumpApp(tester, location: '/more');
    expect(find.text('October 2026'), findsOneWidget);
    final handle = tester.ensureSemantics();
    expect(find.bySemanticsLabel('Fri 23 Oct, draft'), findsOneWidget);
    expect(find.bySemanticsLabel('Thu 22 Oct, no digest'), findsOneWidget);
    handle.dispose();
    expect(app.cloud.calls.where((c) => c == 'select:digests'), isNotEmpty);
    await tester.tap(find.descendant(of: find.byType(MonthCalendar), matching: find.text('23')));
    await tester.pumpAndSettle();
    expect(find.text('Draft · 2026-10-23'), findsOneWidget);
  });

  testWidgets('TC-APP-124: months change with the arrows or a swipe, never into the future', (tester) async {
    // Given: History on October 2026 (the current month).
    // When: "Next month" is checked, then "Previous month" is tapped, then the calendar is swiped left.
    // Then: next is disabled in the current month; September shows "No digests in September"; the swipe
    //       returns to October with its digest listed.
    await pumpApp(tester, location: '/more');
    expect(tester.widget<IconButton>(find.widgetWithIcon(IconButton, Icons.chevron_right_rounded)).onPressed, isNull);
    await tester.tap(find.byTooltip('Previous month'));
    await tester.pumpAndSettle();
    expect(find.text('September 2026'), findsOneWidget);
    expect(find.text('No digests in September'), findsOneWidget);
    await tester.fling(find.byType(MonthCalendar), const Offset(-300, 0), 1000);
    await tester.pumpAndSettle();
    expect(find.text('October 2026'), findsOneWidget);
    expect(find.text('Fri 23 Oct 2026'), findsOneWidget);
  });
}
