import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/repositories/ids.dart';
import 'package:work_tracker/data/sources/cloud_source.dart';
import 'package:work_tracker/data/sources/snapshot_cache.dart';

/// Asks, standing rules, and the manager's replies with their extracted items.
class AsksRepository {
  AsksRepository(this._source, this._cache, {DateTime Function()? clock}) : _clock = clock ?? DateTime.now;

  final CloudSource _source;
  final SnapshotCache _cache;
  final DateTime Function() _clock;

  String get _uid => _source.userId ?? 'anonymous';

  Future<Snapshot<List<Ask>>> asks() {
    return _cache.read<List<Ask>>(
      userId: _uid,
      name: 'asks',
      load: () => _source.select('asks', orderBy: 'opened_on'),
      decode: (json) => ((json as List?)?.cast<Json>() ?? const []).map(Ask.fromJson).toList(growable: false),
    );
  }

  /// Change an ask's status. "done" needs evidence and "disagree" needs a reason (enforced by the DB too).
  Future<Ask> setStatus(Ask ask, String status, {String? evidence, String? reason}) async {
    if (status == 'done' && (evidence == null || evidence.trim().isEmpty)) {
      throw ArgumentError('Closing an ask as done needs evidence: a link, ticket or commit.');
    }
    if (status == 'disagree' && (reason == null || reason.trim().isEmpty)) {
      throw ArgumentError('Disagreeing needs a reason.');
    }
    final closed = status == 'done' || status == 'disagree';
    final rows = await _source.update(
      'asks',
      {
        'status': status,
        if (evidence != null) 'evidence': evidence.trim(),
        if (reason != null) 'reason': reason.trim(),
        'closed_on': closed ? isoDay(_clock()) : null,
        'updated_by': 'app',
      },
      where: [Filter.eq('ask_id', ask.askId)],
    );
    if (rows.isEmpty) throw StateError('ask ${ask.askId} not found');
    return Ask.fromJson(rows.first);
  }

  /// Clear every open ask: they are never reported in a digest. Returns how many were cleared.
  Future<int> clearOpenAsks() async {
    var cleared = 0;
    for (final status in const ['open', 'waiting']) {
      final rows = await _source.update(
        'asks',
        {'status': 'cleared', 'closed_on': isoDay(_clock()), 'updated_by': 'app'},
        where: [Filter.eq('status', status)],
      );
      cleared += rows.length;
    }
    return cleared;
  }

  /// Delete one open ask (cleared: never reported). [restoreAsk] undoes it.
  Future<void> clearAsk(Ask ask) async {
    final rows = await _source.update(
      'asks',
      {'status': 'cleared', 'closed_on': isoDay(_clock()), 'updated_by': 'app'},
      where: [Filter.eq('ask_id', ask.askId)],
    );
    if (rows.isEmpty) throw StateError('ask ${ask.askId} not found');
  }

  /// Undo a delete: the ask is open again, exactly as before.
  Future<void> restoreAsk(Ask ask) async {
    await _source.update(
      'asks',
      {'status': 'open', 'closed_on': null, 'updated_by': 'app'},
      where: [Filter.eq('ask_id', ask.askId)],
    );
  }

  /// Delete (switch off) one of your manager's rules; [setManagerRuleOn] with true undoes it.
  Future<void> setManagerRuleOn(StandingRule rule, {required bool on}) async {
    if (rule.mine) throw ArgumentError('Use the My rules controls for your own rules.');
    await _source.update('standing_rules', {'active': on, 'updated_by': 'app'}, where: [Filter.eq('rule_id', rule.ruleId)]);
  }

  /// Switch off all of your manager's rules (seed and from replies); drafts stop being checked against them.
  Future<int> clearManagerRules() async {
    final active = (await rules()).where((r) => !r.mine && r.active).toList();
    for (final rule in active) {
      await _source.update('standing_rules', {'active': false, 'updated_by': 'app'}, where: [Filter.eq('rule_id', rule.ruleId)]);
    }
    return active.length;
  }

  Future<List<StandingRule>> rules() async {
    final rows = await _source.select('standing_rules', orderBy: 'rule_id');
    return rows.map(StandingRule.fromJson).toList(growable: false);
  }

  /// Add a rule in your own words; the Mac reads it and asks you to confirm what it understood.
  Future<StandingRule> addMyRule(String text) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty) throw ArgumentError('Type the rule first.');
    final rows = await _source.insert('standing_rules', [
      {
        'rule_id': newId('mine', _clock()),
        'text': trimmed,
        'check_kind': 'filter',
        'source': 'mine',
        'status': 'parsing',
        'updated_by': 'app',
      },
    ]);
    return StandingRule.fromJson(rows.first);
  }

  /// Change a rule's wording: the Mac reads it again.
  Future<void> editMyRule(StandingRule rule, String text) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty) throw ArgumentError('Type the rule first.');
    await _setRule(rule, {'text': trimmed, 'status': 'parsing', 'check_params': <String, Object?>{}});
  }

  /// Accept what the Mac understood: the rule applies from the next draft.
  Future<void> confirmMyRule(StandingRule rule) => _setRule(rule, {'status': 'active'});

  Future<void> setMyRuleOn(StandingRule rule, {required bool on}) => _setRule(rule, {'status': on ? 'active' : 'off'});

  Future<void> deleteMyRule(StandingRule rule) async {
    if (!rule.mine) throw ArgumentError('Only your own rules can be deleted.');
    await _source.delete('standing_rules', where: [Filter.eq('rule_id', rule.ruleId)]);
  }

  Future<void> _setRule(StandingRule rule, Json values) async {
    if (!rule.mine) throw ArgumentError("The manager's rules can't be changed here.");
    final rows = await _source.update('standing_rules', {...values, 'updated_by': 'app'}, where: [Filter.eq('rule_id', rule.ruleId)]);
    if (rows.isEmpty) throw StateError('rule ${rule.ruleId} not found');
  }

  /// Hand a pasted or shared reply to the Mac, which splits it into items.
  Future<ManagerReply> submitReply(String text, {String? forWorkDate}) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty) throw ArgumentError('The reply is empty.');
    final rows = await _source.insert('manager_replies', [
      {'reply_id': newId('reply', _clock()), 'raw_text': trimmed, 'source': 'app', 'for_work_date': ?forWorkDate},
    ]);
    return ManagerReply.fromJson(rows.first);
  }

  Future<List<ManagerReply>> replies({int limit = 20}) async {
    final rows = await _source.select('manager_replies', orderBy: 'received_at', descending: true, limit: limit);
    return rows.map(ManagerReply.fromJson).toList(growable: false);
  }

  Future<List<ReplyItem>> items(String replyId) async {
    final rows = await _source.select('reply_items', where: [Filter.eq('reply_id', replyId)], orderBy: 'idx');
    return rows.map(ReplyItem.fromJson).toList(growable: false);
  }

  /// Save the user's review: each item confirmed or dismissed, with any type change.
  Future<void> reviewItems(List<ReplyItem> items) async {
    for (final item in items) {
      await _source.update(
        'reply_items',
        {'status': item.status, 'type': item.type, 'updated_by': 'app'},
        where: [Filter.eq('item_id', item.itemId)],
      );
    }
  }
}
