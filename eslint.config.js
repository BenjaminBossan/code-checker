export default [
  {
    files: ['**/*.js'],
    languageOptions: {
      ecmaVersion: 2021,
      sourceType: 'script',
      globals: {
        d3: 'readonly',
        document: 'readonly',
        window: 'readonly',
        FileReader: 'readonly',
        alert: 'readonly',
        Event: 'readonly'
      }
    },
    rules: {
      'no-unused-vars': 'error',
      'no-undef': 'error'
    }
  }
];
