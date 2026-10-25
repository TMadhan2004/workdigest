import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/models/models.dart';

import '../fakes/fakes.dart';

Json _pr(String repo, int id, String opened, {List<String> tickets = const []}) => {
  'repo': repo,
  'pr_id': id,
  'title': '$repo change $id',
  'url': 'https://bitbucket.org/ei-india-admin/$repo/pull-requests/$id',
  'state': 'OPEN',
  'opened_at': opened,
  'approved_by': <String>[],
  'ticket_keys': tickets,
};

Json _rule(String id, String status, Json params) =>
    {'rule_id': id, 'text': id, 'check_kind': 'filter', 'source': 'mine', 'status': status, 'active': true, 'check_params': params};

void main() {
  testWidgets('TC-APP-136: Timeline Open PRs follow my confirmed rules and update live', (tester) async {
    // Given: identity-service #312 plus quiz-service #3 and #4 (150 days old, EIT-495), an active rule hiding
    //       quiz-service #3, an active "older than 30 days" rule, and an unconfirmed rule hiding identity-service.
    // When: the Timeline opens, then the Mac marks identity-service #312 merged.
    // Then: only #312 is listed; #3 and #4 are folded under "Hidden by your rules (2)"; the unconfirmed rule
    //       changes nothing; after the merge #312 leaves the list without a refresh.
    final cloud = seededCloud();
    cloud.tables['pull_requests'] = [
      _pr('quiz-service', 3, '2026-05-26T09:00:00Z', tickets: ['EIT-495']),
      _pr('quiz-service', 4, '2026-05-26T09:00:00Z', tickets: ['EIT-495']),
      {...cloud.tables['pull_requests']!.single, 'ticket_keys': ['EIT-6685']},
    ];
    cloud.seed('standing_rules', [
      _rule('mine-a', 'active', {'exclude_prs': [{'repo': 'quiz-service', 'numbers': [3]}]}),
      _rule('mine-b', 'active', {'exclude_prs_older_than_days': 30}),
      _rule('mine-c', 'needs_review', {'exclude_repos': ['identity-service']}),
    ]);
    await pumpApp(tester, cloud: cloud, location: '/timeline');
    await tester.scrollUntilVisible(find.text('Open PRs'), 300, scrollable: find.byType(Scrollable).first);
    expect(find.textContaining('identity-service #312'), findsOneWidget);
    expect(find.text('Hidden by your rules (2)'), findsOneWidget);
    expect(find.textContaining('quiz-service #3'), findsNothing);
    await tester.ensureVisible(find.text('Hidden by your rules (2)'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Hidden by your rules (2)'));
    await tester.pumpAndSettle();
    expect(find.textContaining('quiz-service #3', skipOffstage: false), findsOneWidget);
    expect(find.textContaining('quiz-service #4', skipOffstage: false), findsOneWidget);

    cloud.macWrites('pull_requests', [
      {...cloud.tables['pull_requests']!.last, 'state': 'MERGED'},
    ]);
    cloud.tables['pull_requests']!.removeWhere((r) => r['pr_id'] == 312 && r['state'] == 'OPEN');
    cloud.emit('pull_requests', const {});
    await tester.pumpAndSettle();
    expect(find.textContaining('identity-service #312'), findsNothing);
  });

  test('TC-APP-137: the app hides exactly the PRs the Mac leaves out of the digest', () {
    // Given: the merged rule from TC-MYR-003 (quiz-service #3 and #4, older than 30 days, ticket EIT-495).
    // When: PRs are checked on 26 Oct.
    // Then: same answers as the Mac: by number, by age (31 days yes, 29 no), by ticket; unrelated PRs stay.
    final rules = PrExclusions.fromRules([
      StandingRule.fromJson(_rule('a', 'active', {'exclude_prs': [{'repo': 'quiz-service', 'numbers': [3, 4]}], 'exclude_prs_older_than_days': 60})),
      StandingRule.fromJson(_rule('b', 'active', {'exclude_tickets': ['eit-495'], 'exclude_prs_older_than_days': 30})),
      StandingRule.fromJson(_rule('c', 'off', {'exclude_repos': ['identity-service']})),
    ]);
    final now = DateTime.utc(2026, 10, 26);
    PullRequest pr(String repo, int id, int ageDays, [List<String> tickets = const []]) =>
        PullRequest.fromJson(_pr(repo, id, now.subtract(Duration(days: ageDays)).toIso8601String(), tickets: tickets));
    expect(rules.olderThanDays, 30);
    expect(rules.excludes(pr('quiz-service', 4, 1), now), isTrue);
    expect(rules.excludes(pr('identity-service', 1, 31), now), isTrue);
    expect(rules.excludes(pr('identity-service', 1, 29), now), isFalse);
    expect(rules.excludes(pr('identity-service', 2, 1, ['EIT-495']), now), isTrue);
    expect(rules.excludes(pr('quiz-service', 5, 1), now), isFalse);
  });
}
