import { Injectable, signal } from '@angular/core';

/** What the Mac already knows (no secret values). */
export interface SetupState {
  team: { domain: string; site_url: string; apk_url: string };
  account: { email: string; signed_in: boolean };
  profile: { name: string; emails: string[]; manager_name: string; team_members: string[] };
  conversation: { learned: boolean };
  repos: string[];
  atlassian: {
    email: string;
    jira_token: boolean;
    bitbucket_token: boolean;
    jira_site: string;
    bitbucket_workspace: string;
  };
  ai: { provider: string };
  hooks: { claude: boolean; cursor: boolean };
  service: { installed: boolean; running: boolean };
}

export interface Preview {
  manager_name: string;
  greeting: string;
  header_fields: string[];
  signoff: string;
  sections: { key: string; title: string; inline: boolean; custom: boolean }[];
  digests: number;
  replies: number;
  notes: string[];
}

export interface Check {
  ok: boolean;
  detail: string;
}

export interface AiState {
  label: string;
  state: 'ok' | 'missing' | 'logged_out' | 'failing';
  detail: string;
  login: string;
}

/** A step failed; ``message`` is written for the person using the page. */
export class ApiError extends Error {}

/** Reads the one-time token from the link (#token=…) and removes it from the address bar. */
export function takeToken(location: Location, history: History): string {
  const match = /token=([A-Za-z0-9_-]+)/.exec(location.hash);
  if (match) {
    history.replaceState(null, '', location.pathname);
    return match[1];
  }
  return '';
}

/** The local setup server's API (only reachable from this Mac). */
@Injectable({ providedIn: 'root' })
export class Api {
  private token = '';
  readonly expired = signal(false);

  constructor() {
    this.token = takeToken(window.location, window.history) || sessionStorage.getItem('wd-setup') || '';
    // A reload keeps working in this tab; the token dies with the server anyway.
    if (this.token) sessionStorage.setItem('wd-setup', this.token);
  }

  get<T>(path: string): Promise<T> {
    return this.call<T>('GET', path);
  }

  post<T>(path: string, body: object = {}): Promise<T> {
    return this.call<T>('POST', path, body);
  }

  private async call<T>(method: string, path: string, body?: object): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`/api/${path}`, {
        method,
        headers: {
          'X-Setup-Token': this.token,
          ...(body ? { 'Content-Type': 'application/json' } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch {
      throw new ApiError('The setup window in Terminal was closed. Run `digest setup` again.');
    }
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) this.expired.set(true);
    if (!response.ok) throw new ApiError(data.error ?? `Something went wrong (${response.status}).`);
    return data as T;
  }
}
