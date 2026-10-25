import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:receive_sharing_intent/receive_sharing_intent.dart';

/// Text shared into the app (long-press the manager's reply in WhatsApp → Share → WorkDigest).
final sharedTextProvider = StateProvider<String?>((ref) => null);

/// Whether to listen for shared text (tests turn it off: there is no platform plugin there).
final shareIntakeEnabledProvider = Provider<bool>((ref) => ShareIntakeListener.supported);

/// Listens for text shared from other apps and hands it to the reply intake screen.
///
/// Only Android and iOS have a share sheet target; elsewhere this is a no-op wrapper.
class ShareIntakeListener extends ConsumerStatefulWidget {
  const ShareIntakeListener({required this.child, required this.onShared, super.key});

  final Widget child;
  final void Function(String text) onShared;

  static bool get supported =>
      !kIsWeb && (defaultTargetPlatform == TargetPlatform.android || defaultTargetPlatform == TargetPlatform.iOS);

  @override
  ConsumerState<ShareIntakeListener> createState() => _ShareIntakeListenerState();
}

class _ShareIntakeListenerState extends ConsumerState<ShareIntakeListener> {
  StreamSubscription<List<SharedMediaFile>>? _subscription;

  @override
  void initState() {
    super.initState();
    if (!ref.read(shareIntakeEnabledProvider)) return;
    final intent = ReceiveSharingIntent.instance;
    _subscription = intent.getMediaStream().listen(_handle);
    unawaited(intent.getInitialMedia().then(_handle).whenComplete(intent.reset));
  }

  void _handle(List<SharedMediaFile> files) {
    final text = files
        .where((f) => f.type == SharedMediaType.text || f.type == SharedMediaType.url)
        .map((f) => f.path)
        .where((t) => t.trim().isNotEmpty)
        .join('\n\n');
    if (text.isEmpty) return;
    ref.read(sharedTextProvider.notifier).state = text;
    widget.onShared(text);
  }

  @override
  void dispose() {
    unawaited(_subscription?.cancel());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
