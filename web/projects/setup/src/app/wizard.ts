import { Injectable, computed, inject, signal } from '@angular/core';
import { Api, ApiError, SetupState } from './api';

export interface StepInfo {
  id: string;
  title: string;
  short: string;
}

export const STEPS: StepInfo[] = [
  { id: 'welcome', title: 'Welcome', short: 'Start' },
  { id: 'account', title: 'Your account', short: 'Account' },
  { id: 'profile', title: 'You and your manager', short: 'You' },
  { id: 'conversation', title: 'Your digest format', short: 'Format' },
  { id: 'repos', title: 'Your repositories', short: 'Repos' },
  { id: 'atlassian', title: 'Jira and Bitbucket', short: 'Atlassian' },
  { id: 'ai', title: 'AI for drafts', short: 'AI' },
  { id: 'hooks', title: 'Agent capture', short: 'Capture' },
  { id: 'start', title: 'Start WorkDigest', short: 'Start' },
  { id: 'phone', title: 'Your phone', short: 'Phone' },
];

/** Where the person is in setup, and what the Mac already has. */
@Injectable({ providedIn: 'root' })
export class Wizard {
  private readonly api = inject(Api);
  readonly state = signal<SetupState | null>(null);
  readonly index = signal(0);
  readonly step = computed(() => STEPS[this.index()]);
  readonly loadError = signal('');

  async load(): Promise<void> {
    try {
      this.state.set(await this.api.get<SetupState>('state'));
      this.loadError.set('');
    } catch (error) {
      this.loadError.set((error as Error).message);
    }
  }

  async next(): Promise<void> {
    await this.load();
    this.go(this.index() + 1);
  }

  back(): void {
    this.go(this.index() - 1);
  }

  go(index: number): void {
    this.index.set(Math.max(0, Math.min(STEPS.length - 1, index)));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}

/** Runs one action with a busy flag and a readable error. */
export class Action {
  readonly busy = signal(false);
  readonly error = signal('');

  async run<T>(work: () => Promise<T>): Promise<T | undefined> {
    this.busy.set(true);
    this.error.set('');
    try {
      return await work();
    } catch (error) {
      this.error.set(error instanceof ApiError ? error.message : `Unexpected problem: ${error}`);
      return undefined;
    } finally {
      this.busy.set(false);
    }
  }
}
