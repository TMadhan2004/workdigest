import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/features/today/today_screen.dart';

import '../fakes/fakes.dart';

Map<String, dynamic> _note(String id, String kind, String title, {Map<String, dynamic> payload = const {}, int minute = 0}) => {
  'id': id,
  'kind': kind,
  'title': title,
  'body': '$title body',
  'payload': payload,
  'created_at': DateTime.utc(2026, 10, 23, 12, minute).toIso8601String(),
  'read_at': null,
};

AppNotification _of(String kind) => AppNotification(id: 'x', kind: kind, title: '', body: '');

void main() {
  test('TC-APP-152: each kind of notification opens the right place', () {
    // Given: every kind the Mac can post.
    // When: their tap action is looked up.
    // Then: drafts open the draft, AI changes open Settings, a Mac update shows the command,
    //       anything else stays on Today.
    expect(notificationAction(_of('draft_ready')), NotificationAction.draft);
    expect(notificationAction(_of('ai_unavailable')), NotificationAction.settings);
    expect(notificationAction(_of('ai_restored')), NotificationAction.settings);
    expect(notificationAction(_of('update_available')), NotificationAction.macUpdate);
    expect(notificationAction(_of('mac_offline')), NotificationAction.today);
  });

  testWidgets('TC-APP-153: tapping notifications goes there, and all can be marked read', (tester) async {
    // Given: unread "AI stopped", "Mac update" and "Draft ready" notifications.
    // When: the bell's list opens and "AI stopped" is tapped; then the Mac update; then
    //       "Mark all as read".
    // Then: Settings opens; the Mac update shows `digest update` to copy; afterwards no
    //       notification is unread and the bell's dot is gone.
    final cloud = seededCloud()
      ..seed('notifications', [
        _note('n1', 'draft_ready', 'Draft ready', payload: {'work_date': '2026-10-23'}),
        _note('n2', 'update_available', 'WorkDigest 0.2.0 for your Mac', minute: 1),
        _note('n3', 'ai_unavailable', 'Cursor stopped working', minute: 2),
      ]);
    await pumpApp(tester, cloud: cloud);
    await tester.tap(find.byTooltip('Notifications'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cursor stopped working').last);
    await tester.pumpAndSettle();
    expect(find.text('Send your digest by'), findsOneWidget); // Settings
    expect(cloud.tables['notifications']!.firstWhere((n) => n['id'] == 'n3')['read_at'], isNotNull);

    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Notifications'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('WorkDigest 0.2.0 for your Mac').last);
    await tester.pumpAndSettle();
    expect(find.text('digest update'), findsOneWidget);
    await tester.tap(find.text('Done'));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Notifications'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Mark all as read'));
    await tester.pumpAndSettle();
    expect(cloud.tables['notifications']!.where((n) => n['read_at'] == null), isEmpty);
    expect(find.text('Draft ready'), findsNothing);
  });
}
