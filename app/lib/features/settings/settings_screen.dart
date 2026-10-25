import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/core/widgets/states.dart';
import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/features/reminders/reminders.dart';
import 'package:work_tracker/features/timeline/timeline_screen.dart';

final AutoDisposeFutureProvider<AppSettings> settingsProvider = FutureProvider.autoDispose<AppSettings>((ref) => ref.watch(settingsRepositoryProvider).load());

final AutoDisposeFutureProvider<Json?> templateProvider = FutureProvider.autoDispose<Json?>((ref) => ref.watch(settingsRepositoryProvider).activeTemplate());

/// Your digest time and reminders, LLM provider, what is sent, hidden sessions and the template.
class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final settings = ref.watch(settingsProvider);
    return Scaffold(
      body: Column(
        children: [
          const AuroraHeader(title: 'Settings'),
          Expanded(
            child: settings.when(
              loading: () => ListView(padding: pagePadding(context), children: const [SkeletonCard(), SizedBox(height: Space.m), SkeletonCard()]),
              error: (error, _) => ErrorState(message: '$error', onRetry: () => ref.invalidate(settingsProvider)),
              data: (value) => _SettingsBody(settings: value),
            ),
          ),
        ],
      ),
    );
  }
}

class _SettingsBody extends ConsumerStatefulWidget {
  const _SettingsBody({required this.settings});

  final AppSettings settings;

  @override
  ConsumerState<_SettingsBody> createState() => _SettingsBodyState();
}

class _SettingsBodyState extends ConsumerState<_SettingsBody> {
  late AppSettings _settings = widget.settings;
  bool _saving = false;

  Future<void> _save(AppSettings next) async {
    setState(() {
      _settings = next;
      _saving = true;
    });
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref.read(settingsRepositoryProvider).save(next);
    } on Object catch (error) {
      messenger.showSnackBar(SnackBar(content: Text('Could not save: $error')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _pickTime(String current, void Function(String) apply) async {
    final parts = current.split(':');
    final picked = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(hour: int.tryParse(parts.first) ?? 20, minute: int.tryParse(parts.last) ?? 0),
    );
    if (picked != null) apply('${picked.hour.toString().padLeft(2, '0')}:${picked.minute.toString().padLeft(2, '0')}');
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListView(
      padding: pagePadding(context),
      children: cascade([
        if (_saving) const LinearProgressIndicator(),
        Text('Appearance', style: theme.textTheme.titleMedium),
        const SizedBox(height: Space.s),
        SegmentedButton<ThemeMode>(
          segments: const [
            ButtonSegment(value: ThemeMode.dark, icon: Icon(Icons.dark_mode_outlined), label: Text('Dark')),
            ButtonSegment(value: ThemeMode.light, icon: Icon(Icons.light_mode_outlined), label: Text('Light')),
            ButtonSegment(value: ThemeMode.system, icon: Icon(Icons.brightness_auto_outlined), label: Text('System')),
          ],
          selected: {ref.watch(themeModeProvider)},
          onSelectionChanged: (value) => unawaited(ref.read(themeModeProvider.notifier).set(value.single)),
        ),
        const SizedBox(height: Space.l),
        Text('Your digest time', style: theme.textTheme.titleMedium),
        Card(
          child: Column(
            children: [
              ListTile(
                title: const Text('Send your digest by'),
                subtitle: Text('The draft is prepared at ${_settings.nudgeTime}, two hours earlier'),
                trailing: Text(_settings.sendDeadline, style: theme.textTheme.titleMedium),
                onTap: () => _pickTime(
                  _settings.sendDeadline,
                  (t) => _save(_settings.copyWith(sendDeadline: t, nudgeTime: draftTimeFor(t))),
                ),
              ),
              SwitchListTile(
                title: const Text('Prepare the draft automatically'),
                subtitle: Text('At ${_settings.nudgeTime} on your Mac'),
                value: _settings.autoDraft,
                onChanged: (value) => _save(_settings.copyWith(autoDraft: value)),
              ),
              SwitchListTile(
                title: const Text('Remind me on this phone'),
                subtitle: Text('Monday to Friday at ${reminderTimeFor(_settings.sendDeadline)}, an hour and a half before'),
                value: ref.watch(remindersEnabledProvider),
                onChanged: (on) => unawaited(ref.read(remindersEnabledProvider.notifier).set(on: on)),
              ),
            ],
          ),
        ),
        const SizedBox(height: Space.l),
        Text('LLM on the Mac', style: theme.textTheme.titleMedium),
        Card(
          child: RadioGroup<String>(
            groupValue: _settings.provider,
            onChanged: (value) => value == null ? null : _save(_settings.copyWith(provider: value)),
            child: Column(
              children: [
                for (final (value, label) in const [('cursor_cli', 'Cursor (your subscription)'), ('claude_cli', 'Claude Code')])
                  Builder(
                    builder: (context) {
                      // Live: the Mac re-checks each provider hourly and after any failure.
                      final capability = ref.watch(profileProvider).valueOrNull?.capability(value);
                      final usable = capability?.usable ?? true;
                      return RadioListTile<String>(
                        value: value,
                        enabled: usable,
                        title: Text(label),
                        subtitle: usable ? null : Text(capability!.reason),
                      );
                    },
                  ),
                const RadioListTile<String>(value: 'none', title: Text('No LLM (facts only)')),
              ],
            ),
          ),
        ),
        const SizedBox(height: Space.l),
        Text('What may be sent to the LLM', style: theme.textTheme.titleMedium),
        Padding(
          padding: const EdgeInsets.symmetric(vertical: Space.s),
          child: Text(
            "Everything is redacted on the Mac first. Check your company's policy on sending code-derived data to cloud and LLM services.",
            style: theme.textTheme.bodySmall,
          ),
        ),
        Card(
          child: Column(
            children: [
              for (final entry in AppSettings.transmitKeys.entries)
                SwitchListTile(
                  title: Text(entry.value),
                  value: _settings.transmitOn(entry.key),
                  onChanged: (value) => _save(_settings.copyWith(transmit: {..._settings.transmit, entry.key: value})),
                ),
            ],
          ),
        ),
        const SizedBox(height: Space.l),
        const _HiddenSessions(),
        const SizedBox(height: Space.l),
        const _TemplateEditor(),
        const SizedBox(height: Space.xxl),
      ], maxStaggered: 10),
    );
  }
}

/// Sessions hidden from digests (personal work), each restorable.
class _HiddenSessions extends ConsumerWidget {
  const _HiddenSessions();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final hidden = ref.watch(hiddenSessionsProvider).valueOrNull ?? const <HiddenSession>[];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('Hidden sessions', style: theme.textTheme.titleMedium),
        const SizedBox(height: Space.s),
        Card(
          child: hidden.isEmpty
              ? const ListTile(
                  leading: Icon(Icons.visibility_outlined),
                  title: Text('Nothing hidden'),
                  subtitle: Text('Hide personal work from the Timeline (swipe a session left).'),
                )
              : Column(
                  children: [
                    for (final session in hidden)
                      ListTile(
                        leading: const Icon(Icons.visibility_off_outlined),
                        title: Text(session.reason ?? session.sessionKey),
                        subtitle: const Text('Left out of digests'),
                        trailing: TextButton(
                          onPressed: () => ref.read(workRepositoryProvider).restoreSession(session.sessionKey),
                          child: const Text('Restore'),
                        ),
                      ),
                  ],
                ),
        ),
      ],
    );
  }
}

/// The sections of your digest, in order: edit, add or delete (the Mac uses them from the next draft).
class _TemplateEditor extends ConsumerWidget {
  const _TemplateEditor();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final template = ref.watch(templateProvider);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Digest template', style: theme.textTheme.titleMedium),
        const SizedBox(height: Space.s),
        template.when(
          loading: () => const SkeletonCard(lines: 2),
          error: (error, _) => Text('Could not load the template: $error'),
          data: (body) {
            if (body == null) {
              return const Card(
                child: ListTile(
                  title: Text('Using the built-in template'),
                  subtitle: Text('The Mac uploads it on its next sync; you can then edit it here.'),
                ),
              );
            }
            final sections = objects(body, 'sections');
            return Card(
              child: Column(
                children: [
                  for (var i = 0; i < sections.length; i++)
                    ListTile(
                      title: Text('${i + 1}. ${str(sections[i], 'title')}'),
                      subtitle: Text(str(sections[i], 'guidance'), maxLines: 2, overflow: TextOverflow.ellipsis),
                      onTap: () => _editSection(context, ref, body, i),
                      trailing: PopupMenuButton<String>(
                        tooltip: 'Section options',
                        onSelected: (choice) => choice == 'edit' ? _editSection(context, ref, body, i) : _deleteSection(context, ref, body, i),
                        itemBuilder: (_) => [
                          const PopupMenuItem(value: 'edit', child: Text('Edit')),
                          PopupMenuItem(value: 'delete', enabled: sections.length > 1, child: const Text('Delete')),
                        ],
                      ),
                    ),
                  const Divider(height: 1),
                  ListTile(
                    leading: const Icon(Icons.add_rounded),
                    title: const Text('Add section'),
                    subtitle: const Text('Added at the end; the AI writes it from your guidance'),
                    onTap: () => _addSection(context, ref, body),
                  ),
                ],
              ),
            );
          },
        ),
      ],
    );
  }

  Future<void> _save(WidgetRef ref, Json body, List<Map<String, dynamic>> sections) async {
    final version = 'app-${DateTime.now().millisecondsSinceEpoch}';
    await ref.read(settingsRepositoryProvider).saveTemplate({...body, 'sections': sections}, version: version);
    ref.invalidate(templateProvider);
  }

  Future<void> _editSection(BuildContext context, WidgetRef ref, Json body, int index) async {
    final sections = [for (final s in objects(body, 'sections')) Map<String, dynamic>.of(s)];
    final result = await showDialog<SectionInput>(
      context: context,
      builder: (_) => _SectionDialog(title: 'Edit section', initial: SectionInput(str(sections[index], 'title'), str(sections[index], 'guidance'))),
    );
    if (result == null) return;
    sections[index] = {...sections[index], 'title': result.title, 'guidance': result.guidance};
    await _save(ref, body, sections);
  }

  Future<void> _addSection(BuildContext context, WidgetRef ref, Json body) async {
    final sections = [for (final s in objects(body, 'sections')) Map<String, dynamic>.of(s)];
    final result = await showDialog<SectionInput>(
      context: context,
      builder: (_) => const _SectionDialog(title: 'Add section', initial: SectionInput('', '')),
    );
    if (result == null) return;
    final key = customSectionKey(result.title, {for (final s in sections) str(s, 'key')});
    sections.add({'key': key, 'title': result.title, 'guidance': result.guidance, 'required': true, 'inline': false});
    await _save(ref, body, sections);
  }

  Future<void> _deleteSection(BuildContext context, WidgetRef ref, Json body, int index) async {
    final sections = [for (final s in objects(body, 'sections')) Map<String, dynamic>.of(s)];
    if (sections.length <= 1) return;
    final title = str(sections[index], 'title');
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text('Delete "$title"?'),
        content: const Text('Drafts from now on leave this section out. Digests already written keep it.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialog, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(dialog, true), child: const Text('Delete')),
        ],
      ),
    );
    if (!(confirmed ?? false)) return;
    sections.removeAt(index);
    await _save(ref, body, sections);
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('"$title" deleted'),
          action: SnackBarAction(
            label: 'Undo',
            onPressed: () => unawaited(_save(ref, body, [for (final s in objects(body, 'sections')) Map<String, dynamic>.of(s)])),
          ),
        ),
      );
    }
  }
}

/// A section's title and guidance, as typed.
@immutable
class SectionInput {
  const SectionInput(this.title, this.guidance);

  final String title;
  final String guidance;
}

/// "Customer impact" → "custom_customer_impact" (made unique against [taken]).
String customSectionKey(String title, Set<String> taken) {
  final slug = title.toLowerCase().replaceAll(RegExp('[^a-z0-9]+'), '_').replaceAll(RegExp(r'^_+|_+$'), '');
  final base = 'custom_${slug.isEmpty ? 'section' : (slug.length > 30 ? slug.substring(0, 30) : slug)}';
  var key = base;
  for (var n = 2; taken.contains(key); n++) {
    key = '${base}_$n';
  }
  return key;
}

class _SectionDialog extends StatefulWidget {
  const _SectionDialog({required this.title, required this.initial});

  final String title;
  final SectionInput initial;

  @override
  State<_SectionDialog> createState() => _SectionDialogState();
}

class _SectionDialogState extends State<_SectionDialog> {
  late final _title = TextEditingController(text: widget.initial.title);
  late final _guidance = TextEditingController(text: widget.initial.guidance);

  @override
  void dispose() {
    _title.dispose();
    _guidance.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.title),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(controller: _title, autofocus: true, decoration: const InputDecoration(labelText: 'Title')),
            const SizedBox(height: Space.m),
            TextField(
              controller: _guidance,
              maxLines: 5,
              decoration: const InputDecoration(labelText: 'Guidance for the draft', hintText: 'What this section should say, e.g. "One line per customer-facing change"'),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        ValueListenableBuilder(
          valueListenable: _title,
          builder: (context, value, _) => FilledButton(
            onPressed: value.text.trim().isEmpty ? null : () => Navigator.pop(context, SectionInput(_title.text.trim(), _guidance.text.trim())),
            child: const Text('Save'),
          ),
        ),
      ],
    );
  }
}
