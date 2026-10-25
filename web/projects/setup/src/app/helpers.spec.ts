import { ApiError, takeToken } from './api';
import { qrDataUrl } from './steps/phone';
import { splitList } from './steps/profile';
import { Action } from './wizard';

describe('setup helpers', () => {
  it('takes the one-time token from the link and clears the address bar', () => {
    const replaced: string[] = [];
    const location = { hash: '#token=abc_DEF-1', pathname: '/' } as Location;
    const history = { replaceState: (_s: unknown, _t: string, url: string) => replaced.push(url) } as unknown as History;
    expect(takeToken(location, history)).toBe('abc_DEF-1');
    expect(replaced).toEqual(['/']);
    expect(takeToken({ hash: '', pathname: '/' } as Location, history)).toBe('');
  });

  it('splits lists typed with commas or new lines', () => {
    expect(splitList(' a@x.io, b@x.io\nc@x.io ,, ')).toEqual(['a@x.io', 'b@x.io', 'c@x.io']);
  });

  it('draws a QR code as an image', () => {
    expect(qrDataUrl('https://workdigest.example/download/android')).toMatch(/^data:image\/gif;base64,/);
  });

  it('shows API messages as they are and other failures as unexpected', async () => {
    const action = new Action();
    await action.run(() => Promise.reject(new ApiError('use your @ei.study email address')));
    expect(action.error()).toBe('use your @ei.study email address');
    expect(action.busy()).toBeFalse();
    expect(await action.run(() => Promise.resolve(42))).toBe(42);
    expect(action.error()).toBe('');
    await action.run(() => Promise.reject(new Error('boom')));
    expect(action.error()).toContain('Unexpected problem');
  });
});
