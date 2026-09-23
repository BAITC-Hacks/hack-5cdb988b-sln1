import { urgencyLabels, rowKey, validateData, filterGroups, toCsv } from './recommendations.mjs';
const $ = id => document.getElementById(id);
let suppliers = [];
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
  if (!count) $('groups').append(element('p','Нет подходящих рекомендаций. Измените фильтры или сбросьте их.','empty'));
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
async function load() {
  $('retry').hidden = true; $('status').textContent = 'Загружаем моковые рекомендации…';
  try {
    const response = await fetch('./mock_recommendations.json');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = validateData(await response.json());
    suppliers = data.suppliers;
    $('supplier').replaceChildren(new Option('Все поставщики',''),...suppliers.map(s => new Option(s.supplier,s.supplier)));
    $('status').textContent = `Моковые рекомендации загружены · ${data.generated_at}. Данные демонстрационные.`; render();
  } catch (error) { $('status').textContent = `Не удалось загрузить рекомендации: ${error.message}`; $('retry').hidden = false; $('results').textContent = 'Данные недоступны'; }
}
for (const id of ['search','supplier','urgency','approved-only']) $(id).addEventListener('input',render);
$('reset').addEventListener('click',() => { for (const id of ['search','supplier','urgency']) $(id).value = ''; $('approved-only').checked = false; render(); });
$('retry').addEventListener('click',load);
$('export').addEventListener('click',() => {
  const url = URL.createObjectURL(new Blob([toCsv(visibleGroups(),approved)], { type: 'text/csv;charset=utf-8;' }));
  const link = element('a'); link.href = url; link.download = 'recommendations.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
});
let uploadTimer;
function acceptFiles(files) {
  if (!files.length) return;
  clearTimeout(uploadTimer);
  $('status').textContent = 'Обработка… Проверяем CSV-файлы (демо).';
  $('dropzone').setAttribute('aria-busy','true');
  $('files').replaceChildren();
  for (const file of files) {
    const valid = /\.csv$/i.test(file.name) && file.size > 0 && file.size <= 10 * 1024 * 1024;
    $('files').append(element('p',`${file.name} — ${valid ? 'принят для демо; не обработан' : 'ошибка: нужен непустой CSV размером до 10 МБ'}`,valid ? 'file-ok' : 'file-error'));
  }
  uploadTimer = setTimeout(() => {
    $('dropzone').setAttribute('aria-busy','false');
    $('status').textContent = 'Демонстрационный пример ошибки: Не хватает файла \"Товар в пути\" для IEK. Расчёт не выполнялся; на экране моковые рекомендации.';
  }, 700);
}
$('file-input').addEventListener('change',event => acceptFiles(event.target.files));
for (const name of ['dragenter','dragover']) $('dropzone').addEventListener(name,event => { event.preventDefault(); $('dropzone').classList.add('dragging'); });
$('dropzone').addEventListener('dragleave',() => $('dropzone').classList.remove('dragging'));
$('dropzone').addEventListener('drop',event => { event.preventDefault(); $('dropzone').classList.remove('dragging'); acceptFiles(event.dataTransfer.files); });
load();
