import 'package:flutter/material.dart';
import 'package:work_tracker/core/motion/motion.dart';
import 'package:work_tracker/core/theme/tokens.g.dart';

/// Spacing on an 8-point grid (4 is the only half step). Values come from design/tokens.json.
abstract final class Space {
  static const double xs = AuroraTokens.spaceXS;
  static const double s = AuroraTokens.spaceS;
  static const double m = AuroraTokens.spaceM;
  static const double l = AuroraTokens.spaceL;
  static const double xl = AuroraTokens.spaceXL;
  static const double xxl = AuroraTokens.spaceXXL;
}

/// Corner radii: soft cards, pill buttons and chips.
abstract final class Radii {
  static const double card = AuroraTokens.radiusCard;
  static const double tile = AuroraTokens.radiusTile;
  static const double chip = 999;

  /// Highlighted blocks of text (e.g. an unverified draft line): soft corners, never a pill,
  /// because the text can wrap over several lines.
  static const double highlight = AuroraTokens.radiusHighlight;
  static const double pill = AuroraTokens.radiusPill;
  static const BorderRadius cardRadius = BorderRadius.all(Radius.circular(card));
  static const BorderRadius tileRadius = BorderRadius.all(Radius.circular(tile));
  static const BorderRadius pillRadius = BorderRadius.all(Radius.circular(pill));
}

/// The Aurora palette: near-black (or lavender-white) surfaces with an indigo → violet → pink glow.
///
/// Text colours are chosen for WCAG AA on their surfaces; the hero gradient is deep enough for
/// white text (the lighter lavenders are used only for glows and accents).
@immutable
class AuroraColors extends ThemeExtension<AuroraColors> {
  const AuroraColors({
    required this.background,
    required this.glass,
    required this.glassStrong,
    required this.hairline,
    required this.glowA,
    required this.glowB,
    required this.glowC,
    required this.heroGradient,
    required this.onHero,
    required this.onHeroMuted,
    required this.accentGradient,
    required this.online,
  });

  /// Page background under the glow.
  final Color background;

  /// Translucent card fill (glass) and a more opaque variant for floating bars and sheets.
  final Color glass;
  final Color glassStrong;

  /// 1 px borders that catch the light on glass.
  final Color hairline;

  /// The three background glows (indigo, violet, pink).
  final Color glowA;
  final Color glowB;
  final Color glowC;

  /// The hero card and its text.
  final List<Color> heroGradient;
  final Color onHero;
  final Color onHeroMuted;

  /// Selected nav item, primary actions.
  final List<Color> accentGradient;

  /// "Mac connected" dot.
  final Color online;

  static const dark = AuroraColors(
    background: AuroraTokens.darkBackground,
    glass: AuroraTokens.darkGlass,
    glassStrong: AuroraTokens.darkGlassStrong,
    hairline: AuroraTokens.darkHairline,
    glowA: AuroraTokens.darkGlowA,
    glowB: AuroraTokens.darkGlowB,
    glowC: AuroraTokens.darkGlowC,
    heroGradient: [AuroraTokens.darkHero1, AuroraTokens.darkHero2, AuroraTokens.darkHero3],
    onHero: AuroraTokens.darkOnHero,
    onHeroMuted: AuroraTokens.darkOnHeroMuted,
    accentGradient: [AuroraTokens.darkAccent1, AuroraTokens.darkAccent2],
    online: AuroraTokens.darkOnline,
  );

  static const light = AuroraColors(
    background: AuroraTokens.lightBackground,
    glass: AuroraTokens.lightGlass,
    glassStrong: AuroraTokens.lightGlassStrong,
    hairline: AuroraTokens.lightHairline,
    glowA: AuroraTokens.lightGlowA,
    glowB: AuroraTokens.lightGlowB,
    glowC: AuroraTokens.lightGlowC,
    heroGradient: [AuroraTokens.lightHero1, AuroraTokens.lightHero2, AuroraTokens.lightHero3],
    onHero: AuroraTokens.lightOnHero,
    onHeroMuted: AuroraTokens.lightOnHeroMuted,
    accentGradient: [AuroraTokens.lightAccent1, AuroraTokens.lightAccent2],
    online: AuroraTokens.lightOnline,
  );

  @override
  AuroraColors copyWith({Color? background, Color? glass, Color? online}) => AuroraColors(
    background: background ?? this.background,
    glass: glass ?? this.glass,
    glassStrong: glassStrong,
    hairline: hairline,
    glowA: glowA,
    glowB: glowB,
    glowC: glowC,
    heroGradient: heroGradient,
    onHero: onHero,
    onHeroMuted: onHeroMuted,
    accentGradient: accentGradient,
    online: online ?? this.online,
  );

  @override
  AuroraColors lerp(ThemeExtension<AuroraColors>? other, double t) {
    if (other is! AuroraColors) return this;
    Color mix(Color a, Color b) => Color.lerp(a, b, t)!;
    return AuroraColors(
      background: mix(background, other.background),
      glass: mix(glass, other.glass),
      glassStrong: mix(glassStrong, other.glassStrong),
      hairline: mix(hairline, other.hairline),
      glowA: mix(glowA, other.glowA),
      glowB: mix(glowB, other.glowB),
      glowC: mix(glowC, other.glowC),
      heroGradient: [for (var i = 0; i < heroGradient.length; i++) mix(heroGradient[i], other.heroGradient[i])],
      onHero: mix(onHero, other.onHero),
      onHeroMuted: mix(onHeroMuted, other.onHeroMuted),
      accentGradient: [for (var i = 0; i < accentGradient.length; i++) mix(accentGradient[i], other.accentGradient[i])],
      online: mix(online, other.online),
    );
  }
}

/// Semantic colours for digest states, defined per brightness so contrast holds in both themes.
@immutable
class DigestColors extends ThemeExtension<DigestColors> {
  const DigestColors({
    required this.verified,
    required this.onVerified,
    required this.unverified,
    required this.onUnverified,
    required this.missing,
    required this.onMissing,
    required this.realAction,
    required this.onRealAction,
  });

  final Color verified;
  final Color onVerified;
  final Color unverified;
  final Color onUnverified;
  final Color missing;
  final Color onMissing;
  final Color realAction;
  final Color onRealAction;

  static const light = DigestColors(
    verified: Color(0xFFDDF6E8),
    onVerified: Color(0xFF0B3D27),
    unverified: Color(0xFFFFECD2),
    onUnverified: Color(0xFF5C2E00),
    missing: Color(0xFFFFE1E5),
    onMissing: Color(0xFF5C0A1C),
    realAction: Color(0xFFEDE4FF),
    onRealAction: Color(0xFF2E1065),
  );

  static const dark = DigestColors(
    verified: Color(0xFF123A2B),
    onVerified: Color(0xFFA7F3CF),
    unverified: Color(0xFF3F2A10),
    onUnverified: Color(0xFFFFD49A),
    missing: Color(0xFF441B25),
    onMissing: Color(0xFFFFC2CB),
    realAction: Color(0xFF2E2152),
    onRealAction: Color(0xFFDCCBFF),
  );

  @override
  DigestColors copyWith({
    Color? verified,
    Color? onVerified,
    Color? unverified,
    Color? onUnverified,
    Color? missing,
    Color? onMissing,
    Color? realAction,
    Color? onRealAction,
  }) {
    return DigestColors(
      verified: verified ?? this.verified,
      onVerified: onVerified ?? this.onVerified,
      unverified: unverified ?? this.unverified,
      onUnverified: onUnverified ?? this.onUnverified,
      missing: missing ?? this.missing,
      onMissing: onMissing ?? this.onMissing,
      realAction: realAction ?? this.realAction,
      onRealAction: onRealAction ?? this.onRealAction,
    );
  }

  @override
  DigestColors lerp(ThemeExtension<DigestColors>? other, double t) {
    if (other is! DigestColors) return this;
    return DigestColors(
      verified: Color.lerp(verified, other.verified, t)!,
      onVerified: Color.lerp(onVerified, other.onVerified, t)!,
      unverified: Color.lerp(unverified, other.unverified, t)!,
      onUnverified: Color.lerp(onUnverified, other.onUnverified, t)!,
      missing: Color.lerp(missing, other.missing, t)!,
      onMissing: Color.lerp(onMissing, other.onMissing, t)!,
      realAction: Color.lerp(realAction, other.realAction, t)!,
      onRealAction: Color.lerp(onRealAction, other.onRealAction, t)!,
    );
  }
}

/// Aurora themes on Material 3: dark-first, Manrope, glass surfaces, pill controls.
abstract final class AppTheme {
  static const String font = 'Manrope';

  static ThemeData light() => _build(Brightness.light);

  static ThemeData dark() => _build(Brightness.dark);

  static ColorScheme _scheme(Brightness brightness) {
    if (brightness == Brightness.dark) {
      return const ColorScheme(
        brightness: Brightness.dark,
        primary: Color(0xFFC4A5FF),
        onPrimary: Color(0xFF22104A),
        primaryContainer: Color(0xFF3B2A6B),
        onPrimaryContainer: Color(0xFFEADDFF),
        secondary: Color(0xFFF2A7D8),
        onSecondary: Color(0xFF3D0A2B),
        secondaryContainer: Color(0xFF2B2338),
        onSecondaryContainer: Color(0xFFEBDDFF),
        tertiary: Color(0xFF7EE2B8),
        onTertiary: Color(0xFF00382A),
        error: Color(0xFFFF8A9A),
        onError: Color(0xFF4A0010),
        surface: Color(0xFF131218),
        onSurface: Color(0xFFF3F0F8),
        onSurfaceVariant: Color(0xFFB2ADBF),
        surfaceContainerLowest: Color(0xFF0E0D12),
        surfaceContainerLow: Color(0xFF18171D),
        surfaceContainer: Color(0xFF1D1B23),
        surfaceContainerHigh: Color(0xFF24222B),
        surfaceContainerHighest: Color(0xFF2D2A35),
        outline: Color(0xFF6E6879),
        outlineVariant: Color(0xFF34313C),
        inverseSurface: Color(0xFFEDE9F4),
        onInverseSurface: Color(0xFF1B1A20),
        inversePrimary: Color(0xFF6D3FE0),
        shadow: Colors.black,
        scrim: Colors.black,
        surfaceTint: Colors.transparent,
      );
    }
    return const ColorScheme(
      brightness: Brightness.light,
      primary: Color(0xFF6237D6),
      onPrimary: Colors.white,
      primaryContainer: Color(0xFFE8DEFF),
      onPrimaryContainer: Color(0xFF22104A),
      secondary: Color(0xFFA0306E),
      onSecondary: Colors.white,
      secondaryContainer: Color(0xFFEDE6F8),
      onSecondaryContainer: Color(0xFF2B1A44),
      tertiary: Color(0xFF0F7A55),
      onTertiary: Colors.white,
      error: Color(0xFFB3261E),
      onError: Colors.white,
      surface: Color(0xFFF7F5FC),
      onSurface: Color(0xFF17151C),
      onSurfaceVariant: Color(0xFF5B5667),
      surfaceContainerLowest: Colors.white,
      surfaceContainerLow: Color(0xFFFBFAFE),
      surfaceContainer: Color(0xFFF1EDF8),
      surfaceContainerHigh: Color(0xFFEAE5F4),
      surfaceContainerHighest: Color(0xFFE3DDEF),
      outline: Color(0xFF7A7487),
      outlineVariant: Color(0xFFD6D0E2),
      inverseSurface: Color(0xFF2B2833),
      onInverseSurface: Color(0xFFF3F0F8),
      inversePrimary: Color(0xFFC4A5FF),
      shadow: Colors.black,
      scrim: Colors.black,
      surfaceTint: Colors.transparent,
    );
  }

  static ThemeData _build(Brightness brightness) {
    final scheme = _scheme(brightness);
    final aurora = brightness == Brightness.dark ? AuroraColors.dark : AuroraColors.light;
    final base = ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      brightness: brightness,
      fontFamily: font,
      visualDensity: VisualDensity.standard,
      materialTapTargetSize: MaterialTapTargetSize.padded,
    );
    final text = base.textTheme.copyWith(
      displaySmall: base.textTheme.displaySmall?.copyWith(fontWeight: FontWeight.w800, letterSpacing: -1),
      headlineMedium: base.textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800, letterSpacing: -0.5),
      headlineSmall: base.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.3),
      titleLarge: base.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
      titleMedium: base.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
      titleSmall: base.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w600),
      labelLarge: base.textTheme.labelLarge?.copyWith(fontWeight: FontWeight.w700),
    );
    return base.copyWith(
      extensions: [
        if (brightness == Brightness.light) DigestColors.light else DigestColors.dark,
        aurora,
      ],
      textTheme: text,
      // Pages paint the Aurora backdrop (see router); scaffolds stay see-through.
      scaffoldBackgroundColor: Colors.transparent,
      canvasColor: aurora.background,
      appBarTheme: AppBarTheme(
        centerTitle: false,
        backgroundColor: Colors.transparent,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        titleTextStyle: text.titleLarge?.copyWith(color: scheme.onSurface),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: aurora.glass,
        shape: RoundedRectangleBorder(borderRadius: Radii.cardRadius, side: BorderSide(color: aurora.hairline)),
        margin: EdgeInsets.zero,
        clipBehavior: Clip.antiAlias,
      ),
      chipTheme: base.chipTheme.copyWith(
        shape: const StadiumBorder(),
        side: BorderSide(color: aurora.hairline),
        backgroundColor: scheme.surfaceContainerHigh,
        selectedColor: scheme.primaryContainer,
        labelStyle: text.labelLarge?.copyWith(color: scheme.onSurface),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: scheme.surfaceContainerHigh,
        border: OutlineInputBorder(borderRadius: Radii.tileRadius, borderSide: BorderSide(color: aurora.hairline)),
        enabledBorder: OutlineInputBorder(borderRadius: Radii.tileRadius, borderSide: BorderSide(color: aurora.hairline)),
        focusedBorder: OutlineInputBorder(borderRadius: Radii.tileRadius, borderSide: BorderSide(color: scheme.primary, width: 1.5)),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(64, 52),
          shape: const StadiumBorder(),
          textStyle: text.labelLarge,
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(64, 52),
          shape: const StadiumBorder(),
          side: BorderSide(color: aurora.hairline),
          textStyle: text.labelLarge,
        ),
      ),
      textButtonTheme: TextButtonThemeData(style: TextButton.styleFrom(minimumSize: const Size(48, 48), shape: const StadiumBorder())),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        shape: const StadiumBorder(),
        backgroundColor: scheme.primary,
        foregroundColor: scheme.onPrimary,
        elevation: 0,
        highlightElevation: 0,
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: aurora.glassStrong,
        surfaceTintColor: Colors.transparent,
        showDragHandle: true,
        shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(Radii.card))),
      ),
      dialogTheme: DialogThemeData(backgroundColor: aurora.glassStrong, surfaceTintColor: Colors.transparent),
      tabBarTheme: TabBarThemeData(
        labelColor: scheme.primary,
        unselectedLabelColor: scheme.onSurfaceVariant,
        indicatorColor: scheme.primary,
        dividerColor: aurora.hairline,
        labelStyle: text.titleSmall,
        unselectedLabelStyle: text.titleSmall,
      ),
      listTileTheme: ListTileThemeData(iconColor: scheme.primary, shape: const RoundedRectangleBorder(borderRadius: Radii.tileRadius)),
      dividerTheme: DividerThemeData(color: aurora.hairline, space: 1),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: const StadiumBorder(),
        backgroundColor: scheme.inverseSurface,
        contentTextStyle: text.bodyMedium?.copyWith(color: scheme.onInverseSurface),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(color: scheme.primary),
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: FluidPageTransitionsBuilder(),
          TargetPlatform.iOS: FluidPageTransitionsBuilder(),
          TargetPlatform.macOS: FluidPageTransitionsBuilder(),
        },
      ),
    );
  }
}

extension DigestTheme on BuildContext {
  DigestColors get digestColors => Theme.of(this).extension<DigestColors>() ?? DigestColors.dark;

  AuroraColors get aurora => Theme.of(this).extension<AuroraColors>() ?? AuroraColors.dark;
}
