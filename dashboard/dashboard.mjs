import { urgencyLabels, rowKey, validateData, filterGroups, toCsv } from './recommendations.mjs';
export function initDashboard(document, { fetch: fetchData = globalThis.fetch, url = globalThis.URL } = {}) {
const $ = id => document.getElementById(id);
let suppliers = [];
let selectedFiles = [];
let loading = false;
let hasResults = false;
const approved = new Set();
const number = n => n.toLocaleString('ru-RU');
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function visibleGroups() {
  const groups = filterGroups(suppliers, { search: $('search').value, supplier: $('supplier').value, urgency: $('urgency').value });
  return $('approved-only').checked ? groups.map(s => ({ ...s, items: s.items.filter(r => approved.has(rowKey(s,r))) })).filter(s => s.items.length) : groups;
}
function render() {
  const groups = visibleGroups();
  const all = suppliers.flatMap(s => s.items);
  $('supplier-count').textContent = number(suppliers.length);
  $('item-count').textContent = number(all.length);
  $('critical-count').textContent = number(all.filter(r => r.urgency === 'critical').length);
  $('approved-count').textContent = number(approved.size);
  const count = groups.reduce((sum,s) => sum + s.items.length,0);
  $('results').textContent = `Показано позиций: ${count} из ${all.length}`;
  $('export').disabled = count === 0;
  $('groups').replaceChildren();
  if (!count) {
    const empty = element('div', undefined, 'empty');
    empty.append(element('span', hasResults ? '◎' : '↥', 'empty-icon'),
      element('h3', hasResults ? 'Нет подходящих рекомендаций' : 'Ваш следующий заказ начинается здесь'),
      element('p', hasResults ? 'Если расчёт содержит позиции, измените фильтры или сбросьте их. Подробности ошибок — выше.' : 'Выберите исходные файлы и нажмите «Загрузить и рассчитать». Здесь появятся рекомендации по поставщикам.'));
    $('groups').append(empty);
  }
  for (const s of groups) {
    const section = element('section',undefined,'supplier-group');
    const heading = element('div',undefined,'supplier-heading');
    const title = element('div');
    title.append(element('h2',s.supplier),element('span',`Позиций: ${s.items.length}`));
    const approveAll = element('button','Подтвердить видимые','secondary');
    approveAll.disabled = s.items.every(r => approved.has(rowKey(s,r)));
    approveAll.addEventListener('click',() => { s.items.forEach(r => approved.add(rowKey(s,r))); render(); });
    heading.append(title,approveAll);
    const scroll = element('div',undefined,'table-scroll');
    scroll.tabIndex = 0;
    scroll.setAttribute('role', 'region');
    scroll.setAttribute('aria-label', `Таблица поставщика ${s.supplier}, прокрутка по горизонтали`);
    const table = element('table');
    const caption = element('caption',`Рекомендации поставщика ${s.supplier}`,'sr-only');
    const head = element('thead'); const hr = element('tr');
    for (const label of ['Артикул','Наименование','Категория','Остаток','В пути','К заказу','Срочность','Причина','Решение']) { const th = element('th',label); th.scope = 'col'; hr.append(th); }
    head.append(hr); const body = element('tbody');
    for (const r of s.items) {
      const tr = element('tr'); const product = element('td');
      tr.append(element('td',r.sku,'sku')); product.append(element('strong',r.name));
      const details = element('details'); details.append(element('summary','Почему такой заказ?'),element('p',r.reason)); tr.append(product,element('td',r.category));
      for (const field of ['current_stock','in_transit_qty','recommended_qty']) tr.append(element('td',number(r[field]),field === 'recommended_qty' ? 'quantity' : 'numeric'));
      const urgency = element('td'); urgency.append(element('span',urgencyLabels[r.urgency],`badge ${r.urgency}`)); tr.append(urgency); const reason = element('td'); reason.append(details); tr.append(reason);
      const action = element('td'); const label = element('label',undefined,'check-label'); const checkbox = element('input'); checkbox.type = 'checkbox'; checkbox.checked = approved.has(rowKey(s,r)); checkbox.setAttribute('aria-label',`Подтвердить ${r.name}, ${s.supplier}`);
      checkbox.addEventListener('change',() => { const key = rowKey(s,r); checkbox.checked ? approved.add(key) : approved.delete(key); render(); });
      label.append(checkbox,element('span',checkbox.checked ? 'Подтверждено' : 'Подтвердить')); action.append(label); tr.append(action); body.append(tr);
    }
    table.append(caption,head,body); scroll.append(table); section.append(heading,scroll); $('groups').append(section);
  }
}
function setStatus(message, state = 'idle') {
  $('status').textContent = message;
  $('status').dataset.state = state;
}
function renderFiles() {
  $('files').replaceChildren();
  selectedFiles.forEach((file, index) => {
    const item = element('li', undefined, 'file-item');
    const info = element('div');
    info.append(element('strong', file.name), element('span', `${number(Math.ceil(file.size / 1024))} КБ`));
    const remove = element('button', '×', 'remove-file');
    remove.type = 'button';
    remove.disabled = loading;
    remove.setAttribute('aria-label', `Убрать файл ${file.name}`);
    remove.addEventListener('click', () => {
      selectedFiles.splice(index, 1);
      $('file-input').value = '';
      renderFiles();
      setStatus(selectedFiles.length ? `Выбрано файлов: ${selectedFiles.length}. Можно запускать расчёт.` : 'Выберите файлы для расчёта.');
      const next = $('files').querySelectorAll('button');
      (next[Math.min(index, next.length - 1)] ?? $('file-input')).focus();
    });
    item.append(info, remove);
    $('files').append(item);
  });
  $('file-count').textContent = selectedFiles.length ? `Выбрано: ${selectedFiles.length}` : 'Файлы ещё не выбраны';
  $('upload-button').disabled = loading || !selectedFiles.length;
}
function setLoading(value) {
  loading = value;
  $('dropzone').setAttribute('aria-busy', String(value));
  $('file-input').disabled = value;
  $('spinner').hidden = !value;
  $('upload-label').textContent = value ? 'Выполняем расчёт…' : 'Загрузить и рассчитать';
  renderFiles();
}
async function upload() {
  if (loading || !selectedFiles.length) return;
  setLoading(true);
  setStatus('Файлы отправляются и обрабатываются. Дождитесь завершения расчёта.', 'loading');
  $('api-error').hidden = true;
  try {
    const formData = new FormData();
    for (const file of selectedFiles) formData.append('files', file);
    const response = await fetchData('/api/upload', { method: 'POST', body: formData });
    if (!response.ok) {
      let detail = '';
      try {
        const body = await response.json();
        if (typeof body.detail === 'string') detail = body.detail;
        else if (Array.isArray(body.detail)) detail = body.detail.map(item => item.msg).filter(Boolean).join('; ');
      } catch { /* A proxy may return HTML or an empty response. */ }
      throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : '. Сервер не смог выполнить расчёт.'}`);
    }
    const data = validateData(await response.json());
    suppliers = data.suppliers;
    approved.clear();
    hasResults = true;
    $('approved-only').checked = false;
    const option = (label, value) => { const node = element('option', label); node.value = value; return node; };
    $('supplier').replaceChildren(option('Все поставщики', ''), ...suppliers.map(s => option(s.supplier, s.supplier)));
    $('supplier').value = '';
    $('calculated-at').textContent = `Последний расчёт: ${data.generated_at}`;
    const errors = data.errors ?? [];
    $('partial-errors').hidden = errors.length === 0;
    $('error-list').replaceChildren(...errors.map(error => element('li', `${error.supplier}: ${error.error}`)));
    setStatus(errors.length ? `Расчёт завершён с ошибками: ${errors.length}. Успешные результаты доступны ниже.` : 'Расчёт готов. Проверьте рекомендации и подтвердите нужные позиции.', errors.length ? 'warning' : 'success');
    render();
  } catch (error) {
    setStatus('Расчёт не выполнен. Можно повторить отправку выбранных файлов.', 'error');
    $('api-error').textContent = `Не удалось выполнить расчёт: ${error.message}${hasResults ? ' Ниже сохранены результаты предыдущего расчёта.' : ''}`;
    $('api-error').hidden = false;
  } finally {
    setLoading(false);
  }
}
for (const id of ['search','supplier','urgency','approved-only']) $(id).addEventListener('input',render);
$('reset').addEventListener('click',() => { for (const id of ['search','supplier','urgency']) $(id).value = ''; $('approved-only').checked = false; render(); });
$('upload-form').addEventListener('submit', event => { event.preventDefault(); upload(); });
$('export').addEventListener('click',() => {
  const href = url.createObjectURL(new Blob([toCsv(visibleGroups(),approved)], { type: 'text/csv;charset=utf-8;' }));
  const link = element('a'); link.href = href; link.download = 'recommendations.csv'; document.body.append(link); link.click(); link.remove();
  // Give the browser time to start the download before releasing its URL.
  setTimeout(() => url.revokeObjectURL(href),1000);
});
function acceptFiles(files) {
  if (loading || !files.length) return;
  selectedFiles = Array.from(files);
  renderFiles();
  setStatus(`Выбрано файлов: ${selectedFiles.length}. Можно запускать расчёт.`);
}
$('file-input').addEventListener('change',event => acceptFiles(event.target.files));
for (const name of ['dragenter','dragover']) $('dropzone').addEventListener(name,event => { event.preventDefault(); if (!loading) $('dropzone').classList.add('dragging'); });
$('dropzone').addEventListener('dragleave',event => { if (!$('dropzone').contains(event.relatedTarget)) $('dropzone').classList.remove('dragging'); });
$('dropzone').addEventListener('drop',event => { event.preventDefault(); $('dropzone').classList.remove('dragging'); acceptFiles(event.dataTransfer.files); });
renderFiles();
render();
}

if (typeof document !== 'undefined') initDashboard(document);
