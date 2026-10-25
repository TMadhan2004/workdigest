import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../fakes/fakes.dart';

void main() {
  testWidgets("TC-APP-138: the manager's name comes from settings, with a neutral fallback", (tester) async {
    // Given: settings naming the manager "Meera", then settings with no manager name.
    // When: Today and the Rules tab are shown.
    // Then: labels read "Add Meera's reply" and "Meera's rules"; without a name they read
    //       "Add your manager's reply" and "Your manager's rules".
    final named = seededCloud();
    named.tables['settings']!.single['manager_name'] = 'Meera';
    await pumpApp(tester, cloud: named);
    expect(find.text("Add Meera's reply"), findsOneWidget);
    await tester.tap(find.byTooltip('Asks'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Rules'));
    await tester.pumpAndSettle();
    expect(find.text("Meera's rules"), findsOneWidget);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 1));
    final unnamed = seededCloud();
    unnamed.tables['settings']!.single['manager_name'] = '';
    await pumpApp(tester, cloud: unnamed);
    expect(find.text("Add your manager's reply"), findsOneWidget);
  });

  testWidgets('TC-APP-139: AI providers the Mac cannot use are locked, with the reason', (tester) async {
    // Given: the Mac reports Cursor not installed and Claude Code working.
    // When: Settings opens.
    // Then: the Cursor option is disabled with "Not installed on your Mac"; Claude Code and facts-only stay selectable.
    final cloud = seededCloud();
    cloud.tables['settings']!.single['capabilities'] = {
      'cursor_cli': {'state': 'missing', 'detail': '', 'checked_at': '2026-10-23T12:00:00Z'},
      'claude_cli': {'state': 'ok', 'detail': '', 'checked_at': '2026-10-23T12:00:00Z'},
    };
    await pumpApp(tester, cloud: cloud, location: '/today/profile/settings');
    await tester.scrollUntilVisible(find.text('Claude Code'), 300, scrollable: find.byType(Scrollable).first);
    final cursor = tester.widget<RadioListTile<String>>(find.widgetWithText(RadioListTile<String>, 'Cursor (your subscription)'));
    final claude = tester.widget<RadioListTile<String>>(find.widgetWithText(RadioListTile<String>, 'Claude Code'));
    expect(cursor.enabled, isFalse);
    expect(find.text('Not installed on your Mac'), findsOneWidget);
    expect(claude.enabled, isTrue);
  });
}
