@Tags(['golden'])
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../fakes/fakes.dart';

/// Screenshots of the main screens in light, dark and large text.
///
/// Update after an intended visual change with `flutter test --update-goldens test/golden`.
/// Text renders in the test font (blocks), so goldens check layout, colour and spacing.
void main() {
  const variants = [
    ('light', Brightness.light, 1.0),
    ('dark', Brightness.dark, 1.0),
    ('large_text', Brightness.light, 1.6),
  ];
  for (final (name, brightness, scale) in variants) {
    for (final (screen, location) in const [('today', '/today'), ('draft', '/draft'), ('asks', '/asks'), ('history', '/more')]) {
      testWidgets('TC-APP-090: $screen golden ($name)', (tester) async {
        // Given: the screen with real data in the given theme and text size.
        // When: it is rendered.
        // Then: it matches the reviewed screenshot.
        await pumpApp(tester, location: location, brightness: brightness, textScale: scale);
        await expectLater(find.byType(MaterialApp), matchesGoldenFile('goldens/${screen}_$name.png'));
      });
    }
  }
}
