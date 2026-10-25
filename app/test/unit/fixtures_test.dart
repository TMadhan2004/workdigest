import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import '../fixtures/embedded_fixtures.dart';

void main() {
  test('TC-APP-104: embedded fixtures match the JSON files they were generated from', () {
    // Given: the JSON files in test/fixtures and the generated embedded_fixtures.dart.
    // When: both are compared file by file.
    // Then: every JSON file is embedded with identical text (otherwise run scripts/embed_app_fixtures.py).
    final files = Directory('test/fixtures').listSync().whereType<File>().where((f) => f.path.endsWith('.json'));
    final onDisk = {for (final f in files) f.uri.pathSegments.last: f.readAsStringSync()};
    expect(embeddedFixtures.keys.toSet(), onDisk.keys.toSet());
    for (final name in onDisk.keys) {
      expect(embeddedFixtures[name], onDisk[name], reason: name);
    }
  });
}
