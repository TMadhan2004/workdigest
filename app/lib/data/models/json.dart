/// Small, strict helpers for reading JSON from Supabase rows.
///
/// Rows come from our own schema, but a field can still be null or missing (older rows, partial
/// selects). These helpers make the fallback explicit instead of throwing deep in the UI.
library;

typedef Json = Map<String, dynamic>;

String str(Json json, String key, [String fallback = '']) {
  final value = json[key];
  return value == null ? fallback : value.toString();
}

String? strOrNull(Json json, String key) {
  final value = json[key];
  return value?.toString();
}

int intOf(Json json, String key, [int fallback = 0]) {
  final value = json[key];
  if (value is int) return value;
  if (value is num) return value.toInt();
  return int.tryParse('${value ?? ''}') ?? fallback;
}

int? intOrNull(Json json, String key) {
  final value = json[key];
  if (value == null) return null;
  if (value is num) return value.toInt();
  return int.tryParse('$value');
}

bool boolOf(Json json, String key, {bool fallback = false}) {
  final value = json[key];
  return value is bool ? value : fallback;
}

List<String> strings(Json json, String key) {
  final value = json[key];
  if (value is List) return value.map((e) => e.toString()).toList(growable: false);
  return const [];
}

List<Json> objects(Json json, String key) {
  final value = json[key];
  if (value is List) return value.whereType<Map<String, dynamic>>().toList(growable: false);
  return const [];
}

Json object(Json json, String key) {
  final value = json[key];
  return value is Map<String, dynamic> ? value : const {};
}

DateTime? dateTimeOrNull(Json json, String key) {
  final value = json[key];
  return value == null ? null : DateTime.tryParse(value.toString());
}

/// ``YYYY-MM-DD`` for a calendar day.
String isoDay(DateTime day) =>
    '${day.year.toString().padLeft(4, '0')}-${day.month.toString().padLeft(2, '0')}-${day.day.toString().padLeft(2, '0')}';
