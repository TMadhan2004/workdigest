import 'package:flutter_test/flutter_test.dart';

import 'app_flow.dart';

void main() {
  testWidgets('TC-APP-100: evening flow end to end (sign in → reply → close day → draft → share)', (tester) async {
    // Given: a signed-out phone, a Mac that answers jobs, and Priya's reply to paste.
    // When: the whole evening flow is driven through the real router and screens.
    // Then: the reply item is confirmed, the close-day job carries the rating, the Mac's draft appears,
    //       and the exact WhatsApp text goes to the share sheet and is marked sent.
    await runEveningFlow(tester);
  });
}
