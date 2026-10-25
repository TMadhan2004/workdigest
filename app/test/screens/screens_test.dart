import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import '../fakes/fakes.dart';

/// Real screens of the app for the website, rendered with the real fonts and the fictional demo
/// data the tests use. Skipped in normal test runs; generate them with `make screens`:
///
/// ```sh
/// flutter test test/screens --dart-define=SCREENS=true --update-goldens   # → app/build/screens/*.png
/// ```
const _enabled = bool.fromEnvironment('SCREENS');

Future<void> _loadFont(String family, List<String> files) async {
  final loader = FontLoader(family);
  for (final file in files) {
    loader.addFont(Future.value(ByteData.sublistView(File(file).readAsBytesSync())));
  }
  await loader.load();
}

void main() {
  setUpAll(() async {
    if (!_enabled) return;
    // Tests draw text with a block font by default; load the app's real fonts instead.
    await _loadFont('Manrope', [for (final w in [400, 500, 600, 700, 800]) 'assets/fonts/Manrope-$w.ttf']);
    final flutterRoot = File(Platform.resolvedExecutable).parent.parent.parent.parent.parent.parent.path;
    await _loadFont('MaterialIcons', ['$flutterRoot/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf']);
  });

  for (final (name, location) in const [
    ('today', '/today'),
    ('timeline', '/timeline'),
    ('draft', '/draft'),
    ('asks', '/asks'),
    ('history', '/more'),
    ('settings', '/today/profile/settings'),
  ]) {
    testWidgets('TC-APP-151: real screen for the website ($name)', (tester) async {
      // Given: the demo data, dark theme and the app's real fonts.
      // When: the screen opens and its animations finish.
      // Then: it is saved as build/screens/<name>.png for `make screens` (skipped in normal runs).
      await pumpApp(tester, location: location, brightness: Brightness.dark);
      await tester.pump(const Duration(seconds: 2));
      await expectLater(find.byType(MaterialApp), matchesGoldenFile('../../build/screens/$name.png'));
    }, skip: !_enabled);
  }
}
