// Shared by both apps' unit tests. CI runs Chrome without its sandbox (containers have none).
module.exports = function (config) {
  config.set({
    frameworks: ['jasmine'],
    plugins: [require('karma-jasmine'), require('karma-chrome-launcher'), require('karma-coverage')],
    reporters: ['progress'],
    browsers: ['ChromeHeadless'],
    customLaunchers: {
      ChromeHeadlessCI: { base: 'ChromeHeadless', flags: ['--no-sandbox', '--disable-dev-shm-usage'] },
    },
    singleRun: true,
    restartOnFileChange: false,
  });
};
