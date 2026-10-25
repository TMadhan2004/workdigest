import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../fakes/fakes.dart';

void main() {
  testWidgets('TC-APP-106: the Mac status updates live: drafting, then offline, with no refresh', (tester) async {
    // Given: Today open with the Mac online and idle.
    // When: the Mac reports "drafting", then sends its shutdown status "offline".
    // Then: the status line changes to "writing your draft…" and then to "Mac offline since …", without any refresh.
    final app = await pumpApp(tester);
    expect(find.text('Mac connected'), findsOneWidget);
    final seen = fixedNow.toUtc().toIso8601String();
    app.cloud.macWrites('devices', [{'device_id': 'mac-1', 'kind': 'mac', 'status': 'drafting', 'last_seen_at': seen}]);
    await tester.pumpAndSettle();
    expect(find.text('Mac connected · writing your draft…'), findsOneWidget);
    app.cloud.macWrites('devices', [{'device_id': 'mac-1', 'status': 'offline', 'last_seen_at': seen}]);
    await tester.pumpAndSettle();
    expect(find.textContaining('Mac offline since'), findsOneWidget);
  });

  testWidgets('TC-APP-107: a draft the Mac pushes appears on screen by itself', (tester) async {
    // Given: the Draft screen for 26 Oct, which has no draft yet.
    // When: the Mac writes the digest row for that day.
    // Then: "No draft" is replaced by the draft without pulling to refresh.
    final app = await pumpApp(tester, location: '/draft?day=2026-10-26', now: DateTime(2026, 10, 26, 20));
    expect(find.textContaining('No draft for 2026-10-26'), findsOneWidget);
    final digest = {...fixture('digest_2026-10-23.json'), 'digest_id': 'd-26', 'work_date': '2026-10-26', 'user_id': userId};
    app.cloud.macWrites('digests', [digest]);
    await tester.pumpAndSettle();
    expect(find.textContaining('No draft for 2026-10-26'), findsNothing);
    expect(find.text('Share to WhatsApp'), findsOneWidget);
  });

  testWidgets('TC-APP-108: returning to the app reconnects and re-reads what is on screen', (tester) async {
    // Given: the app open on Today, then sent to the background.
    // When: it is resumed.
    // Then: the cloud source is asked to resync once.
    final app = await pumpApp(tester);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();
    expect(app.cloud.resyncs, ['resync']);
  });
}
