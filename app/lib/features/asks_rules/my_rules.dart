import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/features/draft_editor/draft_controller.dart';

/// Type a rule in your own words ("Don't include quiz-service PRs 3–7").
class MyRuleComposer extends ConsumerStatefulWidget {
  const MyRuleComposer({super.key});

  @override
  ConsumerState<MyRuleComposer> createState() => _MyRuleComposerState();
}

class _MyRuleComposerState extends ConsumerState<MyRuleComposer> {
  final _text = TextEditingController();
  bool _saving = false;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _add() async {
    if (_text.text.trim().isEmpty) return;
    setState(() => _saving = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(asksRepositoryProvider).addMyRule(_text.text);
      _text.clear();
      unawaited(Haptics.confirm());
    } on Object catch (error) {
      messenger.showSnackBar(SnackBar(content: Text('Could not add the rule: $error')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.fromLTRB(Space.m, Space.m, Space.s, Space.m),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: _text,
              minLines: 1,
              maxLines: 4,
              textInputAction: TextInputAction.done,
              onSubmitted: (_) => _add(),
              decoration: const InputDecoration(
                labelText: 'Add a rule',
                hintText: "e.g. Don't include quiz-service PRs 3–7",
              ),
            ),
          ),
          const SizedBox(width: Space.s),
          IconButton.filled(
            tooltip: 'Add rule',
            onPressed: _saving ? null : _add,
            icon: const Icon(Icons.arrow_upward_rounded),
          ),
        ],
      ),
    );
  }
}

/// One of my rules, in whichever state it is: being read, waiting for confirmation, on or off.
class MyRuleCard extends ConsumerWidget {
  const MyRuleCard({required this.rule, super.key});

  final StandingRule rule;

  Future<void> _run(BuildContext context, Future<void> Function() action, {String? done}) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await action();
      if (done != null) messenger.showSnackBar(SnackBar(content: Text(done)));
    } on Object catch (error) {
      messenger.showSnackBar(SnackBar(content: Text('$error')));
    }
  }

  Future<void> _edit(BuildContext context, WidgetRef ref) async {
    final controller = TextEditingController(text: rule.text);
    final text = await showDialog<String>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('Edit rule'),
        content: TextField(controller: controller, autofocus: true, minLines: 1, maxLines: 4),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialog), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(dialog, controller.text), child: const Text('Save')),
        ],
      ),
    );
    controller.dispose();
    if (text == null || !context.mounted) return;
    await _run(context, () => ref.read(asksRepositoryProvider).editMyRule(rule, text), done: 'The Mac will read the new wording.');
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final repo = ref.read(asksRepositoryProvider);
    final reading = rule.status == 'parsing';
    final review = rule.status == 'needs_review';
    final on = rule.status == 'active';
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: Text(rule.text, style: theme.textTheme.titleSmall)),
              if (!reading && !review)
                Switch(
                  value: on,
                  onChanged: (value) => _run(context, () => repo.setMyRuleOn(rule, on: value)),
                ),
              PopupMenuButton<String>(
                tooltip: 'Rule options',
                onSelected: (choice) => switch (choice) {
                  'edit' => _edit(context, ref),
                  _ => _run(context, () => repo.deleteMyRule(rule), done: 'Rule deleted'),
                },
                itemBuilder: (_) => const [
                  PopupMenuItem(value: 'edit', child: Text('Edit')),
                  PopupMenuItem(value: 'delete', child: Text('Delete')),
                ],
              ),
            ],
          ),
          const SizedBox(height: Space.s),
          if (reading)
            Row(
              children: [
                Icon(Icons.hourglass_top_rounded, size: 18, color: scheme.onSurfaceVariant),
                const SizedBox(width: Space.s),
                Expanded(
                  child: Text(
                    ref.watch(macOnlineProvider) ? 'The Mac is reading this rule…' : 'Waiting for the Mac to read this rule',
                    style: theme.textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                ),
              ],
            )
          else ...[
            Text(review ? 'Understood as:' : (on ? 'Applied to every draft:' : 'Off'), style: theme.textTheme.labelMedium?.copyWith(color: scheme.onSurfaceVariant)),
            const SizedBox(height: Space.xs),
            Wrap(
              spacing: Space.s,
              runSpacing: Space.s,
              children: [for (final chip in rule.understood) Chip(label: Text(chip))],
            ),
            if (rule.instructionOnly) ...[
              const SizedBox(height: Space.s),
              Text(
                'Best effort: the AI follows this when writing; it is not checked in code.',
                style: theme.textTheme.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
              ),
            ],
          ],
          if (review) ...[
            const SizedBox(height: Space.m),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(onPressed: () => _edit(context, ref), child: const Text('Reword')),
                ),
                const SizedBox(width: Space.s),
                Expanded(
                  child: FilledButton(
                    onPressed: () => _run(context, () async {
                      await repo.confirmMyRule(rule);
                      unawaited(Haptics.confirm());
                    }),
                    child: const Text('Confirm'),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

/// After confirming rules: apply them to today's draft now instead of waiting for tonight.
class ApplyRulesButton extends ConsumerWidget {
  const ApplyRulesButton({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return TextButton.icon(
      icon: const Icon(Icons.auto_awesome_rounded),
      label: const Text("Apply to today's draft"),
      onPressed: () async {
        final day = ref.read(todayProvider);
        final job = await ref.read(draftActionsProvider).generate(day);
        if (context.mounted) context.go('/draft?job=${job.id}');
      },
    );
  }
}
