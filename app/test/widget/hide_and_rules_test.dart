import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/models/models.dart';

import '../fakes/fakes.dart';

Future<void> _openRules(WidgetTester tester) async {
  await tester.tap(find.text('Rules'));
  await tester.pumpAndSettle();
}

Json _rule(String status, {Json params = const {}}) => {
  'rule_id': 'mine-1',
  'text': "Don't include quiz-service PRs 3-4",
  'check_kind': 'filter',
  'source': 'mine',
  'status': status,
  'check_params': params,
  'active': true,
};

const Json _prParams = {
  'exclude_prs': [
    {'repo': 'quiz-service', 'numbers': [3, 4]},
  ],
};

void main() {
  group('Hidden sessions', () {
    testWidgets('TC-APP-115: hiding a session removes it at once, with Undo, and records why', (tester) async {
      // Given: the Timeline for 23 Oct with the "UI tweaks" Cursor session.
      // When: "Hide from digests" is chosen from its menu, then Undo is tapped.
      // Then: it disappears and a hidden_sessions row with a readable reason exists; Undo brings it back.
      final app = await pumpApp(tester, location: '/timeline');
      expect(find.text('UI tweaks'), findsOneWidget);
      await tester.tap(find.byTooltip('Session options').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Hide from digests'));
      await tester.pumpAndSettle();
      expect(find.text('UI tweaks'), findsNothing);
      final hidden = app.cloud.tables['hidden_sessions']!.single;
      expect(hidden['session_key'], 'cursor:C1');
      expect(hidden['reason'], 'UI tweaks · 2026-10-23');
      await tester.tap(find.text('Undo'));
      await tester.pumpAndSettle();
      expect(app.cloud.tables['hidden_sessions'], isEmpty);
      expect(find.text('UI tweaks'), findsOneWidget);
    });

    testWidgets('TC-APP-116: swiping a session left hides it', (tester) async {
      // Given: the Timeline with two sessions.
      // When: the Cursor session is swiped to the left.
      // Then: it is hidden from digests.
      final app = await pumpApp(tester, location: '/timeline');
      await tester.drag(find.text('UI tweaks'), const Offset(-500, 0));
      await tester.pumpAndSettle();
      expect(app.cloud.tables['hidden_sessions']!.single['session_key'], 'cursor:C1');
      expect(find.text('UI tweaks'), findsNothing);
    });

    testWidgets('TC-APP-117: a draft written before a session was hidden offers a redraft', (tester) async {
      // Given: 23 Oct's draft (created 10:00 UTC) and a session hidden after it.
      // When: the Timeline opens and Redraft is tapped.
      // Then: the banner explains the draft still includes it, and a full job for 23 Oct is queued.
      final cloud = seededCloud()
        ..seed('hidden_sessions', [
          {'session_key': 'cursor:C1', 'hidden_at': '2026-10-23T11:00:00Z', 'reason': 'UI tweaks · 2026-10-23'},
        ]);
      final app = await pumpApp(tester, cloud: cloud, location: '/timeline');
      expect(find.text('This draft still includes a session you hid.'), findsOneWidget);
      await tester.tap(find.text('Redraft'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      final job = app.cloud.tables['generation_jobs']!.single;
      expect(job['params'], {'work_date': '2026-10-23'});
      app.cloud.macWrites('generation_jobs', [{'id': job['id'], 'status': 'done'}]);
      await tester.pumpAndSettle();
    });

    testWidgets('TC-APP-118: Settings lists hidden sessions and restores them', (tester) async {
      // Given: one hidden session.
      // When: Settings opens and Restore is tapped.
      // Then: it was listed by its reason, and the hidden_sessions row is removed.
      final cloud = seededCloud()
        ..seed('hidden_sessions', [
          {'session_key': 'cursor:C1', 'hidden_at': '2026-10-23T11:00:00Z', 'reason': 'UI tweaks · 2026-10-23'},
        ]);
      await pumpApp(tester, cloud: cloud, location: '/today/profile/settings');
      await tester.scrollUntilVisible(find.text('UI tweaks · 2026-10-23'), 300, scrollable: find.byType(Scrollable).first);
      await tester.tap(find.text('Restore'));
      await tester.pumpAndSettle();
      expect(cloud.tables['hidden_sessions'], isEmpty);
      expect(find.text('Nothing hidden'), findsOneWidget);
    });
  });

  group('My rules', () {
    testWidgets('TC-APP-119: a typed rule is read by the Mac, confirmed, switched off and deleted', (tester) async {
      // Given: the Rules tab.
      // When: a rule is typed and added; the Mac reads it; Confirm, then the switch, then Delete are used.
      // Then: it shows as being read, then "Understood as" with chips, then applied, then off, then gone.
      final app = await pumpApp(tester, location: '/asks');
      await _openRules(tester);
      await tester.enterText(find.widgetWithText(TextField, 'Add a rule'), "Don't include quiz-service PRs 3-4");
      await tester.tap(find.byTooltip('Add rule'));
      await tester.pumpAndSettle();
      final stored = app.cloud.tables['standing_rules']!.firstWhere((r) => r['source'] == 'mine');
      expect(stored['status'], 'parsing');
      expect(stored['updated_by'], 'app');
      expect(find.textContaining('this rule'), findsOneWidget);

      app.cloud.macWrites('standing_rules', [
        {...stored, 'status': 'needs_review', 'check_params': _prParams, 'updated_by': 'mac'},
      ]);
      await tester.pumpAndSettle();
      expect(find.text('Understood as:'), findsOneWidget);
      expect(find.text('Hide quiz-service PRs #3, #4'), findsOneWidget);
      await tester.tap(find.text('Confirm'));
      await tester.pumpAndSettle();
      expect(stored['status'], 'active');
      expect(find.text('Applied to every draft:'), findsOneWidget);

      await tester.tap(find.byType(Switch));
      await tester.pumpAndSettle();
      expect(stored['status'], 'off');

      await tester.tap(find.byTooltip('Rule options'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();
      expect(app.cloud.tables['standing_rules']!.where((r) => r['source'] == 'mine'), isEmpty);
    });

    testWidgets("TC-APP-120: instruction-only rules are marked best effort; Priya's rules are separate and read-only", (tester) async {
      // Given: an active instruction-only rule of mine and the seeded manager rules.
      // When: the Rules tab opens.
      // Then: the instruction shows as best effort; manager rules are listed under "Priya's rules" without a switch.
      final cloud = seededCloud()
        ..seed('standing_rules', [
          {..._rule('active', params: {'instruction': 'Keep In short under 4 sentences'}), 'text': 'Keep it short'},
        ]);
      await pumpApp(tester, cloud: cloud, location: '/asks');
      await _openRules(tester);
      expect(find.text('Instruction for the AI: Keep In short under 4 sentences'), findsOneWidget);
      expect(find.textContaining('Best effort'), findsOneWidget);
      expect(find.text("Priya's rules"), findsOneWidget);
      expect(find.text('Follow the 11-point structure'), findsOneWidget);
      expect(find.byType(Switch), findsOneWidget);
    });

    testWidgets("TC-APP-121: \"Apply to today's draft\" asks the Mac for a fresh draft", (tester) async {
      // Given: an active rule of mine.
      // When: "Apply to today's draft" is tapped.
      // Then: a full job for today is queued.
      final cloud = seededCloud()..seed('standing_rules', [_rule('active', params: _prParams)]);
      final app = await pumpApp(tester, cloud: cloud, location: '/asks');
      await _openRules(tester);
      await tester.tap(find.text("Apply to today's draft"));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      final job = app.cloud.tables['generation_jobs']!.single;
      expect(job['params'], {'work_date': '2026-10-23'});
      app.cloud.macWrites('generation_jobs', [{'id': job['id'], 'status': 'done'}]);
      await tester.pumpAndSettle();
    });
  });

  test("TC-APP-122: the app's \"Understood as\" wording matches the Mac's", () {
    // Given: the same merged rule the Mac describes in TC-MYR-003.
    // When: the app builds its chips.
    // Then: the wording is identical, line for line.
    const rule = StandingRule(
      ruleId: 'r',
      text: 't',
      checkKind: 'filter',
      source: 'mine',
      active: true,
      params: {
        'exclude_prs': [
          {'repo': 'quiz-service', 'numbers': [3, 4]},
        ],
        'exclude_prs_older_than_days': 30,
        'exclude_tickets': ['EIT-495'],
        'instruction': 'Be brief',
      },
    );
    expect(rule.understood, [
      'Hide quiz-service PRs #3, #4',
      'Hide PRs open more than 30 days',
      'Hide ticket EIT-495',
      'Instruction for the AI: Be brief',
    ]);
    expect(rule.mine, isTrue);
    expect(rule.instructionOnly, isFalse);
  });
}
