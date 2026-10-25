import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/features/asks_rules/asks_screen.dart';
import 'package:work_tracker/features/auth/sign_in_screen.dart';
import 'package:work_tracker/features/draft_editor/draft_screen.dart';
import 'package:work_tracker/features/history/history_screen.dart';
import 'package:work_tracker/features/onboarding/mac_setup.dart';
import 'package:work_tracker/features/profile/profile_screen.dart';
import 'package:work_tracker/features/reply_intake/reply_screen.dart';
import 'package:work_tracker/features/settings/settings_screen.dart';
import 'package:work_tracker/features/shell/app_shell.dart';
import 'package:work_tracker/features/timeline/timeline_screen.dart';
import 'package:work_tracker/features/today/today_screen.dart';

/// Routes. Signed-out users always land on /signin; an account whose Mac has never connected
/// lands on /setup-mac until the first heartbeat arrives. Every page paints the Aurora backdrop
/// itself, so pages are opaque during transitions while scaffolds stay transparent.
final routerProvider = Provider<GoRouter>((ref) {
  final auth = ValueNotifier<String?>(ref.read(cloudSourceProvider).userId);
  // Has a Mac ever sent a heartbeat for this account? null while unknown (never redirect then).
  final hasMac = ValueNotifier<bool?>(null);
  final refresh = Listenable.merge([auth, hasMac]);
  ref
    ..listen(userIdProvider, (previous, next) {
      auth.value = next.valueOrNull;
      if (previous?.valueOrNull != next.valueOrNull) hasMac.value = null;
    })
    ..listen(macStatusProvider, (_, next) {
      if (next.hasValue && !next.isLoading) hasMac.value = next.value != null;
    }, fireImmediately: true)
    ..onDispose(auth.dispose)
    ..onDispose(hasMac.dispose);

  return GoRouter(
    initialLocation: '/today',
    refreshListenable: refresh,
    redirect: (context, state) {
      final signedIn = auth.value != null;
      final location = state.matchedLocation;
      if (!signedIn) return location == '/signin' ? null : '/signin';
      if (hasMac.value == false) return location == '/setup-mac' ? null : '/setup-mac';
      if (location == '/signin' || (location == '/setup-mac' && hasMac.value == true)) return '/today';
      return null;
    },
    routes: [
      GoRoute(path: '/signin', builder: (context, state) => const AuroraBackground(child: SignInScreen())),
      GoRoute(path: '/setup-mac', builder: (context, state) => const AuroraBackground(child: FinishSetupScreen())),
      GoRoute(
        path: '/reply',
        pageBuilder: (context, state) => MaterialPage(
          fullscreenDialog: true,
          child: AuroraBackground(child: ReplyScreen(initialText: state.uri.queryParameters['text'])),
        ),
      ),
      StatefulShellRoute(
        builder: (context, state, shell) => shell,
        navigatorContainerBuilder: (context, shell, children) => AppShell(shell: shell, children: children),
        branches: [
          StatefulShellBranch(
            // A hero controller so the avatar flies from Today into the profile header.
            observers: [HeroController()],
            routes: [
              GoRoute(
                path: '/today',
                builder: (context, state) => const AuroraBackground(child: TodayScreen()),
                routes: [
                  GoRoute(
                    path: 'profile',
                    builder: (context, state) => const AuroraBackground(child: ProfileScreen()),
                    routes: [GoRoute(path: 'settings', builder: (context, state) => const AuroraBackground(child: SettingsScreen()))],
                  ),
                ],
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/draft',
                builder: (context, state) => AuroraBackground(
                  child: DraftScreen(day: state.uri.queryParameters['day'], jobId: state.uri.queryParameters['job']),
                ),
              ),
            ],
          ),
          StatefulShellBranch(routes: [GoRoute(path: '/asks', builder: (context, state) => const AuroraBackground(child: AsksScreen()))]),
          StatefulShellBranch(routes: [GoRoute(path: '/timeline', builder: (context, state) => const AuroraBackground(child: TimelineScreen()))]),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/more',
                builder: (context, state) => const AuroraBackground(child: HistoryScreen()),
              ),
            ],
          ),
        ],
      ),
    ],
  );
});
