import 'package:work_tracker/data/models/json.dart';
import 'package:work_tracker/data/models/models.dart';
import 'package:work_tracker/data/sources/cloud_source.dart';

/// User settings and the digest template (the cloud scheduler and the Mac both read these).
class SettingsRepository {
  SettingsRepository(this._source);

  final CloudSource _source;

  Future<AppSettings> load() async {
    final rows = await _source.select('settings', limit: 1);
    return rows.isEmpty ? const AppSettings() : AppSettings.fromJson(rows.first);
  }

  Future<void> save(AppSettings settings) async {
    await _source.upsert('settings', [settings.toJson()], onConflict: 'user_id');
  }

  /// The active template body (null means the Mac's built-in default).
  Future<Json?> activeTemplate() async {
    final rows = await _source.select('templates', where: [const Filter.eq('active', true)], limit: 1);
    return rows.isEmpty ? null : object(rows.first, 'body');
  }

  /// Store an edited template as a new active version (old versions stay for history).
  Future<void> saveTemplate(Json body, {required String version}) async {
    await _source.update('templates', {'active': false}, where: [const Filter.eq('active', true)]);
    await _source.insert('templates', [
      {'version': version, 'body': body, 'active': true},
    ]);
  }
}
