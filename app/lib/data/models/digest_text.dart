import 'package:flutter/foundation.dart';

/// The WhatsApp digest text split into blocks: header, numbered sections, closing.
///
/// This mirrors the Mac's parser (`recorder/src/workdigest/digest/document.py`). Sections are
/// lines starting at column 0 with `N. `, and their bodies are indented. The closing is unindented
/// text after a blank line that follows the last section's body. Editing one block and joining
/// again leaves every other block byte-for-byte unchanged.
@immutable
class DigestText {
  const DigestText({required this.header, required this.sections, required this.closing});

  factory DigestText.parse(String text) {
    final lines = text.replaceAll('\r\n', '\n').split('\n');
    final header = <String>[];
    final sections = <int, List<String>>{};
    final order = <int>[];
    final closing = <String>[];
    int? current;
    var blankBefore = false;
    for (final line in lines) {
      final match = _section.firstMatch(line);
      if (match != null && closing.isEmpty) {
        current = int.parse(match.group(1)!);
        order.add(current);
        sections[current] = [line];
        blankBefore = false;
        continue;
      }
      if (current == null) {
        header.add(line);
        continue;
      }
      if (line.trim().isEmpty) {
        blankBefore = true;
        (closing.isEmpty ? sections[current]! : closing).add(line);
        continue;
      }
      final unindented = !line.startsWith(' ') && !line.startsWith('\t');
      if (closing.isNotEmpty || (unindented && blankBefore && sections[current]!.length > 1)) {
        closing.add(line);
      } else {
        sections[current]!.add(line);
      }
      blankBefore = false;
    }
    return DigestText(
      header: header.join('\n'),
      sections: {for (final n in order) n: _trimTrailingBlank(sections[n]!).join('\n')},
      closing: closing.join('\n').trim(),
    );
  }

  static final RegExp _section = RegExp(r'^(\d{1,2})\.\s+\S');

  final String header;

  /// Section number → its full block, including the "N. Title" line.
  final Map<int, String> sections;
  final String closing;

  DigestText replaceSection(int number, String block) {
    if (!sections.containsKey(number)) throw ArgumentError('no section $number');
    return DigestText(header: header, sections: {...sections, number: block.trimRight()}, closing: closing);
  }

  DigestText replaceClosing(String text) => DigestText(header: header, sections: sections, closing: text.trim());

  String join() {
    final buffer = StringBuffer(header.trimRight())
      ..write('\n\n')
      ..write(sections.values.join('\n\n'));
    if (closing.isNotEmpty) buffer.write('\n\n$closing');
    return '${buffer.toString().trimRight()}\n';
  }

  static List<String> _trimTrailingBlank(List<String> lines) {
    var end = lines.length;
    while (end > 0 && lines[end - 1].trim().isEmpty) {
      end--;
    }
    return lines.sublist(0, end);
  }
}

/// Plain-text checks that WhatsApp will render the draft as intended.
abstract final class WhatsAppText {
  static final RegExp _markdownHeading = RegExp(r'^\s*#{1,6}\s', multiLine: true);

  /// Problems that would break WhatsApp formatting (empty when the text is fine).
  static List<String> problems(String text) => [
    if (_markdownHeading.hasMatch(text)) 'Markdown headings (#) are shown literally in WhatsApp',
    if (text.contains('**')) 'Use *bold*, not **bold**',
    if (RegExp(r'^\s*\|.*\|\s*$', multiLine: true).hasMatch(text)) 'Tables do not render in WhatsApp',
    if (RegExp(r'\[[^\]]+\]\(https?://').hasMatch(text)) 'Links must be plain URLs, not [text](url)',
  ];
}
