import { Routes } from '@angular/router';
import { Home } from './home';

export const routes: Routes = [
  { path: '', component: Home, title: 'WorkDigest · Your daily digest, drafted from evidence' },
  { path: '**', redirectTo: '' },
];
