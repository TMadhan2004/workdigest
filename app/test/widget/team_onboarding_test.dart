import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:work_tracker/core/config/app_config.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/features/auth/sign_in_screen.dart';
import 'package:work_tracker/features/update/background.dart';
import 'package:work_tracker/features/update/update_service.dart';

import '../fakes/fakes.dart';

const _config = AppConfig(
  supabaseUrl: 'https://demo.supabase.co',
  publishableKey: 'pk',
  siteUrl: 'https://workdigest.example',
  allowedDomain: 'ei.study',
);

const _release = Release(
  version: '1.4.0',
  build: 14,
  apkUrl: 'https://workdigest.example/download/WorkDigest.apk',
  sha256: 'abc',
  minBuild: 9,
  notes: 'Asks can be closed with a date filter.\nFaster drafts.',
  size: 31457280,
);

/// The team's build, with the website check answered "no update" (no network in tests).
final List<Override> _noUpdates = [
  appConfigProvider.overrideWithValue(_config),
  updateStatusProvider.overrideWith((ref) async => null),
];

void main() {
  group('Mac first', () {
    testWidgets('TC-APP-140: sign-in only takes company emails and shows how to set up a Mac', (tester) async {
      // Given: a signed-out app built for the ei.study team.
      // When: a gmail address is tried.
      // Then: "Use your @ei.study email" without asking the server; the Mac command is on screen.
      final cloud = seededCloud()..signedIn = false;
      await pumpApp(tester, cloud: cloud, overrides: _noUpdates);
      await tester.enterText(find.widgetWithText(TextFormField, 'Work email'), 'me@gmail.com');
      await tester.enterText(find.widgetWithText(TextFormField, 'Password'), 'right');
      await tester.tap(find.text('Sign in'));
      await tester.pumpAndSettle();
      expect(find.text('Use your @ei.study email'), findsOneWidget);
      expect(cloud.calls.where((c) => c.startsWith('signIn')), isEmpty);
      expect(find.text('New here? Set up on your Mac first'), findsOneWidget);
      expect(find.text('curl -fsSL https://workdigest.example/install.sh | bash'), findsOneWidget);
    });

    testWidgets('TC-APP-141: an account without a Mac waits on "Finish setup", then opens Today when the Mac connects', (tester) async {
      // Given: a signed-in account whose Mac has never sent a heartbeat.
      // When: the app opens, then the Mac's first heartbeat arrives live.
      // Then: "Finish setup on your Mac" with the command; then Today without any tap.
      final cloud = seededCloud()..tables['devices']!.clear();
      await pumpApp(tester, cloud: cloud, disableAnimations: true, overrides: _noUpdates);
      expect(find.text('Finish setup on your Mac'), findsOneWidget);
      expect(find.text('Waiting for your Mac…'), findsOneWidget);
      cloud.macWrites('devices', [
        {'device_id': 'mac-1', 'kind': 'mac', 'label': 'DEMO-MAC-01', 'last_seen_at': fixedNow.toUtc().toIso8601String()},
      ]);
      await tester.pumpAndSettle();
      expect(find.text('Finish setup on your Mac'), findsNothing);
      expect(find.text('Friday, 23 October'), findsOneWidget);
    });

    test('TC-APP-142: sign-in errors say what to do', () {
      // Given: the errors Supabase returns.
      // When: they are turned into messages.
      // Then: wrong password points to the Mac setup; unconfirmed email and no network are explained.
      expect(signInMessage(StateError('Invalid login credentials')), contains('Set up WorkDigest on your Mac'));
      expect(signInMessage(StateError('Email not confirmed')), contains('Confirm your email'));
      expect(signInMessage(const SocketExceptionLike()), contains('No connection'));
      expect(validateCompanyEmail(' Arjun.K@EI.study ', 'ei.study'), isNull);
      expect(validateCompanyEmail('arjun', 'ei.study'), 'Enter your work email');
    });
  });

  group('Updates', () {
    test('TC-APP-143: version.json is read safely and each release is announced once', () {
      // Given: version.json files, good and broken; an install at build 10.
      // When: they are parsed and checked.
      // Then: the good one is read; http links and missing builds are refused; a release is announced once.
      final release = Release.fromVersionJson({
        'version': '1.4.0',
        'android': {'build': 14, 'apk_url': 'https://x/WorkDigest.apk', 'sha256': 'abc', 'min_build': 9, 'notes': 'New\nMore'},
      })!;
      expect((release.version, release.build, release.minBuild), ('1.4.0', 14, 9));
      expect(Release.fromVersionJson({'android': {'build': 14, 'apk_url': 'http://x/a.apk'}}), isNull);
      expect(Release.fromVersionJson({'android': {'apk_url': 'https://x/a.apk'}}), isNull);
      expect(Release.fromVersionJson({'mac': <String, Object>{}}), isNull);
      const current = AppVersion('1.3.0', 10);
      expect(shouldNotify(release, current, null), isTrue);
      expect(shouldNotify(release, current, 14), isFalse);
      expect(shouldNotify(release, const AppVersion('1.4.0', 14), null), isFalse);
      expect(updateNotice(release), ('WorkDigest 1.4.0 is live', 'New'));
      expect(const UpdateStatus(current: current, latest: _release).required, isFalse);
      expect(const UpdateStatus(current: AppVersion('1.0.0', 8), latest: _release).required, isTrue);
    });

    testWidgets('TC-APP-144: a new release shows a banner; Update downloads with progress and opens the installer', (tester) async {
      // Given: build 10 installed and 1.4.0 (build 14) on the website.
      // When: Update is tapped and the download runs; then a damaged download.
      // Then: the banner with the first note line; progress 40%; "installer is open"; the checksum message and Try again.
      final events = StreamController<InstallProgress>();
      await pumpApp(
        tester,
        overrides: [
          appConfigProvider.overrideWithValue(_config),
          updateStatusProvider.overrideWith((ref) async => const UpdateStatus(current: AppVersion('1.3.0', 10), latest: _release)),
          updateInstallerProvider.overrideWithValue((_) => events.stream),
        ],
      );
      expect(find.text('WorkDigest 1.4.0 is live'), findsOneWidget);
      expect(find.text('Asks can be closed with a date filter.'), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, 'Update'));
      await tester.pumpAndSettle();
      expect(find.text("What's new"), findsOneWidget);
      expect(find.textContaining('30.0 MB'), findsOneWidget);
      await tester.tap(find.text('Download and install'));
      events.add(const InstallProgress(fraction: 0.4));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.text('Downloading… 40%'), findsOneWidget);
      events.add(const InstallProgress(fraction: 1, installing: true));
      await tester.pumpAndSettle();
      expect(find.textContaining("Android's installer is open"), findsOneWidget);
      events.add(const InstallProgress(error: 'The download was damaged, so it was not installed. Try again.'));
      await tester.pumpAndSettle();
      expect(find.textContaining('damaged'), findsOneWidget);
      expect(find.text('Try again'), findsOneWidget);
      await events.close();
    });

    test('TC-APP-146: only release builds look for updates', () {
      // Given: a build with the website configured, running as a debug build (as tests do).
      // When: the update source is read.
      // Then: there is none, so a debug-signed install never offers the release-signed APK,
      //       which Android would refuse as a conflicting package.
      final container = ProviderContainer(overrides: [appConfigProvider.overrideWithValue(_config)]);
      addTearDown(container.dispose);
      expect(updatesSupported, isFalse);
      expect(container.read(updateSourceProvider), isNull);
    });

    testWidgets('TC-APP-145: an install below the minimum build must update first', (tester) async {
      // Given: build 8 installed; the release needs at least build 9.
      // When: the app opens.
      // Then: only "Update required" shows, with the download button; Today is not reachable.
      await pumpApp(
        tester,
        overrides: [
          appConfigProvider.overrideWithValue(_config),
          updateStatusProvider.overrideWith((ref) async => const UpdateStatus(current: AppVersion('1.0.0', 8), latest: _release)),
        ],
      );
      expect(find.text('Update required'), findsOneWidget);
      expect(find.text('Download and install'), findsOneWidget);
      expect(find.text('Friday, 23 October'), findsNothing);
    });
  });
}

/// Stands in for a network failure (its text mentions the socket).
class SocketExceptionLike implements Exception {
  const SocketExceptionLike();

  @override
  String toString() => 'SocketException: Failed host lookup';
}
