import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/data/models/json.dart';

import '../fakes/fakes.dart';

/// Rows exactly as the Mac pushed them in the recorder's end-to-end test (TC-E2E-001).
FakeCloudSource _cloudFromMac() {
  final rows = fixture('e2e_cloud.json');
  final cloud = FakeCloudSource(clock: () => _now);
  for (final entry in rows.entries) {
    cloud.seed(entry.key, [
      for (final row in (entry.value as List<dynamic>).cast<Json>()) {...row, 'user_id': userId},
    ]);
  }
  cloud.seed('devices', [
    {'device_id': 'mac-e2e', 'kind': 'mac', 'label': 'Mac', 'last_seen_at': _now.toUtc().toIso8601String()},
  ]);
  return cloud;
}

DateTime get _workDate => DateTime.parse(((fixture('e2e_cloud.json')['days'] as List<dynamic>).first as Json)['work_date'] as String);

DateTime get _now => DateTime(_workDate.year, _workDate.month, _workDate.day, 20, 10);

void main() {
  testWidgets('TC-APP-102: Today shows the 8 pm nudge the Mac posted in the end-to-end run', (tester) async {
    // Given: the cloud rows written by the recorder's synthetic day (hooks → daemon → 20:00 job → push).
    // When: the app opens Today at 20:10.
    // Then: the "Draft ready – send before 9pm" notification with its gap count is shown.
    await pumpApp(tester, cloud: _cloudFromMac(), now: _now);
    expect(find.textContaining('Draft ready – send before 9pm'), findsOneWidget);
    final body = ((fixture('e2e_cloud.json')['notifications'] as List<dynamic>).first as Json)['body'] as String;
    expect(find.textContaining(body), findsOneWidget);
  });

  testWidgets('TC-APP-103: the draft the Mac pushed renders with its hook evidence', (tester) async {
    // Given: the same pushed rows.
    // When: the draft editor opens.
    // Then: the 644/644 test line captured by the hooks and the EIT-8403 ticket are on screen.
    await pumpApp(tester, cloud: _cloudFromMac(), now: _now, location: '/draft');
    await tester.scrollUntilVisible(find.textContaining('644/644'), 300, scrollable: find.byType(Scrollable).first);
    expect(find.textContaining('644/644'), findsWidgets);
    expect(find.textContaining('EIT-8403'), findsWidgets);
  });
}
