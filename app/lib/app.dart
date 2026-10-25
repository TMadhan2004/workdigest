import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:work_tracker/core/router/router.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/features/share/share_intake.dart';
import 'package:work_tracker/features/update/update_service.dart';
import 'package:work_tracker/features/update/update_ui.dart';

class WorkDigestApp extends ConsumerWidget {
  const WorkDigestApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(routerProvider);
    return MaterialApp.router(
      title: 'WorkDigest',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      themeMode: ref.watch(themeModeProvider),
      routerConfig: router,
      builder: (context, child) => LiveResync(
        child: UpdateGate(
          child: ShareIntakeListener(
            onShared: (_) => router.push('/reply'),
            child: child ?? const SizedBox.shrink(),
          ),
        ),
      ),
    );
  }
}

/// When the app returns to the foreground, reconnect realtime and re-read everything on screen
/// (changes made while it was in the background were not delivered), and look for an update.
class LiveResync extends ConsumerStatefulWidget {
  const LiveResync({required this.child, super.key});

  final Widget child;

  @override
  ConsumerState<LiveResync> createState() => _LiveResyncState();
}

class _LiveResyncState extends ConsumerState<LiveResync> {
  late final AppLifecycleListener _lifecycle;

  @override
  void initState() {
    super.initState();
    _lifecycle = AppLifecycleListener(
      onResume: () {
        unawaited(ref.read(cloudSourceProvider).resync());
        ref.invalidate(updateStatusProvider); // a release may have shipped while away
      },
    );
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}

/// Shown when the app was built without its Supabase settings.
class ConfigMissingApp extends StatelessWidget {
  const ConfigMissingApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      home: const Scaffold(
        backgroundColor: Color(0xFF0E0D12),
        body: Center(
          child: Padding(
            padding: EdgeInsets.all(Space.l),
            child: Text(
              'Build the app with --dart-define=SUPABASE_URL=… and --dart-define=SUPABASE_PUBLISHABLE_KEY=… '
              '(see docs/setup-macos.md).',
              textAlign: TextAlign.center,
            ),
          ),
        ),
      ),
    );
  }
}
