/// Build-time configuration, passed with `--dart-define`:
///
/// ```sh
/// flutter run \
///   --dart-define=SUPABASE_URL=https://<ref>.supabase.co \
///   --dart-define=SUPABASE_PUBLISHABLE_KEY=<publishable or anon key> \
///   --dart-define=SITE_URL=https://<the WorkDigest website>   # update checks; optional
/// ```
///
/// Only the public publishable key ever ships in the app. It is safe to embed by design,
/// because row-level security protects every table. The service-role key must never be used here.
class AppConfig {
  const AppConfig({required this.supabaseUrl, required this.publishableKey, required this.siteUrl, required this.allowedDomain});

  factory AppConfig.fromEnvironment() => const AppConfig(
    supabaseUrl: String.fromEnvironment('SUPABASE_URL'),
    publishableKey: String.fromEnvironment('SUPABASE_PUBLISHABLE_KEY'),
    siteUrl: String.fromEnvironment('SITE_URL'),
    allowedDomain: String.fromEnvironment('ALLOWED_DOMAIN', defaultValue: 'ei.study'),
  );

  final String supabaseUrl;
  final String publishableKey;

  /// The WorkDigest website: version.json for updates, and the Mac setup instructions.
  final String siteUrl;

  /// Only accounts on this email domain exist (the database refuses other sign-ups).
  final String allowedDomain;

  /// The command a new teammate pastes into Terminal on their Mac.
  String get macInstallCommand => 'curl -fsSL ${siteUrl.isEmpty ? 'https://<website>' : siteUrl}/install.sh | bash';

  bool get isComplete => supabaseUrl.startsWith('https://') && publishableKey.isNotEmpty;
}
