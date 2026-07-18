/**
 * Меню «⚙ Магазин» в таблице «Товари Чернівці».
 * Кнопки дергают GitHub Actions (workflow_dispatch) репозитория с конвейером.
 *
 * Установка (однократно):
 *  1. Расширения → Apps Script → вставить этот файл.
 *  2. Настройки проекта → Свойства скрипта → добавить GITHUB_TOKEN
 *     (fine-grained PAT, доступ только к репо chernivtsi-feed, право Actions: Read and write).
 *  3. Обновить страницу таблицы — появится меню «⚙ Магазин».
 */
const GH_OWNER = 'Ilya330';
const GH_REPO  = 'chernivtsi-feed';

function onOpen() {
  SpreadsheetApp.getUi().createMenu('⚙ Магазин')
    .addItem('1. Обробити прайс (синхронізація)', 'runSync')
    .addItem('2. Оновити XML-фід', 'runFeed')
    .addItem('Синхронізація + фід', 'runBoth')
    .addSeparator()
    .addItem('Пробний прогін (dry-run, без запису)', 'runDry')
    .addToUi();
}

function dispatch_(action, dryRun) {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) {
    SpreadsheetApp.getUi().alert('Не задан GITHUB_TOKEN у Властивостях скрипта.');
    return;
  }
  const resp = UrlFetchApp.fetch(
    `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/actions/workflows/pipeline.yml/dispatches`,
    {
      method: 'post',
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
      contentType: 'application/json',
      muteHttpExceptions: true,
      payload: JSON.stringify({
        ref: 'main',
        inputs: { action: action, dry_run: dryRun ? 'true' : 'false' },
      }),
    });
  const code = resp.getResponseCode();
  if (code === 204) {
    SpreadsheetApp.getActive().toast('Запущено. Результат — у листі «Лог» через 1–2 хвилини.');
  } else {
    SpreadsheetApp.getUi().alert('Помилка запуску (' + code + '): ' + resp.getContentText());
  }
}

function runSync() { dispatch_('sync', false); }
function runFeed() { dispatch_('feed', false); }
function runBoth() { dispatch_('both', false); }
function runDry()  { dispatch_('sync', true); }
