import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../fakes/fakes.dart';

void main() {
  testWidgets('TC-APP-130: an open ask offers only Done and Disagree, and keeps real-action, raised-count, ticket and date', (tester) async {
    // Given: the open OTP ask (needs a real action, raised 3 times, about EIT-8403, since 21 Oct).
    // When: the Open tab is shown.
    // Then: its chips are shown and the only actions are Done and Disagree (no Waiting).
    await pumpApp(tester, location: '/asks');
    expect(find.text('Needs real action'), findsOneWidget);
    expect(find.text('Raised 3×'), findsOneWidget);
    expect(find.text('EIT-8403'), findsOneWidget);
    expect(find.text('Since 2026-10-21'), findsOneWidget);
    expect(find.text('Done'), findsWidgets);
    expect(find.text('Disagree'), findsWidgets);
    expect(find.text('Waiting'), findsNothing);
  });

  testWidgets('TC-APP-131: "Clear all" clears every open ask after confirming; cleared asks are not listed as closed', (tester) async {
    // Given: two open asks and one closed (done) ask.
    // When: Clear all is tapped, cancelled, tapped again and confirmed, then the Closed tab is opened.
    // Then: cancelling changes nothing; confirming marks both open asks cleared (with today's date) and empties
    //       the Open tab; Closed still shows only the done ask.
    final app = await pumpApp(tester, location: '/asks');
    await tester.tap(find.text('Clear all'));
    await tester.pumpAndSettle();
    expect(find.text('Clear 2 open asks?'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(app.cloud.tables['asks']!.where((a) => a['status'] == 'open'), hasLength(2));
    await tester.tap(find.text('Clear all'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Clear'));
    await tester.pumpAndSettle();
    final cleared = app.cloud.tables['asks']!.where((a) => a['status'] == 'cleared').toList();
    expect(cleared, hasLength(2));
    expect(cleared.every((a) => a['closed_on'] == '2026-10-23' && a['updated_by'] == 'app'), isTrue);
    expect(find.text('No open asks'), findsOneWidget);
    await tester.tap(find.text('Closed'));
    await tester.pumpAndSettle();
    expect(find.text('Add Activity comments on EIT-8403'), findsOneWidget);
    expect(find.text('Raise a Jira bug for the OTP fix'), findsNothing);
  });

  testWidgets('TC-APP-132: the Closed tab filters by the day asks were closed', (tester) async {
    // Given: three closed asks: two on Fri 23 Oct (today in the test), one on Wed 21 Oct.
    // When: the Closed tab opens, then "Wed 21 Oct" is chosen, then "All".
    // Then: the chips show counts per day; filtering shows only that day's ask; All shows all three.
    final cloud = seededCloud()
      ..seed('asks', [
        {'ask_id': 'c2', 'text': 'Share repo links', 'kind': 'one_time_action', 'status': 'done', 'evidence': 'https://bitbucket.org/x', 'opened_on': '2026-10-20', 'closed_on': '2026-10-23'},
        {'ask_id': 'c3', 'text': 'Do you have 10 agents?', 'kind': 'question', 'status': 'disagree', 'reason': '3 are enough', 'opened_on': '2026-10-20', 'closed_on': '2026-10-21'},
      ]);
    await pumpApp(tester, cloud: cloud, location: '/asks');
    await tester.tap(find.text('Closed'));
    await tester.pumpAndSettle();
    expect(find.text('All (3)'), findsOneWidget);
    expect(find.text('Today (2)'), findsOneWidget);
    expect(find.text('Wed 21 Oct (1)'), findsOneWidget);
    expect(find.byTooltip('Pick a day'), findsOneWidget);
    await tester.ensureVisible(find.text('Wed 21 Oct (1)'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Wed 21 Oct (1)'));
    await tester.pumpAndSettle();
    expect(find.text('Do you have 10 agents?'), findsOneWidget);
    expect(find.text('Share repo links'), findsNothing);
    await tester.ensureVisible(find.text('All (3)'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('All (3)'));
    await tester.pumpAndSettle();
    expect(find.text('Share repo links'), findsOneWidget);
  });

  testWidgets("TC-APP-133: \"Clear all\" switches off Priya's rules but never my own", (tester) async {
    // Given: two seeded Priya's rules and one active rule of mine.
    // When: Priya's rules are cleared and confirmed.
    // Then: both are inactive (marked as app edits) and disappear from the list; my rule is untouched.
    final cloud = seededCloud()
      ..seed('standing_rules', [
        {'rule_id': 'mine-1', 'text': 'Keep it short', 'check_kind': 'filter', 'source': 'mine', 'status': 'active', 'active': true, 'check_params': {'instruction': 'Keep it short'}},
      ]);
    await pumpApp(tester, cloud: cloud, location: '/asks');
    await tester.tap(find.text('Rules'));
    await tester.pumpAndSettle();
    expect(find.text('Follow the 11-point structure'), findsOneWidget);
    await tester.tap(find.text('Clear all'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Clear'));
    await tester.pumpAndSettle();
    final rules = {for (final r in cloud.tables['standing_rules']!) r['rule_id']: r};
    expect(rules['R01-structure']!['active'], isFalse);
    expect(rules['R10-send-time']!['active'], isFalse);
    expect(rules['R01-structure']!['updated_by'], 'app');
    expect(rules['mine-1']!['active'], isTrue);
    expect(find.text('Follow the 11-point structure'), findsNothing);
    expect(find.text('Keep it short'), findsOneWidget);
  });

  testWidgets('TC-APP-134: one open ask can be deleted from its menu or by swiping, with Undo', (tester) async {
    // Given: the open OTP ask and the open agent-crew question.
    // When: the OTP ask is deleted from its menu, Undo is tapped, then the question is swiped away.
    // Then: the OTP ask is cleared and leaves the list at once; Undo makes it open again (no close date);
    //       the swiped question is cleared; Clear all is not needed for either.
    final app = await pumpApp(tester, location: '/asks');
    Map<String, dynamic> row(String id) => app.cloud.tables['asks']!.firstWhere((a) => a['ask_id'] == id);
    await tester.tap(find.byTooltip('Ask options').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();
    expect(row('a-otp')['status'], 'cleared');
    expect(find.text('Raise a Jira bug for the OTP fix'), findsNothing);
    await tester.tap(find.text('Undo'));
    await tester.pumpAndSettle();
    expect(row('a-otp')['status'], 'open');
    expect(row('a-otp')['closed_on'], isNull);
    expect(find.text('Raise a Jira bug for the OTP fix'), findsOneWidget);

    await tester.drag(find.text('Do you have a crew of at least 10 agents?'), const Offset(-500, 0));
    await tester.pumpAndSettle();
    expect(row('a-crew')['status'], 'cleared');
    expect(find.text('Do you have a crew of at least 10 agents?'), findsNothing);
  });

  testWidgets("TC-APP-135: one of Priya's rules can be deleted, with Undo", (tester) async {
    // Given: the Rules tab with two of Priya's rules.
    // When: the first rule's delete button is tapped, then Undo.
    // Then: that rule is switched off and leaves the list while the other stays; Undo switches it back on.
    final app = await pumpApp(tester, location: '/asks');
    await tester.tap(find.text('Rules'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Delete rule').first);
    await tester.pumpAndSettle();
    final rule = app.cloud.tables['standing_rules']!.firstWhere((r) => r['rule_id'] == 'R01-structure');
    expect(rule['active'], isFalse);
    expect(find.text('Follow the 11-point structure'), findsNothing);
    expect(find.text('Send before 9pm'), findsOneWidget);
    await tester.tap(find.text('Undo'));
    await tester.pumpAndSettle();
    expect(rule['active'], isTrue);
    expect(find.text('Follow the 11-point structure'), findsOneWidget);
  });
}
