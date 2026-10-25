import 'package:flutter/services.dart';
import 'package:share_plus/share_plus.dart';

/// Sends the digest to WhatsApp (or anywhere) through the system share sheet, or copies it.
///
/// The share sheet is used rather than `wa.me` links, which break on long text. Nothing is ever
/// sent automatically: the user picks WhatsApp and the chat and presses send.
abstract class ShareService {
  Future<bool> share(String text, {String? subject});

  Future<void> copy(String text);
}

class SystemShareService implements ShareService {
  const SystemShareService();

  @override
  Future<bool> share(String text, {String? subject}) async {
    final result = await SharePlus.instance.share(ShareParams(text: text, subject: subject));
    return result.status == ShareResultStatus.success;
  }

  @override
  Future<void> copy(String text) => Clipboard.setData(ClipboardData(text: text));
}
