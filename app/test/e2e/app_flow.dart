import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/data/models/json.dart';

import '../fakes/fakes.dart';

/// The evening flow end to end: sign in → reply → close day → Mac drafts → review → share.
///
/// Shared by the headless test (`test/e2e/app_flow_test.dart`) and the on-device integration
/// test (`integration_test/app_test.dart`).
Future<void> runEveningFlow(WidgetTester tester) async {
  final cloud = seededCloud()..signedIn = false;
  // The digest for the day only appears once the "Mac" finishes the job.
  final draft = cloud.tables.remove('digests')!.single;
  cloud.onInsert = (table, row) {
    if (table == 'manager_replies') {
      cloud.seed('reply_items', [
        {'item_id': 'e0', 'reply_id': row['reply_id'], 'idx': 0, 'type': 'one_time_action', 'text': 'Log the empty-DELETE bug', 'status': 'proposed'},
      ]);
    }
  };
  final app = await pumpApp(tester, cloud: cloud);

  // 1. Sign in.
  await tester.enterText(find.widgetWithText(TextFormField, 'Work email'), 'arjun.k@ei.study');
  await tester.enterText(find.widgetWithText(TextFormField, 'Password'), 'right');
  await tester.tap(find.text('Sign in'));
  await tester.pumpAndSettle();
  expect(find.text('Friday, 23 October'), findsOneWidget);

  // 2. Add Priya's reply and confirm the extracted ask.
  await tester.tap(find.text("Add Priya's reply"));
  await tester.pumpAndSettle();
  await tester.enterText(find.byType(TextField), 'Hi Arjun,\n\nPlease log the empty-DELETE bug.');
  await tester.pump();
  await tester.tap(find.text('Find asks and rules'));
  await tester.pump();
  await tester.pumpAndSettle();
  await tester.tap(find.text('Confirm 1 of 1'));
  await tester.pumpAndSettle();
  expect(cloud.tables['reply_items']!.single['status'], 'confirmed');
  // Let the "items confirmed" snackbar time out so it does not cover the next sheet.
  await tester.pump(const Duration(seconds: 5));
  await tester.pumpAndSettle();

  // 3. Close the day; the Mac picks up the job and writes the draft.
  await tester.scrollUntilVisible(find.text('Close day'), 300, scrollable: find.byType(Scrollable).first);
  await tester.ensureVisible(find.text('Close day'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Close day'));
  await tester.pumpAndSettle();
  await tester.ensureVisible(find.text('9 · Super fantastic'));
  await tester.tap(find.text('9 · Super fantastic'));
  await tester.pump();
  await tester.ensureVisible(find.text('Close day and draft the digest'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Close day and draft the digest'));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 500));
  expect(find.textContaining('Waiting for the Mac'), findsOneWidget);
  final job = cloud.tables['generation_jobs']!.single;
  expect((job['params'] as Json)['rating'], 9);
  cloud.seed('digests', [
    {...draft, 'created_at': '2026-10-23T12:00:00.000Z'},
  ]);
  job['status'] = 'done';
  await tester.pump(const Duration(seconds: 4));
  await tester.pumpAndSettle();

  // 4. Review and share.
  expect(find.textContaining('gaps to confirm'), findsOneWidget);
  await tester.tap(find.text('Share to WhatsApp'));
  await tester.pumpAndSettle();
  expect(app.share.shared.single, startsWith('Greetings Priya,'));
  await tester.tap(find.text('Mark as sent'));
  await tester.pumpAndSettle();
  expect(cloud.tables['digests']!.single['status'], 'sent_marked');
}
