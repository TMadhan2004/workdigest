import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:work_tracker/app.dart';
import 'package:work_tracker/core/config/app_config.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/data/sources/cloud_source.dart';
import 'package:work_tracker/data/sources/snapshot_cache.dart';
import 'package:work_tracker/features/update/background.dart';
import 'package:work_tracker/features/update/update_schedule.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final config = AppConfig.fromEnvironment();
  if (!config.isComplete) {
    runApp(const ConfigMissingApp());
    return;
  }
  await Supabase.initialize(url: config.supabaseUrl, publishableKey: config.publishableKey);
  final prefs = await SharedPreferences.getInstance();
  await initNotifications();
  await scheduleUpdateChecks(config.siteUrl);
  runApp(
    ProviderScope(
      overrides: [
        cloudSourceProvider.overrideWithValue(SupabaseCloudSource(Supabase.instance.client)),
        snapshotCacheProvider.overrideWithValue(SnapshotCache(prefs)),
        preferencesProvider.overrideWithValue(prefs),
      ],
      child: const WorkDigestApp(),
    ),
  );
}
