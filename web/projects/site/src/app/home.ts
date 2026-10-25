import { Component } from '@angular/core';
import { Faq } from './sections/faq';
import { Features } from './sections/features';
import { Hero } from './sections/hero';
import { How } from './sections/how';
import { Privacy } from './sections/privacy';
import { Start } from './sections/start';
import { Story } from './sections/story';

@Component({
  selector: 'wd-home',
  imports: [Hero, Story, Features, How, Privacy, Start, Faq],
  template: `
    <wd-hero />
    <wd-story />
    <wd-features />
    <wd-how />
    <wd-privacy />
    <wd-start />
    <wd-faq />
  `,
})
export class Home {}
