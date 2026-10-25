import 'dart:math';

final Random _random = Random.secure();

/// A unique, sortable id for rows the app creates (digest versions, replies).
String newId(String prefix, DateTime now) {
  final suffix = List.generate(6, (_) => _random.nextInt(36).toRadixString(36)).join();
  return '$prefix-${now.microsecondsSinceEpoch.toRadixString(36)}-$suffix';
}

/// The work day for ``now``: before the boundary hour it still belongs to the previous day.
DateTime workDay(DateTime now, {int boundaryHour = 4}) {
  final shifted = now.subtract(Duration(hours: boundaryHour));
  return DateTime(shifted.year, shifted.month, shifted.day);
}
