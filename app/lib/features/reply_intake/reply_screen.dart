import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/data/sources/cloud_source.dart';
import 'package:work_tracker/features/share/share_intake.dart';

/// Paste or share the manager's reply; review the asks and rules extracted from it.
class ReplyScreen extends ConsumerStatefulWidget {
  const ReplyScreen({this.initialText, this.pollInterval = const Duration(seconds: 20), super.key});

  final String? initialText;
  /// Safety re-check; the reply items normally arrive live.
  final Duration pollInterval;

  @override
  ConsumerState<ReplyScreen> createState() => _ReplyScreenState();
}

enum _Stage { compose, waiting, review, saving }

class _ReplyScreenState extends ConsumerState<ReplyScreen> {
  late final TextEditingController _text = TextEditingController(
    text: widget.initialText ?? ref.read(sharedTextProvider) ?? '',
  );
  _Stage _stage = _Stage.compose;
  String? _replyId;
  List<ReplyItem> _items = const [];
  String? _error;
  Timer? _poll;
  StreamSubscription<TableChange>? _live;

  @override
  void dispose() {
    _poll?.cancel();
    unawaited(_live?.cancel());
    _text.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() {
      _stage = _Stage.waiting;
      _error = null;
    });
    try {
      final reply = await ref.read(asksRepositoryProvider).submitReply(_text.text);
      ref.read(sharedTextProvider.notifier).state = null;
      _replyId = reply.replyId;
      _live = ref.read(cloudSourceProvider).changes('reply_items').listen((change) {
        if (change.resync || change.record['reply_id'] == _replyId) unawaited(_checkItems());
      });
      _poll = Timer.periodic(widget.pollInterval, (_) => _checkItems());
      await _checkItems();
    } on Object catch (error) {
      setState(() {
        _stage = _Stage.compose;
        _error = '$error';
      });
    }
  }

  Future<void> _checkItems() async {
    final id = _replyId;
    if (id == null || _stage != _Stage.waiting) return;
    final items = await ref.read(asksRepositoryProvider).items(id);
    if (items.isEmpty || !mounted) return;
    _poll?.cancel();
    unawaited(_live?.cancel());
    setState(() {
      _items = [for (final item in items) item.status == 'proposed' ? item.copyWith(status: _defaultStatus(item)) : item];
      _stage = _Stage.review;
    });
  }

  /// Feedback and info are dismissed by default; the rest are kept.
  static String _defaultStatus(ReplyItem item) =>
      const {'feedback', 'info_only'}.contains(item.type) ? 'dismissed' : 'confirmed';

  Future<void> _confirm() async {
    setState(() => _stage = _Stage.saving);
    try {
      await ref.read(asksRepositoryProvider).reviewItems(_items);
      unawaited(Haptics.confirm());
      if (!mounted) return;
      final kept = _items.where((i) => i.status == 'confirmed').length;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$kept items confirmed. The Mac adds them to your asks and rules.')));
      // Opened from a share there may be nothing to go back to: land on Asks instead.
      if (Navigator.of(context).canPop()) {
        Navigator.of(context).pop();
      } else {
        context.go('/asks');
      }
    } on Object catch (error) {
      setState(() {
        _stage = _Stage.review;
        _error = '$error';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: Column(
        children: [
          AuroraHeader(title: '${capitalizeFirst(ref.watch(managerPossessiveProvider))} reply'),
          Expanded(
            child: AnimatedSwitcher(
              duration: Motion.of(context, Motion.standard),
              child: switch (_stage) {
                _Stage.compose => ListView(
                  key: const ValueKey('compose'),
                  padding: const EdgeInsets.all(Space.m),
                  children: [
                    Text(
                      'Paste his reply, or long-press it in WhatsApp → Share → WorkDigest.',
                      style: theme.textTheme.bodyMedium,
                    ),
                    const SizedBox(height: Space.m),
                    TextField(
                      controller: _text,
                      minLines: 8,
                      maxLines: 20,
                      decoration: const InputDecoration(labelText: 'Reply text', alignLabelWithHint: true),
                    ),
                    if (_error != null) ...[
                      const SizedBox(height: Space.s),
                      Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
                    ],
                    const SizedBox(height: Space.m),
                    ListenableBuilder(
                      listenable: _text,
                      builder: (context, _) => FilledButton.icon(
                        onPressed: _text.text.trim().isEmpty ? null : _submit,
                        icon: const Icon(Icons.auto_awesome),
                        label: const Text('Find asks and rules'),
                      ),
                    ),
                  ],
                ),
                _Stage.waiting => Center(
                  key: const ValueKey('waiting'),
                  child: Semantics(
                    liveRegion: true,
                    child: const Padding(
                      padding: EdgeInsets.all(Space.xl),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          CircularProgressIndicator(),
                          SizedBox(height: Space.m),
                          Text('The Mac is reading the reply…', textAlign: TextAlign.center),
                          SizedBox(height: Space.s),
                          Text('This needs the Mac online; the items appear here as soon as it has read the reply.', textAlign: TextAlign.center),
                        ],
                      ),
                    ),
                  ),
                ),
                _Stage.review || _Stage.saving => _ReviewList(
                  key: const ValueKey('review'),
                  items: _items,
                  saving: _stage == _Stage.saving,
                  error: _error,
                  onChanged: (item) => setState(() => _items = [for (final i in _items) i.itemId == item.itemId ? item : i]),
                  onConfirm: _confirm,
                ),
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _ReviewList extends StatelessWidget {
  const _ReviewList({required this.items, required this.saving, required this.onChanged, required this.onConfirm, this.error, super.key});

  final List<ReplyItem> items;
  final bool saving;
  final String? error;
  final void Function(ReplyItem) onChanged;
  final VoidCallback onConfirm;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final kept = items.where((i) => i.status == 'confirmed').length;
    return Column(
      children: [
        Expanded(
          child: ListView.separated(
            padding: const EdgeInsets.all(Space.m),
            itemCount: items.length + 1,
            separatorBuilder: (_, _) => const SizedBox(height: Space.s),
            itemBuilder: (context, index) {
              if (index == 0) {
                return Text(
                  'Check each item. Actions and questions become open asks; standing rules are checked on every draft.',
                  style: theme.textTheme.bodyMedium,
                );
              }
              final item = items[index - 1];
              final keep = item.status == 'confirmed';
              return Card(
                child: Padding(
                  padding: const EdgeInsets.all(Space.m),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          DropdownButton<String>(
                            value: item.type,
                            onChanged: saving ? null : (value) => onChanged(item.copyWith(type: value)),
                            items: [
                              for (final type in ReplyItem.types) DropdownMenuItem(value: type, child: Text(ReplyItem.typeLabel(type))),
                            ],
                          ),
                          const Spacer(),
                          Text(keep ? 'Keep' : 'Skip', style: theme.textTheme.labelMedium),
                          Switch(
                            value: keep,
                            onChanged: saving ? null : (value) => onChanged(item.copyWith(status: value ? 'confirmed' : 'dismissed')),
                          ),
                        ],
                      ),
                      if (item.heading != null) Text(item.heading!, style: theme.textTheme.labelLarge),
                      Text(item.text, style: theme.textTheme.bodyMedium?.copyWith(color: keep ? null : theme.disabledColor)),
                      if (item.relatedTickets.isNotEmpty || item.dueDate != null)
                        Padding(
                          padding: const EdgeInsets.only(top: Space.xs),
                          child: Text(
                            [...item.relatedTickets, if (item.dueDate != null) 'due ${item.dueDate}'].join(' · '),
                            style: theme.textTheme.bodySmall,
                          ),
                        ),
                    ],
                  ),
                ),
              );
            },
          ),
        ),
        if (error != null) Padding(padding: const EdgeInsets.all(Space.s), child: Text(error!, style: TextStyle(color: theme.colorScheme.error))),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(Space.m),
            child: SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: saving ? null : onConfirm,
                child: saving ? const LinearProgressIndicator() : Text('Confirm $kept of ${items.length}'),
              ),
            ),
          ),
        ),
      ],
    );
  }
}
