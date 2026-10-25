import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/states.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/features/draft_editor/draft_controller.dart';

/// One statement, highlighted by state; tap to see its evidence.
class StatementTile extends StatelessWidget {
  const StatementTile({required this.statement, required this.onTap, this.depth = 0, super.key});

  final Statement statement;
  final VoidCallback onTap;
  final int depth;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.digestColors;
    final (Color? bg, Color? fg, IconData? icon, String state) = statement.isPlaceholder
        ? (colors.missing, colors.onMissing, Icons.help_outline, 'missing')
        : statement.isUnverified
        ? (colors.unverified, colors.onUnverified, Icons.report_gmailerrorred_outlined, 'unverified')
        : (null, null, null, statement.evidenceIds.isEmpty ? 'no evidence needed' : 'verified');
    return Padding(
      padding: EdgeInsets.only(left: depth * Space.m, top: Space.xs, bottom: Space.xs),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Semantics(
            button: true,
            label: '${statement.text}. $state. Double tap to see evidence.',
            excludeSemantics: true,
            child: InkWell(
              onTap: onTap,
              borderRadius: BorderRadius.circular(Radii.highlight),
              child: Container(
                width: double.infinity,
                constraints: const BoxConstraints(minHeight: 40),
                padding: bg == null
                    ? const EdgeInsets.symmetric(horizontal: Space.s, vertical: Space.s)
                    : const EdgeInsets.fromLTRB(Space.m - 4, Space.s + 2, Space.m - 4, Space.s + 2),
                decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(Radii.highlight)),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (depth > 0) Text('–  ', style: theme.textTheme.bodyMedium?.copyWith(color: fg)),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(statement.text, style: theme.textTheme.bodyMedium?.copyWith(color: fg)),
                          if (statement.isUnverified)
                            Padding(
                              padding: const EdgeInsets.only(top: Space.xs),
                              child: Text(
                                'Confirm: ${statement.problems.join('; ')}',
                                style: theme.textTheme.labelSmall?.copyWith(color: fg),
                              ),
                            ),
                        ],
                      ),
                    ),
                    if (icon != null) ...[const SizedBox(width: Space.s), Icon(icon, size: 18, color: fg)],
                  ],
                ),
              ),
            ),
          ),
          for (final child in statement.children)
            StatementTile(statement: child, onTap: onTap, depth: depth + 1),
        ],
      ),
    );
  }
}

/// A numbered section with expand/collapse, edit and regenerate.
class SectionCard extends StatefulWidget {
  const SectionCard({
    required this.section,
    required this.onStatementTap,
    required this.onEdit,
    required this.onRegenerate,
    this.busy = false,
    super.key,
  });

  final DraftSection section;
  final void Function(Statement) onStatementTap;
  final VoidCallback onEdit;
  final VoidCallback? onRegenerate;
  final bool busy;

  @override
  State<SectionCard> createState() => _SectionCardState();
}

class _SectionCardState extends State<SectionCard> {
  bool _expanded = true;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.digestColors;
    final section = widget.section;
    final unverified = section.unverifiedCount;
    final missing = section.statements.isEmpty;
    return Card(
      child: Column(
        children: [
          InkWell(
            borderRadius: Radii.cardRadius,
            onTap: () => setState(() => _expanded = !_expanded),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(Space.m, Space.s, Space.xs, Space.s),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('${section.number}. ${section.title}', style: theme.textTheme.titleMedium),
                        if (section.titleSuffix.isNotEmpty)
                          Text(section.titleSuffix, style: theme.textTheme.bodySmall),
                      ],
                    ),
                  ),
                  if (missing)
                    StatusChip(label: 'Missing', background: colors.missing, foreground: colors.onMissing)
                  else if (unverified > 0)
                    StatusChip(label: '$unverified to confirm', background: colors.unverified, foreground: colors.onUnverified),
                  if (widget.busy)
                    const Padding(
                      padding: EdgeInsets.all(Space.s),
                      child: SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2)),
                    )
                  else
                    PopupMenuButton<String>(
                      tooltip: 'Section actions for ${section.title}',
                      onSelected: (value) => value == 'edit' ? widget.onEdit() : widget.onRegenerate?.call(),
                      itemBuilder: (_) => [
                        const PopupMenuItem(value: 'edit', child: ListTile(leading: Icon(Icons.edit_outlined), title: Text('Edit'))),
                        if (widget.onRegenerate != null)
                          const PopupMenuItem(value: 'regen', child: ListTile(leading: Icon(Icons.auto_awesome_outlined), title: Text('Regenerate'))),
                      ],
                    ),
                  AnimatedRotation(
                    turns: _expanded ? 0 : -0.25,
                    duration: Motion.of(context, Motion.quick),
                    child: const Icon(Icons.expand_more, semanticLabel: 'Expand or collapse'),
                  ),
                ],
              ),
            ),
          ),
          MotionSize(
            child: _expanded
                ? Padding(
                    padding: const EdgeInsets.fromLTRB(Space.s, 0, Space.s, Space.m),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        if (missing)
                          Padding(
                            padding: const EdgeInsets.all(Space.s),
                            child: Text('No evidence captured. Write it or confirm "None."', style: theme.textTheme.bodyMedium),
                          ),
                        for (final statement in section.statements)
                          StatementTile(statement: statement, onTap: () => widget.onStatementTap(statement)),
                        for (final line in section.trailer)
                          Padding(padding: const EdgeInsets.all(Space.s), child: Text(line, style: theme.textTheme.bodyMedium)),
                      ],
                    ),
                  )
                : const SizedBox(width: double.infinity),
          ),
        ],
      ),
    );
  }
}

/// Collapsible list of gaps to confirm, most important first.
class GapPanel extends StatefulWidget {
  const GapPanel({required this.gaps, super.key});

  final List<Gap> gaps;

  @override
  State<GapPanel> createState() => _GapPanelState();
}

class _GapPanelState extends State<GapPanel> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.digestColors;
    final blocking = widget.gaps.where((g) => g.kind != 'input').length;
    return Card(
      color: blocking == 0 ? colors.verified : colors.missing,
      child: Column(
        children: [
          ListTile(
            leading: Icon(blocking == 0 ? Icons.check_circle_outline : Icons.flag_outlined, color: blocking == 0 ? colors.onVerified : colors.onMissing),
            title: Text(
              blocking == 0 ? 'Nothing blocking' : '$blocking gaps to confirm',
              style: theme.textTheme.titleMedium?.copyWith(color: blocking == 0 ? colors.onVerified : colors.onMissing),
            ),
            subtitle: Text('${widget.gaps.length} items in total', style: TextStyle(color: blocking == 0 ? colors.onVerified : colors.onMissing)),
            trailing: Icon(_open ? Icons.expand_less : Icons.expand_more, semanticLabel: _open ? 'Hide gaps' : 'Show gaps'),
            onTap: () => setState(() => _open = !_open),
          ),
          MotionSize(
            child: _open
                ? ColoredBox(
                    color: theme.colorScheme.surfaceContainerLow,
                    child: Column(
                      children: [
                        for (final gap in widget.gaps)
                          ListTile(
                            dense: true,
                            leading: _GapIcon(kind: gap.kind),
                            title: Text(gap.message),
                            subtitle: Text(gap.section == null ? gap.label : '${gap.label} · ${gap.section}'),
                          ),
                      ],
                    ),
                  )
                : const SizedBox(width: double.infinity),
          ),
        ],
      ),
    );
  }
}

class _GapIcon extends StatelessWidget {
  const _GapIcon({required this.kind});

  final String kind;

  @override
  Widget build(BuildContext context) {
    final colors = context.digestColors;
    final (IconData icon, Color color) = switch (kind) {
      'real_action' => (Icons.task_alt, colors.onRealAction),
      'rule' => (Icons.rule, Theme.of(context).colorScheme.error),
      'unverified' => (Icons.report_gmailerrorred_outlined, colors.onUnverified),
      'missing' => (Icons.help_outline, colors.onMissing),
      _ => (Icons.info_outline, Theme.of(context).colorScheme.onSurfaceVariant),
    };
    return Icon(icon, color: color, semanticLabel: kind);
  }
}

/// Bottom sheet listing the evidence a statement cites.
class EvidenceSheet extends ConsumerWidget {
  const EvidenceSheet({required this.digest, required this.statement, super.key});

  final Digest digest;
  final Statement statement;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final colors = context.digestColors;
    final evidence = ref.watch(evidenceProvider(digest));
    final ids = {...statement.evidenceIds, for (final c in statement.children) ...c.evidenceIds}.toList();
    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.55,
      maxChildSize: 0.9,
      builder: (context, controller) => ListView(
        controller: controller,
        padding: const EdgeInsets.all(Space.m),
        children: [
          Text('Evidence', style: theme.textTheme.titleLarge),
          const SizedBox(height: Space.s),
          Text(statement.text, style: theme.textTheme.bodyMedium),
          if (statement.isUnverified) ...[
            const SizedBox(height: Space.s),
            StatusChip(
              label: 'Confirm: ${statement.problems.join('; ')}',
              background: colors.unverified,
              foreground: colors.onUnverified,
              icon: Icons.report_gmailerrorred_outlined,
            ),
          ],
          const Divider(height: Space.xl),
          if (ids.isEmpty)
            const Text('No evidence cited. Statements like this need your confirmation before sending.')
          else
            evidence.when(
              loading: () => const SkeletonCard(lines: 2),
              error: (error, _) => Text('Could not load evidence: $error'),
              data: (byId) => Column(
                children: [
                  for (final id in ids)
                    _EvidenceTile(item: byId[id], id: id),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _EvidenceTile extends StatelessWidget {
  const _EvidenceTile({required this.item, required this.id});

  final EvidenceItem? item;
  final String id;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.digestColors;
    if (item == null) {
      return ListTile(title: Text('$id – not found'), subtitle: const Text('This statement cites evidence that does not exist.'));
    }
    final fact = item!.strength == 'fact';
    return Card(
      margin: const EdgeInsets.only(bottom: Space.s),
      child: Padding(
        padding: const EdgeInsets.all(Space.m),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('$id · ${item!.kind.replaceAll('_', ' ')}', style: theme.textTheme.labelLarge),
                const Spacer(),
                StatusChip(
                  label: fact ? 'Fact' : item!.strength == 'claim' ? "Agent's claim" : 'Context',
                  background: fact ? colors.verified : theme.colorScheme.surfaceContainerHighest,
                  foreground: fact ? colors.onVerified : theme.colorScheme.onSurfaceVariant,
                ),
              ],
            ),
            const SizedBox(height: Space.s),
            SelectableText(item!.text, style: theme.textTheme.bodyMedium),
            if (item!.url != null) ...[
              const SizedBox(height: Space.xs),
              SelectableText(item!.url!, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.primary)),
            ],
          ],
        ),
      ),
    );
  }
}

/// Full-text editor for one section block.
class SectionEditorSheet extends StatefulWidget {
  const SectionEditorSheet({required this.title, required this.initial, super.key});

  final String title;
  final String initial;

  @override
  State<SectionEditorSheet> createState() => _SectionEditorSheetState();
}

class _SectionEditorSheetState extends State<SectionEditorSheet> {
  late final TextEditingController _text = TextEditingController(text: widget.initial);

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(Space.m, 0, Space.m, Space.m + MediaQuery.viewInsetsOf(context).bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('Edit ${widget.title}', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: Space.s),
          const Text('Plain WhatsApp text: *bold*, numbered lines and dashes. No # headings or tables.'),
          const SizedBox(height: Space.m),
          TextField(
            controller: _text,
            maxLines: 14,
            minLines: 6,
            style: const TextStyle(fontFamily: 'monospace'),
            decoration: const InputDecoration(alignLabelWithHint: true, labelText: 'Section text'),
          ),
          const SizedBox(height: Space.m),
          FilledButton(onPressed: () => Navigator.of(context).pop(_text.text), child: const Text('Save as my version')),
        ],
      ),
    );
  }
}
