import 'dart:async';

import 'package:flutter/material.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/app_theme.dart';

class CloseDayResult {
  const CloseDayResult({required this.rating, required this.team, this.project});

  final int rating;
  final List<String> team;
  final String? project;
}

/// Ask for the day rating (never guessed), who you worked with, and the project.
class CloseDaySheet extends StatefulWidget {
  const CloseDaySheet({this.initialTeam = const [], this.initialProject, super.key});

  final List<String> initialTeam;
  final String? initialProject;

  static const ratings = {1: 'Very tough', 3: 'Tough', 7: 'Good', 9: 'Super fantastic'};

  @override
  State<CloseDaySheet> createState() => _CloseDaySheetState();
}

class _CloseDaySheetState extends State<CloseDaySheet> {
  int? _rating;
  late final TextEditingController _team = TextEditingController(text: widget.initialTeam.join(', '));
  late final TextEditingController _project = TextEditingController(text: widget.initialProject ?? '');

  @override
  void dispose() {
    _team.dispose();
    _project.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: EdgeInsets.fromLTRB(Space.l, 0, Space.l, Space.l + MediaQuery.viewInsetsOf(context).bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('Close the day', style: theme.textTheme.titleLarge),
          const SizedBox(height: Space.s),
          Text('How was today? This goes in the digest as the day rating.', style: theme.textTheme.bodyMedium),
          const SizedBox(height: Space.m),
          Wrap(
            spacing: Space.s,
            runSpacing: Space.s,
            children: [
              for (final entry in CloseDaySheet.ratings.entries)
                ChoiceChip(
                  label: Text('${entry.key} · ${entry.value}'),
                  selected: _rating == entry.key,
                  onSelected: (_) {
                    unawaited(Haptics.selection());
                    setState(() => _rating = entry.key);
                  },
                ),
            ],
          ),
          const SizedBox(height: Space.l),
          TextField(
            controller: _team,
            decoration: const InputDecoration(labelText: 'Team members today', helperText: 'Comma-separated, e.g. Rahul, Vikram'),
          ),
          const SizedBox(height: Space.m),
          TextField(controller: _project, decoration: const InputDecoration(labelText: 'Project')),
          const SizedBox(height: Space.l),
          FilledButton(
            onPressed: _rating == null
                ? null
                : () => Navigator.of(context).pop(
                    CloseDayResult(
                      rating: _rating!,
                      team: _team.text.split(',').map((s) => s.trim()).where((s) => s.isNotEmpty).toList(),
                      project: _project.text.trim().isEmpty ? null : _project.text.trim(),
                    ),
                  ),
            child: const Text('Close day and draft the digest'),
          ),
        ],
      ),
    );
  }
}
