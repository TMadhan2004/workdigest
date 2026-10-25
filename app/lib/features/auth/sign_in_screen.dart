import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:work_tracker/core/theme/app_theme.dart';
import 'package:work_tracker/core/widgets/aurora.dart';
import 'package:work_tracker/data/providers.dart';
import 'package:work_tracker/features/onboarding/mac_setup.dart';

/// Email and password sign-in. Accounts are only created on the Mac (`digest setup`), so a new
/// teammate is pointed there; the phone never signs anyone up.
class SignInScreen extends ConsumerStatefulWidget {
  const SignInScreen({super.key});

  @override
  ConsumerState<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends ConsumerState<SignInScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _form = GlobalKey<FormState>();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(cloudSourceProvider).signIn(email: _email.text.trim(), password: _password.text);
    } on Object catch (error) {
      if (mounted) setState(() => _error = signInMessage(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(Space.l),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Form(
                key: _form,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Center(
                      child: DecoratedBox(
                        decoration: BoxDecoration(
                          borderRadius: const BorderRadius.all(Radius.circular(24)),
                          boxShadow: [BoxShadow(color: context.aurora.heroGradient[1].withValues(alpha: 0.45), blurRadius: 32)],
                        ),
                        // The WorkDigest logo: the same artwork as the app icon and the website.
                        child: Image.asset('assets/branding/logo.png', width: 88, height: 88, semanticLabel: 'WorkDigest logo'),
                      ),
                    ),
                    const SizedBox(height: Space.m),
                    Text('WorkDigest', style: theme.textTheme.headlineMedium, textAlign: TextAlign.center),
                    const SizedBox(height: Space.s),
                    Text(
                      'Your daily digest, drafted from evidence. You review and send it yourself.',
                      style: theme.textTheme.bodyMedium,
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: Space.xl),
                    TextFormField(
                      controller: _email,
                      keyboardType: TextInputType.emailAddress,
                      autofillHints: const [AutofillHints.email],
                      decoration: InputDecoration(labelText: 'Work email', hintText: 'you@${ref.watch(appConfigProvider).allowedDomain}'),
                      validator: (v) => validateCompanyEmail(v ?? '', ref.read(appConfigProvider).allowedDomain),
                    ),
                    const SizedBox(height: Space.m),
                    TextFormField(
                      controller: _password,
                      obscureText: true,
                      autofillHints: const [AutofillHints.password],
                      decoration: const InputDecoration(labelText: 'Password'),
                      validator: (v) => (v ?? '').isEmpty ? 'Enter your password' : null,
                      onFieldSubmitted: (_) => _submit(),
                    ),
                    if (_error != null) ...[
                      const SizedBox(height: Space.m),
                      Text(_error!, style: TextStyle(color: theme.colorScheme.error), semanticsLabel: _error),
                    ],
                    const SizedBox(height: Space.l),
                    FilledButton(
                      onPressed: _busy ? null : _submit,
                      child: _busy
                          ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2))
                          : const Text('Sign in'),
                    ),
                    const SizedBox(height: Space.xl),
                    GlassCard(
                      padding: const EdgeInsets.all(Space.l),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('New here? Set up on your Mac first', style: theme.textTheme.titleSmall),
                          const SizedBox(height: Space.xs),
                          Text(
                            'Your account is created during the Mac setup. Open Terminal on your Mac and paste:',
                            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                          ),
                          const SizedBox(height: Space.s),
                          const MacInstallCommand(),
                          const SizedBox(height: Space.s),
                          Text(
                            'Then sign in here with the same email and password.',
                            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Only company accounts exist; say so before asking the server.
String? validateCompanyEmail(String value, String domain) {
  final email = value.trim().toLowerCase();
  if (!email.contains('@')) return 'Enter your work email';
  if (!email.endsWith('@${domain.toLowerCase()}')) return 'Use your @$domain email';
  return null;
}

/// A sign-in error in words a person can act on.
String signInMessage(Object error) {
  final text = '$error'.toLowerCase();
  if (text.contains('invalid login') || text.contains('invalid_credentials') || text.contains('invalid credentials')) {
    return 'Wrong email or password. No account yet? Set up WorkDigest on your Mac first (below).';
  }
  if (text.contains('email not confirmed')) {
    return 'Confirm your email first: open the link sent to you during the Mac setup.';
  }
  if (text.contains('socket') || text.contains('network') || text.contains('connection')) {
    return 'No connection. Check your internet and try again.';
  }
  return 'Sign-in failed: $error';
}
