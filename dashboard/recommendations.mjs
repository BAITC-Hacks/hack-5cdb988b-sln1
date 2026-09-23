export const urgencyLabels = { critical: 'Критично', soon: 'Скоро', planned: 'Планово' };
export const rowKey = (supplier, item) => JSON.stringify([supplier.supplier, item.sku]);
export function validateData(data) {
  if (!data || !Array.isArray(data.suppliers)) throw new Error('Ожидался список suppliers.');
  if (typeof data.generated_at !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(data.generated_at)) throw new Error('Некорректный generated_at.');
  if (data.errors !== undefined && (!Array.isArray(data.errors) || data.errors.some(error => !error || typeof error.supplier !== 'string' || typeof error.error !== 'string'))) throw new Error('Некорректный список errors.');
  const ids = new Set();
  for (const s of data.suppliers) {
    if (typeof s.supplier !== 'string' || !s.supplier || ids.has(s.supplier) || !Array.isArray(s.items)) throw new Error('Некорректный поставщик.');
    ids.add(s.supplier);
    const skus = new Set();
    for (const r of s.items) {
      if (['sku','name','category','reason'].some(k => typeof r[k] !== 'string' || !r[k]) || skus.has(r.sku) || !Object.hasOwn(urgencyLabels, r.urgency) || ['current_stock','in_transit_qty','recommended_qty'].some(k => typeof r[k] !== 'number' || !Number.isFinite(r[k]) || r[k] < 0)) throw new Error(`Некорректная рекомендация: ${s.supplier}.`);
      skus.add(r.sku);
    }
  }
  return data;
}
export function filterGroups(groups, { search = '', supplier = '', urgency = '' } = {}) {
  const query = search.trim().toLocaleLowerCase('ru');
  return groups.filter(s => !supplier || s.supplier === supplier).map(s => ({ ...s, items: s.items.filter(r => (!urgency || r.urgency === urgency) && `${r.sku} ${r.name}`.toLocaleLowerCase('ru').includes(query)) })).filter(s => s.items.length);
}
export function toCsv(groups, approved) {
  const fields = ['sku','name','category','current_stock','in_transit_qty','recommended_qty','urgency','reason'];
  const cell = value => `"${String(value).replace(/^[=+@\-\t\r\n]/, "'$&").replaceAll('"', '""')}"`;
  const rows = [['supplier',...fields,'approved']];
  for (const s of groups) for (const r of s.items) rows.push([s.supplier,...fields.map(f => r[f]),approved.has(rowKey(s,r))]);
  return '\uFEFF' + rows.map(r => r.map(cell).join(';')).join('\r\n');
}
