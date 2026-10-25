import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/features/reminders/reminders.dart';
import 'package:work_tracker/features/settings/settings_screen.dart';

import '../fakes/fakes.dart';

void main() {
  test('TC-APP-147: reminders come an hour and a half before the send time, on weekdays only', () {
    // Given: Friday 23 Oct 2026 at 15:00, send by 18:00, manager Priya.
    // When: the next 14 days are planned, then with today already sent, then at 17:00.
    // Then: Fri 16:30 first, no Saturday or Sunday, 10 weekdays; today is dropped when sent or past.
    final friday = DateTime(2026, 10, 23, 15);
    final plan = plannedReminders(now: friday, sendBy: '18:00', manager: 'Priya');
    expect(plan.first.at, DateTime(2026, 10, 23, 16, 30));
    expect(plan.first.body, "Review today's draft and send it to Priya by 18:00.");
    expect(plan.map((r) => r.at.weekday), everyElement(lessThanOrEqualTo(DateTime.friday)));
    expect(plan, hasLength(10));
    expect(plannedReminders(now: friday, sendBy: '18:00', manager: 'Priya', sentToday: true).first.at, DateTime(2026, 10, 26, 16, 30));
    expect(plannedReminders(now: DateTime(2026, 10, 23, 17), sendBy: '18:00', manager: '').first.at.day, 26);
    expect((draftTimeFor('18:00'), reminderTimeFor('18:00'), draftTimeFor('01:00')), ('16:00', '16:30', '00:00'));
  });

  testWidgets('TC-APP-148: the app keeps the reminders scheduled and skips a day marked sent', (tester) async {
    // Given: Friday 18:00 in Asia/Kolkata, send by 21:00 (the seeded settings), today's digest a draft.
    // When: the app opens, then today's digest is marked sent.
    // Then: Fri 19:30 and the next weekdays are scheduled in the account's time zone; after
    //       marking sent, Friday's reminder is gone and Monday 19:30 comes first.
    final reminders = FakeReminderPlugin();
    final app = await pumpApp(tester, reminders: reminders);
    await tester.pumpAndSettle();
    expect(reminders.sorted.first.at, DateTime(2026, 10, 23, 19, 30));
    expect(reminders.zones.toSet(), {'Asia/Kolkata'});
    expect(reminders.sorted.where((r) => r.at.weekday > DateTime.friday), isEmpty);
    for (final digest in app.cloud.tables['digests']!.where((d) => d['work_date'] == '2026-10-23')) {
      digest['status'] = 'sent_marked';
      app.cloud.emit('digests', digest);
    }
    await tester.pumpAndSettle();
    expect(reminders.sorted.first.at, DateTime(2026, 10, 26, 19, 30));
  });

  testWidgets('TC-APP-149: choosing the send time sets the draft time and the reminders can be turned off', (tester) async {
    // Given: Settings, send by 21:00.
    // When: 18:00 is picked, then "Remind me on this phone" is switched off.
    // Then: the cloud gets send_deadline 18:00 and nudge_time 16:00; the texts follow; no reminders remain.
    final reminders = FakeReminderPlugin();
    final app = await pumpApp(tester, location: '/today/profile/settings', reminders: reminders);
    expect(find.text('Send your digest by'), findsOneWidget);
    expect(find.text('21:00'), findsOneWidget);
    await tester.tap(find.text('Send your digest by'));
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.keyboard_outlined));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).at(0), '6');
    await tester.enterText(find.byType(TextField).at(1), '00');
    await tester.tap(find.text('PM'));
    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    final settings = app.cloud.tables['settings']!.single;
    expect((settings['send_deadline'], settings['nudge_time']), ('18:00', '16:00'));
    expect(find.text('The draft is prepared at 16:00, two hours earlier'), findsOneWidget);
    expect(find.text('Monday to Friday at 16:30, an hour and a half before'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Remind me on this phone'), 100);
    await tester.tap(find.text('Remind me on this phone'));
    await tester.pumpAndSettle();
    expect(reminders.scheduled, isEmpty);
  });

  testWidgets('TC-APP-150: template sections can be added and deleted (with undo)', (tester) async {
    // Given: an active template with Done today and Blockers.
    // When: "Customer impact" is added, then Blockers is deleted, then the delete is undone.
    // Then: each change saves a new active template: a custom key for the new section, Blockers
    //       gone, then back; earlier fields of the template are kept.
    final cloud = seededCloud()
      ..seed('templates', [
        {
          'version': 'v1',
          'active': true,
          'body': {
            'manager_name': 'Priya',
            'signoff': 'Regards,\nArjun',
            'sections': [
              {'key': 'done_today', 'title': 'Done today', 'guidance': 'Per ticket.'},
              {'key': 'blockers', 'title': 'Blockers', 'guidance': 'Or None.'},
            ],
          },
        },
      ]);
    await pumpApp(tester, cloud: cloud, location: '/today/profile/settings');
    Json active() => cloud.tables['templates']!.lastWhere((t) => t['active'] == true)['body'] as Json;
    List<String> keys() => [for (final s in active()['sections'] as List) (s as Json)['key'] as String];

    await tester.scrollUntilVisible(find.text('Add section'), 200);
    // Bring it above the floating navigation bar before tapping.
    await tester.drag(find.byType(Scrollable).last, const Offset(0, -300));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Add section'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Title'), 'Customer impact');
    await tester.enterText(find.widgetWithText(TextField, 'Guidance for the draft'), 'Who benefits, in one line.');
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(keys(), ['done_today', 'blockers', 'custom_customer_impact']);
    expect(active()['signoff'], 'Regards,\nArjun');

    await tester.tap(find.byTooltip('Section options').at(1));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete').last);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
    await tester.pumpAndSettle();
    expect(keys(), ['done_today', 'custom_customer_impact']);
    await tester.tap(find.text('Undo'));
    await tester.pumpAndSettle();
    expect(keys(), ['done_today', 'blockers', 'custom_customer_impact']);
    expect(customSectionKey('Customer impact', {'custom_customer_impact'}), 'custom_customer_impact_2');
  });
}
