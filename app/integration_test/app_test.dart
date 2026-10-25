import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import '../test/e2e/app_flow.dart';

/// On-device version of the evening flow (needs a simulator, emulator or phone):
/// `flutter test integration_test -d <device>`.
void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('TC-APP-101: evening flow on a device', runEveningFlow);
}
