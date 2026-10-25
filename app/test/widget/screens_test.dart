import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/core/router/router.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/features/shell/app_shell.dart';
import 'package:work_tracker/features/today/today_controller.dart';

import '../fakes/fakes.dart';

void main() {
  group('Today', () {
    testWidgets('TC-APP-020: shows the day, Mac status, draft gaps, metrics and asks', (tester) async {
      // Given: a seeded day (real 23 Oct draft, Mac seen 40 s ago, 2 open asks, 1 needing real action).
      // When: the app opens on Today.
      // Then: the date, "Mac connected", the gap count, sessions/commits and the asks summary are shown.
      await pumpApp(tester);
      expect(find.text('Friday, 23 October'), findsOneWidget);
      expect(find.text('Mac connected'), findsOneWidget);
      expect(find.textContaining('gaps to confirm'), findsOneWidget);
      expect(find.text('4'), findsOneWidget);
      expect(find.text('98'), findsOneWidget);
      expect(find.text('2 open asks'), findsOneWidget);
      expect(find.text('1 need a real action, not just wording'), findsOneWidget);
    });

    testWidgets('TC-APP-021: an offline Mac and an offline phone are both made visible', (tester) async {
      // Given: the Mac last seen 2 hours ago; then the network drops after the first load.
      // When: Today opens, then is refreshed offline.
      // Then: "Mac offline since …" appears; after the refresh the cached copy shows with the offline banner.
      final cloud = seededCloud();
      cloud.tables['devices']!.single['last_seen_at'] = fixedNow.toUtc().subtract(const Duration(hours: 2)).toIso8601String();
      final app = await pumpApp(tester, cloud: cloud);
      expect(find.textContaining('Mac offline since'), findsOneWidget);
      cloud.offline = true;
      app.container.invalidate(todayOverviewProvider);
      await tester.pumpAndSettle();
      expect(find.textContaining('Offline – showing the copy saved'), findsOneWidget);
      expect(find.text('Friday, 23 October'), findsOneWidget);
    });

    testWidgets('TC-APP-022: with no cache and no network, an error with retry is shown', (tester) async {
      // Given: an offline cloud and an empty cache.
      // When: Today opens.
      // Then: "Could not load" and a "Try again" button appear; going online and retrying shows the day.
      final cloud = seededCloud()..offline = true;
      await pumpApp(tester, cloud: cloud);
      expect(find.text('Could not load'), findsOneWidget);
      cloud.offline = false;
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Friday, 23 October'), findsOneWidget);
    });

    testWidgets('TC-APP-023: Close day needs a rating, then queues the draft with rating and team', (tester) async {
      // Given: the Today screen.
      // When: Close day is tapped, the confirm button is checked before and after picking "7 · Good",
      //       the team is typed and the sheet is confirmed.
      // Then: confirm is disabled until a rating is chosen; a full job with rating 7 and the team is
      //       queued and the draft screen shows the Mac working.
      final app = await pumpApp(tester);
      await tester.tap(find.text('Close day'));
      await tester.pumpAndSettle();
      final confirm = find.widgetWithText(FilledButton, 'Close day and draft the digest');
      expect(tester.widget<FilledButton>(confirm).onPressed, isNull);
      await tester.tap(find.text('7 · Good'));
      await tester.enterText(find.widgetWithText(TextField, 'Team members today'), 'Rahul, Vikram');
      await tester.pump();
      await tester.tap(confirm);
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      final job = app.cloud.tables['generation_jobs']!.single;
      expect(job['kind'], 'full');
      expect((job['params'] as Json)['rating'], 7);
      expect((job['params'] as Json)['team_members'], ['Rahul', 'Vikram']);
      expect(app.cloud.tables['days']!.single['rating'], 7);
      expect(find.textContaining('Waiting for the Mac'), findsOneWidget);
      // Finish the job so the polling stream completes.
      app.cloud.tables['generation_jobs']!.single['status'] = 'done';
      await tester.pump(const Duration(seconds: 4));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('1. Done today'), 300, scrollable: find.byType(Scrollable).first);
      expect(find.text('1. Done today'), findsOneWidget);
    });
  });

  group('Draft', () {
    testWidgets('TC-APP-030: sections render with gap panel, and unverified lines are marked', (tester) async {
      // Given: the real 23 Oct draft.
      // When: the Draft tab opens and the gap panel is expanded.
      // Then: numbered sections, the gap count and individual gap messages are visible.
      await pumpApp(tester, location: '/draft');
      expect(find.textContaining('gaps to confirm'), findsOneWidget);
      await tester.scrollUntilVisible(find.text('1. Done today'), 300, scrollable: find.byType(Scrollable).first);
      expect(find.text('1. Done today'), findsOneWidget);
      await tester.scrollUntilVisible(find.textContaining('gaps to confirm'), -300, scrollable: find.byType(Scrollable).first);
      await tester.tap(find.textContaining('gaps to confirm'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Needs real action'), findsWidgets);
    });

    testWidgets('TC-APP-031: tapping a statement opens its evidence with fact or claim labels', (tester) async {
      // Given: the draft, and the Testing line that cites a test-run fact.
      // When: a statement is tapped.
      // Then: the evidence sheet lists the cited items with "Fact" chips and their exact text.
      await pumpApp(tester, location: '/draft');
      final testLine = find.textContaining('billing-service jest: 714/714');
      await tester.scrollUntilVisible(testLine, 300, scrollable: find.byType(Scrollable).first);
      await tester.ensureVisible(testLine);
      await tester.pumpAndSettle();
      await tester.tap(testLine);
      await tester.pumpAndSettle();
      expect(find.text('Evidence'), findsOneWidget);
      expect(find.text('Fact'), findsWidgets);
      expect(find.textContaining('summary line'), findsWidgets);
    });

    testWidgets('TC-APP-032: Share sends the exact WhatsApp text and offers to mark it sent', (tester) async {
      // Given: the draft.
      // When: "Share to WhatsApp" is tapped and "Mark as sent" is chosen; then Copy is tapped.
      // Then: the share sheet received the full text, the digest is marked sent_marked, and the copy holds the same text.
      final app = await pumpApp(tester, location: '/draft');
      final text = app.cloud.tables['digests']!.single['text'] as String;
      await tester.tap(find.text('Share to WhatsApp'));
      await tester.pumpAndSettle();
      expect(app.share.shared, [text]);
      await tester.tap(find.text('Mark as sent'));
      await tester.pumpAndSettle();
      expect(app.cloud.tables['digests']!.single['status'], 'sent_marked');
      await tester.tap(find.byTooltip('Copy text'));
      await tester.pump();
      expect(app.share.copied, [text]);
    });

    testWidgets('TC-APP-033: editing a section saves a new edited version and leaves the rest unchanged', (tester) async {
      // Given: the draft.
      // When: "Edit" is chosen for section 9 (Learnings), the text replaced and saved.
      // Then: a new digests row with origin=edited exists, its section 9 is the new text, the other
      //       sections are unchanged, and the screen shows "Your edited version".
      final app = await pumpApp(tester, location: '/draft');
      final menu = find.byTooltip('Section actions for Learnings');
      await tester.scrollUntilVisible(menu, 300, scrollable: find.byType(Scrollable).first);
      await tester.ensureVisible(menu);
      await tester.pumpAndSettle();
      await tester.tap(menu);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Edit'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), '9. Learnings\n   1. Test against the real services.');
      await tester.tap(find.text('Save as my version'));
      await tester.pumpAndSettle();
      final rows = app.cloud.tables['digests']!;
      expect(rows, hasLength(2));
      final edited = rows.last;
      expect(edited['origin'], 'edited');
      expect(edited['text'] as String, contains('9. Learnings\n   1. Test against the real services.'));
      final original = rows.first['text'] as String;
      expect((edited['text'] as String).split('9. Learnings').first, original.split('9. Learnings').first);
      await tester.scrollUntilVisible(find.text('Your edited version'), -500, scrollable: find.byType(Scrollable).first);
      expect(find.text('Your edited version'), findsOneWidget);
    });

    testWidgets('TC-APP-034: Regenerate asks the Mac for one section and shows progress on that card', (tester) async {
      // Given: the draft.
      // When: "Regenerate" is chosen for Risks.
      // Then: a 'section' job for risks is queued and only the Risks card shows a spinner.
      final app = await pumpApp(tester, location: '/draft');
      final menu = find.byTooltip('Section actions for Risks');
      await tester.scrollUntilVisible(menu, 300, scrollable: find.byType(Scrollable).first);
      await tester.ensureVisible(menu);
      await tester.pumpAndSettle();
      await tester.tap(menu);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Regenerate'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      final job = app.cloud.tables['generation_jobs']!.single;
      expect((job['kind'], (job['params'] as Json)['section']), ('section', 'risks'));
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      job['status'] = 'done';
      await tester.pump(const Duration(seconds: 4));
      await tester.pumpAndSettle();
      expect(find.byType(CircularProgressIndicator), findsNothing);
    });

    testWidgets('TC-APP-035: a day with no draft offers "Draft it now"', (tester) async {
      // Given: no digest for 22 Oct.
      // When: /draft?day=2026-10-22 opens and "Draft it now" is tapped.
      // Then: the empty state shows and a full job for 22 Oct is queued.
      final app = await pumpApp(tester, location: '/draft?day=2026-10-22');
      expect(find.text('No draft for 2026-10-22 yet'), findsOneWidget);
      await tester.tap(find.text('Draft it now'));
      await tester.pump();
      expect((app.cloud.tables['generation_jobs']!.single['params'] as Json)['work_date'], '2026-10-22');
      app.cloud.tables['generation_jobs']!.single['status'] = 'failed';
      app.cloud.tables['generation_jobs']!.single['error'] = 'Mac offline';
      await tester.pump(const Duration(seconds: 4));
      await tester.pumpAndSettle();
      expect(find.textContaining('The Mac could not finish: Mac offline'), findsOneWidget);
    });
  });

  group('Asks', () {
    testWidgets('TC-APP-040: open asks put real actions first; done needs evidence and checks off', (tester) async {
      // Given: an OTP ask (real action, raised 3×) and a question.
      // When: Asks opens, "Done" is tapped on the OTP ask, Save is checked empty, evidence typed and saved.
      // Then: the OTP ask is listed first with its chips; Save is disabled without evidence; the cloud
      //       row becomes done with the evidence, and the ask moves to the Closed tab.
      final app = await pumpApp(tester, location: '/asks');
      expect(find.text('Needs real action'), findsOneWidget);
      expect(find.text('Raised 3×'), findsOneWidget);
      final first = tester.getTopLeft(find.text('Raise a Jira bug for the OTP fix'));
      final second = tester.getTopLeft(find.text('Do you have a crew of at least 10 agents?'));
      expect(first.dy, lessThan(second.dy));
      await tester.tap(find.widgetWithText(FilledButton, 'Done').first);
      await tester.pumpAndSettle();
      final save = find.widgetWithText(FilledButton, 'Save');
      expect(tester.widget<FilledButton>(save).onPressed, isNull);
      await tester.enterText(find.byType(TextField), 'https://ei-study.atlassian.net/browse/EIT-9999');
      await tester.pump();
      await tester.tap(save);
      await tester.pumpAndSettle();
      final row = app.cloud.tables['asks']!.firstWhere((r) => r['ask_id'] == 'a-otp');
      expect((row['status'], row['evidence']), ('done', 'https://ei-study.atlassian.net/browse/EIT-9999'));
      expect(find.text('Raise a Jira bug for the OTP fix'), findsNothing);
      await tester.tap(find.text('Closed'));
      await tester.pumpAndSettle();
      expect(find.text('Raise a Jira bug for the OTP fix'), findsOneWidget);
    });

    testWidgets('TC-APP-041: rules tab lists automatic and manual rules', (tester) async {
      // Given: two standing rules (one automatic, one manual).
      // When: the Rules tab is opened.
      // Then: both are listed with how they are checked.
      await pumpApp(tester, location: '/asks');
      await tester.tap(find.text('Rules'));
      await tester.pumpAndSettle();
      expect(find.text('Checked automatically on every draft'), findsOneWidget);
      expect(find.text('Checked by you'), findsOneWidget);
    });
  });

  group('Reply intake', () {
    testWidgets('TC-APP-050: paste → Mac extracts items → review → confirm', (tester) async {
      // Given: a "Mac" that answers an app reply with two items (an action and feedback).
      // When: the reply is pasted and submitted, items arrive, and Confirm is tapped.
      // Then: the reply is stored as source=app; feedback starts as Skip; the action is confirmed in the cloud.
      final cloud = seededCloud();
      cloud.onInsert = (table, row) {
        if (table == 'manager_replies') {
          cloud.seed('reply_items', [
            {'item_id': 'r0', 'reply_id': row['reply_id'], 'idx': 0, 'type': 'one_time_action', 'text': 'Raise the OTP bug', 'status': 'proposed', 'related_tickets': ['EIT-8403']},
            {'item_id': 'r1', 'reply_id': row['reply_id'], 'idx': 1, 'type': 'feedback', 'text': 'Excellent engineering', 'status': 'proposed'},
          ]);
        }
      };
      final app = await pumpApp(tester, cloud: cloud, location: '/reply');
      expect(find.widgetWithText(FilledButton, 'Find asks and rules'), findsOneWidget);
      await tester.enterText(find.byType(TextField), 'Hi Arjun,\n\nPlease raise the OTP bug.\nExcellent engineering.');
      await tester.pump();
      await tester.tap(find.text('Find asks and rules'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      await tester.pumpAndSettle();
      expect(cloud.tables['manager_replies']!.single['source'], 'app');
      expect(find.text('Raise the OTP bug'), findsOneWidget);
      expect(find.text('Confirm 1 of 2'), findsOneWidget);
      await tester.tap(find.text('Confirm 1 of 2'));
      await tester.pumpAndSettle();
      expect(app.cloud.tables['reply_items']!.map((r) => r['status']), ['confirmed', 'dismissed']);
    });
  });

  group('Timeline, History, Settings', () {
    testWidgets('TC-APP-060: timeline filters sessions by tool and shows open PRs', (tester) async {
      // Given: a Claude Code and a Cursor session, and identity-service #312 open since 5 Oct.
      // When: the timeline opens and the Cursor filter is chosen.
      // Then: both sessions show first, then only the Cursor one; the open PR shows its age.
      await pumpApp(tester, location: '/timeline');
      expect(find.text('Move billing profiles and credits to billing'), findsOneWidget);
      expect(find.text('UI tweaks'), findsOneWidget);
      expect(find.textContaining('identity-service #312'), findsOneWidget);
      expect(find.textContaining('no approval yet'), findsOneWidget);
      await tester.tap(find.widgetWithText(ChoiceChip, 'Cursor'));
      await tester.pumpAndSettle();
      expect(find.text('Move billing profiles and credits to billing'), findsNothing);
      expect(find.text('UI tweaks'), findsOneWidget);
    });

    testWidgets('TC-APP-061: history lists one entry per day and opens it', (tester) async {
      // Given: the 23 Oct draft.
      // When: History opens and the entry is tapped.
      // Then: "Fri 23 Oct 2026" with a Draft chip is listed, and the draft for that day opens.
      await pumpApp(tester, location: '/more');
      expect(find.text('Fri 23 Oct 2026'), findsOneWidget);
      await tester.tap(find.text('Fri 23 Oct 2026'));
      await tester.pumpAndSettle();
      expect(find.text('Draft · 2026-10-23'), findsOneWidget);
    });

    testWidgets('TC-APP-062: settings save provider and transmit choices to the cloud', (tester) async {
      // Given: default settings.
      // When: "No LLM (facts only)" is chosen and "Names of files edited" is switched off.
      // Then: the settings row has provider none and transmit.file_paths false.
      final app = await pumpApp(tester, location: '/today/profile/settings');
      await tester.tap(find.text('No LLM (facts only)'));
      await tester.pumpAndSettle();
      final toggle = find.widgetWithText(SwitchListTile, 'Names of files edited');
      await tester.scrollUntilVisible(toggle, 200, scrollable: find.byType(Scrollable).first);
      await tester.ensureVisible(toggle);
      await tester.pumpAndSettle();
      await tester.tap(toggle);
      await tester.pumpAndSettle();
      final row = app.cloud.tables['settings']!.single;
      expect(row['provider'], 'none');
      expect((row['transmit'] as Map)['file_paths'], isFalse);
    });

    testWidgets('TC-APP-063: signed-out users land on sign-in; a wrong password shows an error', (tester) async {
      // Given: a signed-out cloud.
      // When: the app opens, a wrong password is tried, then the right one.
      // Then: the sign-in screen shows, the error appears, and the right password lands on Today.
      final cloud = seededCloud()..signedIn = false;
      await pumpApp(tester, cloud: cloud);
      expect(find.text('Sign in'), findsOneWidget);
      await tester.enterText(find.widgetWithText(TextFormField, 'Work email'), 'arjun.k@ei.study');
      await tester.enterText(find.widgetWithText(TextFormField, 'Password'), 'wrong');
      await tester.tap(find.text('Sign in'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Wrong email or password'), findsOneWidget);
      await tester.enterText(find.widgetWithText(TextFormField, 'Password'), 'right');
      await tester.tap(find.text('Sign in'));
      await tester.pumpAndSettle();
      expect(find.text('Friday, 23 October'), findsOneWidget);
    });
  });

  testWidgets('TC-APP-070: the floating navigation bar reaches every area', (tester) async {
    // Given: the app on Today.
    // When: each tab of the floating bar is tapped (found by its label, as a screen reader would).
    // Then: Draft, Asks & rules, Timeline and History open, and the selected tab is announced as selected.
    final app = await pumpApp(tester);
    final handle = tester.ensureSemantics();
    expect(tester.getSemantics(find.bySemanticsLabel('Today')), matchesSemantics(label: 'Today', isButton: true, isSelected: true, hasSelectedState: true, hasTapAction: true, hasEnabledState: true, isEnabled: true));
    handle.dispose();
    for (final (label, title) in const [('Draft', 'Draft · 2026-10-23'), ('Asks', 'Asks & rules'), ('Timeline', 'Timeline'), ('History', 'History')]) {
      await tester.tap(find.descendant(of: find.byType(FloatingNavBar), matching: find.byTooltip(label)));
      await tester.pumpAndSettle();
      expect(find.descendant(of: find.byType(AuroraHeader), matching: find.text(title)), findsOneWidget, reason: label);
    }
    expect(app.container.read(routerProvider).routerDelegate.currentConfiguration.uri.path, '/more');
  });
}
