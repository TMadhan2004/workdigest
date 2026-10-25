import java.util.Properties

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Release signing: CI passes the keystore through environment variables (from GitHub secrets);
// a developer can use android/key.properties instead. Neither is ever committed.
val keyProperties = Properties().apply {
    val file = rootProject.file("key.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}
fun signingValue(env: String, key: String): String? = System.getenv(env) ?: keyProperties.getProperty(key)
val releaseStoreFile = signingValue("WORKDIGEST_KEYSTORE_PATH", "storeFile")

android {
    namespace = "com.workdigest.app"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // flutter_local_notifications uses java.time on older Android versions.
        isCoreLibraryDesugaringEnabled = true
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        applicationId = "com.workdigest.app"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        // Set by the release workflow with --build-name / --build-number.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (releaseStoreFile != null) {
            create("release") {
                storeFile = file(releaseStoreFile)
                storePassword = signingValue("WORKDIGEST_KEYSTORE_PASSWORD", "storePassword")
                keyAlias = signingValue("WORKDIGEST_KEY_ALIAS", "keyAlias")
                keyPassword = signingValue("WORKDIGEST_KEY_PASSWORD", "keyPassword")
            }
        }
    }

    buildTypes {
        release {
            // Without a release keystore (a developer's machine), fall back to the debug keys so
            // `flutter run --release` still works. Such builds cannot update a released app.
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.5")
}

flutter {
    source = "../.."
}
