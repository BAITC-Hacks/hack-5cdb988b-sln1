import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { initDashboard } from '../dashboard.mjs';

const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
const fixture = JSON.parse(await readFile(new URL('../mock_recommendations.json', import.meta.url)));
const data = () => ({ ...structuredClone(fixture), errors: [] });
const ok = body => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
const file = name => new File(['sku;stock\n123;10'], name, { type: 'text/csv' });
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

function setup(t, fetchData = async () => ok(data())) {
  const dom = new JSDOM(html, { url: 'http://localhost/' });
  const { document, Event, MutationObserver } = dom.window;
  const $ = id => document.getElementById(id);
  const calls = [];
  let exported;
  dom.window.HTMLAnchorElement.prototype.click = function () {};
  initDashboard(document, {
    fetch: (...args) => { calls.push(args); return fetchData(...args); },
    url: { createObjectURL: blob => { exported = blob; return 'blob:test'; }, revokeObjectURL() {} },
  });
  t.after(() => dom.window.close());
  function select(files) {
    Object.defineProperty($('file-input'), 'files', { configurable: true, value: files });
    $('file-input').dispatchEvent(new Event('change', { bubbles: true }));
  }
  function drop(files) {
    const event = new Event('drop', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'dataTransfer', { value: { files } });
    $('dropzone').dispatchEvent(event);
    assert.equal(event.defaultPrevented, true);
  }
  function submit() {
    const completed = new Promise(resolve => {
      const observer = new MutationObserver(() => {
        if ($('dropzone').getAttribute('aria-busy') === 'false') {
          observer.disconnect();
          resolve();
        }
      });
      observer.observe($('dropzone'), { attributes: true, attributeFilter: ['aria-busy'] });
    });
    $('upload-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    return completed;
  }
  function input(id, value) {
    if (typeof value === 'boolean') $(id).checked = value;
    else $(id).value = value;
    $(id).dispatchEvent(new Event('input', { bubbles: true }));
  }
  return { $, document, calls, select, drop, submit, input, get exported() { return exported; } };
}

test('starts empty; selection displays all filenames without uploading', t => {
  const ui = setup(t);
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.$('item-count').textContent, '0');
  assert.equal(ui.$('export').disabled, true);
  assert.equal(ui.$('upload-button').disabled, true);
  assert.match(ui.$('groups').textContent, /Выберите исходные файлы/);
  ui.select([file('остатки.csv'), file('в пути.csv')]);
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.$('files').children.length, 2);
  assert.match(ui.$('files').textContent, /остатки.csv/);
  assert.match(ui.$('files').textContent, /в пути.csv/);
  assert.equal(ui.$('spinner').hidden, true);
  assert.equal(ui.$('upload-button').disabled, false);
  ui.$('files').querySelector('button').click();
  assert.equal(ui.$('files').children.length, 1);
  ui.$('files').querySelector('button').click();
  assert.equal(ui.$('upload-button').disabled, true);
  assert.equal(ui.document.activeElement, ui.$('file-input'));
});

test('one POST sends every selected file as repeated files fields and renders the response', async t => {
  const ui = setup(t);
  const files = [file('stock.csv'), file('transit.csv'), file('sales.csv')];
  ui.select(files);
  await ui.submit();
  assert.equal(ui.calls.length, 1);
  const [endpoint, options] = ui.calls[0];
  assert.equal(endpoint, '/api/upload');
  assert.equal(options.method, 'POST');
  assert.ok(options.body instanceof FormData);
  assert.deepEqual([...options.body.keys()], ['files', 'files', 'files']);
  assert.deepEqual(options.body.getAll('files').map(item => item.name), files.map(item => item.name));
  assert.deepEqual(await Promise.all(options.body.getAll('files').map(item => item.text())), await Promise.all(files.map(item => item.text())));
  assert.equal(options.headers, undefined, 'browser supplies the multipart boundary');
  assert.equal(ui.$('supplier-count').textContent, String(fixture.suppliers.length));
  assert.equal(ui.document.querySelectorAll('.supplier-group').length, fixture.suppliers.length);
  assert.equal(ui.$('supplier').options.length, fixture.suppliers.length + 1);
  assert.equal(ui.$('item-count').textContent, String(fixture.suppliers.flatMap(s => s.items).length));
  assert.equal(ui.$('status').dataset.state, 'success');
  assert.equal(ui.$('spinner').hidden, true);
});

test('drop replaces selection; loader covers fetch and body parsing; duplicate submits and changes are blocked', async t => {
  const request = deferred();
  const body = deferred();
  const ui = setup(t, () => request.promise);
  ui.select([file('old.csv')]);
  ui.drop([file('stock.csv'), file('sales.csv')]);
  assert.doesNotMatch(ui.$('files').textContent, /old.csv/);
  const finished = ui.submit();
  assert.equal(ui.$('spinner').hidden, false);
  assert.equal(ui.$('dropzone').getAttribute('aria-busy'), 'true');
  assert.equal(ui.$('file-input').disabled, true);
  assert.equal(ui.$('upload-button').disabled, true);
  assert.equal(ui.$('files').querySelector('button').disabled, true);
  const duplicate = ui.submit();
  ui.drop([file('ignored.csv')]);
  assert.equal(ui.calls.length, 1);
  assert.doesNotMatch(ui.$('files').textContent, /ignored.csv/);
  request.resolve({ ok: true, json: () => body.promise });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.$('spinner').hidden, false, 'still loading until JSON is parsed');
  body.resolve(data());
  await Promise.all([finished, duplicate]);
  assert.equal(ui.$('spinner').hidden, true);
  assert.equal(ui.$('file-input').disabled, false);
  assert.equal(ui.$('upload-button').disabled, false);
});

test('new calculation replaces suppliers, clears approval and resets supplier/approved-only filters', async t => {
  const next = data();
  next.suppliers = [{ ...next.suppliers[0], supplier: 'Новый поставщик' }];
  let count = 0;
  const ui = setup(t, async () => ok(count++ ? next : data()));
  ui.select([file('stock.csv')]);
  await ui.submit();
  ui.document.querySelector('tbody input').click();
  assert.equal(ui.$('approved-count').textContent, '1');
  ui.input('approved-only', true);
  ui.input('supplier', fixture.suppliers[0].supplier);
  await ui.submit();
  assert.equal(ui.$('approved-count').textContent, '0');
  assert.equal(ui.$('approved-only').checked, false);
  assert.equal(ui.$('supplier').value, '');
  assert.deepEqual([...ui.$('supplier').options].map(o => o.value), ['', 'Новый поставщик']);
  assert.equal(ui.document.querySelector('.supplier-heading h2').textContent, 'Новый поставщик');
  assert.equal(ui.document.querySelector('tbody input').checked, false);
});

test('API 501 detail is visible and files remain available for retry', async t => {
  let attempts = 0;
  const ui = setup(t, async () => ++attempts === 1 ? new Response(JSON.stringify({ detail: 'Парсер файлов ещё не подключён.' }), { status: 501 }) : ok(data()));
  ui.select([file('stock.csv')]);
  await ui.submit();
  assert.equal(ui.$('api-error').hidden, false);
  assert.match(ui.$('api-error').textContent, /HTTP 501.*Парсер файлов ещё не подключён/);
  assert.equal(ui.$('status').dataset.state, 'error');
  assert.equal(ui.$('spinner').hidden, true);
  assert.equal(ui.$('upload-button').disabled, false);
  assert.equal(ui.$('files').children.length, 1);
  await ui.submit();
  assert.equal(ui.$('api-error').hidden, true);
  assert.equal(ui.$('status').dataset.state, 'success');
});

test('partial errors render safely alongside successful results and clear on the next success', async t => {
  const partial = data();
  partial.errors = [{ supplier: 'Поставщик <script>', error: 'Не хватает файла «Товар в пути» <img src=x>' }];
  let count = 0;
  const ui = setup(t, async () => ok(count++ ? data() : partial));
  ui.select([file('stock.csv')]);
  await ui.submit();
  assert.equal(ui.$('partial-errors').hidden, false);
  assert.equal(ui.$('status').dataset.state, 'warning');
  assert.match(ui.$('error-list').textContent, /Поставщик <script>: Не хватает файла/);
  assert.equal(ui.$('error-list').querySelector('script, img'), null);
  assert.equal(ui.document.querySelectorAll('.supplier-group').length, partial.suppliers.length);
  assert.equal(ui.$('export').disabled, false);
  await ui.submit();
  assert.equal(ui.$('partial-errors').hidden, true);
  assert.equal(ui.$('error-list').children.length, 0);
});

test('an all-errors response clears obsolete results and exposes all errors', async t => {
  let count = 0;
  const ui = setup(t, async () => ok(count++ ? { generated_at: fixture.generated_at, suppliers: [], errors: [{ supplier: 'IEK', error: 'Недостаточно данных' }] } : data()));
  ui.select([file('stock.csv')]);
  await ui.submit();
  await ui.submit();
  assert.equal(ui.$('supplier-count').textContent, '0');
  assert.equal(ui.$('partial-errors').hidden, false);
  assert.equal(ui.$('export').disabled, true);
  assert.equal(ui.document.querySelectorAll('tbody tr').length, 0);
});

for (const [name, response, message] of [
  ['non-JSON HTTP failure', () => new Response('<html>Unavailable</html>', { status: 503 }), /HTTP 503/],
  ['network failure', () => { throw new TypeError('Failed to fetch'); }, /Failed to fetch/],
  ['invalid JSON', () => new Response('not json'), /Не удалось выполнить расчёт/],
  ['invalid recommendations', () => ok({ generated_at: fixture.generated_at, suppliers: 'invalid' }), /suppliers/],
  ['invalid partial errors', () => ok({ ...data(), errors: ['invalid'] }), /errors/],
  ['API validation error', () => new Response(JSON.stringify({ detail: [{ msg: 'Field required' }] }), { status: 422 }), /HTTP 422.*Field required/],
]) {
  test(`${name} shows an error, stops loading and preserves previous results`, async t => {
    let count = 0;
    const ui = setup(t, async () => count++ ? response() : ok(data()));
    ui.select([file('stock.csv')]);
    await ui.submit();
    ui.document.querySelector('tbody input').click();
    await ui.submit();
    assert.equal(ui.$('api-error').hidden, false);
    assert.match(ui.$('api-error').textContent, message);
    assert.match(ui.$('api-error').textContent, /предыдущего расчёта/);
    assert.equal(ui.$('spinner').hidden, true);
    assert.equal(ui.$('upload-button').disabled, false);
    assert.equal(ui.$('supplier-count').textContent, String(fixture.suppliers.length));
    assert.equal(ui.$('approved-count').textContent, '1');
  });
}

test('search, urgency, supplier groups, visible approval, approved-only, explanations and CSV still work', async t => {
  const ui = setup(t);
  ui.select([file('stock.csv')]);
  await ui.submit();
  const supplier = fixture.suppliers[0];
  const item = supplier.items[0];
  ui.input('search', item.sku);
  ui.input('supplier', supplier.supplier);
  ui.input('urgency', item.urgency);
  assert.equal(ui.document.querySelectorAll('tbody tr').length, 1);
  assert.equal(ui.document.querySelector('details p').textContent, item.reason);
  ui.document.querySelector('.supplier-heading button').click();
  assert.equal(ui.$('approved-count').textContent, '1');
  ui.input('approved-only', true);
  assert.equal(ui.document.querySelectorAll('tbody tr').length, 1);
  ui.$('export').click();
  const csv = await ui.exported.text();
  assert.equal(csv.split('\r\n').length, 2);
  assert.ok(csv.includes(item.sku));
  assert.ok(csv.endsWith('"true"'));
  ui.$('reset').click();
  assert.equal(ui.document.querySelectorAll('tbody tr').length, fixture.suppliers.flatMap(s => s.items).length);
  assert.equal(ui.$('approved-only').checked, false);
  const approved = [...ui.document.querySelectorAll('tbody input')].find(input => input.checked);
  approved.click();
  assert.equal(ui.$('approved-count').textContent, '0');
});
