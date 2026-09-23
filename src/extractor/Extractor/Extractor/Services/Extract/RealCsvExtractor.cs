using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace Extractor.Services.Extract;

public static class RealCsvExtractor {
    private static readonly string[] RuMonths = {
        "янв.", "февр.", "март", "апр.", "май", "июнь", "июль", "авг.", "сент.", "окт.", "нояб.", "дек."
    };

    private static readonly Dictionary<string, string> MonthMap = BuildMonthMap();
    private static readonly Regex ArrivalDateRegex = new(@"поступление до (\d{2})\.(\d{2})\.(\d{4})");

    public static object Extract(byte[] content, string filename) {
        List<string[]> rows;
        try {
            var encoding = Encoding.GetEncoding(1251);
            var text = encoding.GetString(content);
            rows = text.Replace("\r\n", "\n").Split('\n')
                .Where(line => line.Length > 0)
                .Select(line => line.Split(';'))
                .ToList();
        } catch (Exception ex) {
            return new Dictionary<string, object> { ["error"] = $"не удалось прочитать файл '{filename}': {ex.Message}" };
        }

        if (rows.Count < 2) {
            return new Dictionary<string, object> { ["error"] = $"файл '{filename}' пуст или не содержит данных" };
        }

        var fileType = DetectFileType(filename);
        if (fileType is null) {
            return new Dictionary<string, object> { ["error"] = $"не удалось определить тип файла по имени '{filename}'" };
        }

        var supplier = DetectSupplier(filename) ?? DetectSupplierFromContent(rows);
        if (supplier is null) {
            return new Dictionary<string, object> { ["error"] = $"не удалось определить поставщика по имени файла '{filename}'" };
        }

        if (fileType is "seasonality" or "monthly_sales") {
            return new Dictionary<string, object> {
                ["supplier"] = supplier,
                ["file_type"] = fileType,
                ["items"] = new Dictionary<string, object>(),
            };
        }

        Dictionary<string, object> items;
        try {
            items = (supplier, fileType) switch {
                (_, "sales") => ParseSales(rows),
                (_, "stock") => ParseStock(rows),
                ("IEK", "transit") => ParseTransitIek(rows),
                (_, "transit") => ParseTransitSystemElectric(rows),
                ("IEK", "moq") => ParseMoq(rows, "Код 1с", "Мин. разр. к отгр."),
                (_, "moq") => ParseMoq(rows, "Номенклатура.Код", "Кратность"),
                _ => new Dictionary<string, object>(),
            };
        } catch (Exception ex) {
            return new Dictionary<string, object> { ["error"] = $"не удалось разобрать файл '{filename}': {ex.Message}" };
        }

        return new Dictionary<string, object> {
            ["supplier"] = supplier,
            ["file_type"] = fileType,
            ["items"] = items,
        };
    }

    private static string? DetectSupplier(string filename) {
        var lowered = filename.ToLowerInvariant();
        if (lowered.Contains("иэк") || lowered.Contains("iek")) return "IEK";
        if (lowered.Contains("systemelectric") || lowered.Contains("syseme") || lowered.Contains("system electric")) return "SystemElectric";
        return null;
    }

    private static string? DetectSupplierFromContent(List<string[]> rows) {
        var header = rows[0];
        var nameIdx = Array.IndexOf(header, "Номенклатура");
        if (nameIdx < 0) nameIdx = Array.IndexOf(header, "Наименование");
        if (nameIdx < 0) return null;

        var iekHits = 0;
        var seHits = 0;
        foreach (var row in rows.Skip(1).Take(500)) {
            if (nameIdx >= row.Length) continue;
            var name = row[nameIdx].ToLowerInvariant();
            if (name.Contains("iek")) iekHits++;
            if (name.Contains("atlas") || name.Contains("systeme") || name.Contains("schneider") || name.Contains("wessen")) seHits++;
        }
        if (iekHits == 0 && seHits == 0) return null;
        return iekHits >= seHits ? "IEK" : "SystemElectric";
    }

    private static string? DetectFileType(string filename) {
        var lowered = filename.ToLowerInvariant();
        if (lowered.Contains("сезонность")) return "seasonality";
        if (lowered.Contains("ежемесячные продажи")) return "monthly_sales";
        if (lowered.Contains("moq")) return "moq";
        if (lowered.Contains("динамика")) return "sales";
        if (lowered.Contains("остат")) return "stock";
        if (lowered.Contains("пут")) return "transit";
        return null;
    }

    private static double ParseNumber(string raw) {
        var s = raw.Trim().Replace(" ", "").Replace(" ", "").Replace(",", ".");
        if (s.Length == 0) return 0;
        return double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) ? value : 0;
    }

    private static Dictionary<string, string> BuildMonthMap() {
        var map = new Dictionary<string, string>();
        foreach (var year in new[] { 2024, 2025, 2026 }) {
            for (var m = 0; m < RuMonths.Length; m++) {
                map[$"{RuMonths[m]} {year}"] = $"{year}-{(m + 1):D2}";
            }
        }
        return map;
    }

    private static Dictionary<string, object> ParseSales(List<string[]> rows) {
        var header = rows[0];
        var dateIdx = Array.IndexOf(header, "Дата");
        var numberIdx = Array.IndexOf(header, "Номер");
        var codeIdx = Array.IndexOf(header, "Код");
        var nameIdx = Array.IndexOf(header, "Номенклатура");
        var qtyIdx = Array.IndexOf(header, "Количество");

        var result = new Dictionary<string, object>();
        foreach (var row in rows.Skip(1)) {
            if (codeIdx < 0 || codeIdx >= row.Length) continue;
            var sku = row[codeIdx].Trim();
            if (sku.Length == 0) continue;

            var dateRaw = row[dateIdx].Split(' ')[0];
            var parts = dateRaw.Split('.');
            if (parts.Length != 3) continue;
            var isoDate = $"{parts[2]}-{parts[1]}-{parts[0]}";

            if (!result.TryGetValue(sku, out var entryObj)) {
                entryObj = new Dictionary<string, object> {
                    ["name"] = nameIdx >= 0 && nameIdx < row.Length ? row[nameIdx].Trim() : "",
                    ["transactions"] = new List<object>(),
                };
                result[sku] = entryObj;
            }
            var transactions = (List<object>)((Dictionary<string, object>)entryObj)["transactions"];
            transactions.Add(new Dictionary<string, object> {
                ["date"] = isoDate,
                ["order_id"] = numberIdx >= 0 && numberIdx < row.Length ? row[numberIdx].Trim() : "",
                ["qty"] = Math.Abs(ParseNumber(row[qtyIdx])),
            });
        }
        return result;
    }

    private static Dictionary<string, object> ParseStock(List<string[]> rows) {
        var header = rows[0];
        var skuIdx = Array.IndexOf(header, "Номенклатура.Код");
        var monthCols = new List<(int Idx, string Iso)>();
        for (var i = 0; i < header.Length; i++) {
            if (MonthMap.TryGetValue(header[i].Trim(), out var iso)) {
                monthCols.Add((i, iso));
            }
        }

        var result = new Dictionary<string, object>();
        foreach (var row in rows.Skip(1)) {
            if (skuIdx < 0 || skuIdx >= row.Length) continue;
            var sku = row[skuIdx].Trim();
            if (sku.Length == 0) continue;

            var monthlyStock = new Dictionary<string, object>();
            foreach (var (idx, iso) in monthCols) {
                if (idx >= row.Length) continue;
                var cell = row[idx].Trim();
                if (cell.Length == 0) continue;
                monthlyStock[iso] = ParseNumber(cell);
            }
            result[sku] = new Dictionary<string, object> { ["monthly_stock"] = monthlyStock };
        }
        return result;
    }

    private static Dictionary<string, object> ParseTransitIek(List<string[]> rows) {
        var header = rows[0];
        var skuIdx = Array.IndexOf(header, "Код 1с");
        var idColumns = new HashSet<string> { "Код 1с", "Артикул ИЭК", " Наименование" };
        var shipmentCols = new List<(int Idx, string? Date)>();
        for (var i = 0; i < header.Length; i++) {
            if (idColumns.Contains(header[i])) continue;
            var match = ArrivalDateRegex.Match(header[i]);
            string? date = match.Success ? $"{match.Groups[3].Value}-{match.Groups[2].Value}-{match.Groups[1].Value}" : null;
            shipmentCols.Add((i, date));
        }

        var result = new Dictionary<string, object>();
        foreach (var row in rows.Skip(1)) {
            if (skuIdx < 0 || skuIdx >= row.Length) continue;
            var sku = row[skuIdx].Trim();
            if (sku.Length == 0) continue;

            var shipments = new List<object>();
            foreach (var (idx, date) in shipmentCols) {
                if (idx >= row.Length || date is null) continue;
                var qty = ParseNumber(row[idx]);
                if (qty <= 0) continue;
                shipments.Add(new Dictionary<string, object> { ["qty"] = qty, ["expected_date"] = date });
            }
            result[sku] = new Dictionary<string, object> { ["incoming_shipments"] = shipments };
        }
        return result;
    }

    private static Dictionary<string, object> ParseTransitSystemElectric(List<string[]> rows) {
        var headerRowIdx = 0;
        for (var i = 0; i < Math.Min(rows.Count, 5); i++) {
            if (rows[i].Any(cell => cell.Contains("Артикул"))) {
                headerRowIdx = i;
                break;
            }
        }
        var header = rows[headerRowIdx];
        var skuIdx = Array.IndexOf(header, "Код 1с");
        var categoryIdx = Array.IndexOf(header, "Категория 2026");
        var nameIdx = Array.IndexOf(header, "Наименование");
        var transitIdx = -1;
        for (var i = 0; i < header.Length; i++) {
            if (header[i].ToLowerInvariant().Contains("в пути")) {
                transitIdx = i;
                break;
            }
        }

        var result = new Dictionary<string, object>();
        var tomorrow = DateTime.UtcNow.AddDays(1).ToString("yyyy-MM-dd");
        foreach (var row in rows.Skip(headerRowIdx + 1)) {
            if (skuIdx < 0 || skuIdx >= row.Length) continue;
            var sku = row[skuIdx].Trim();
            if (sku.Length == 0) continue;

            var qty = transitIdx >= 0 && transitIdx < row.Length ? ParseNumber(row[transitIdx]) : 0;
            var shipments = new List<object>();
            if (qty > 0) {
                shipments.Add(new Dictionary<string, object> { ["qty"] = qty, ["expected_date"] = tomorrow });
            }

            var entry = new Dictionary<string, object> { ["incoming_shipments"] = shipments };
            if (categoryIdx >= 0 && categoryIdx < row.Length) entry["category"] = row[categoryIdx].Trim();
            if (nameIdx >= 0 && nameIdx < row.Length) entry["name"] = row[nameIdx].Trim();
            result[sku] = entry;
        }
        return result;
    }

    private static Dictionary<string, object> ParseMoq(List<string[]> rows, string skuCol, string qtyCol) {
        var header = rows[0];
        var skuIdx = Array.IndexOf(header, skuCol);
        var qtyIdx = Array.IndexOf(header, qtyCol);

        var result = new Dictionary<string, object>();
        foreach (var row in rows.Skip(1)) {
            if (skuIdx < 0 || skuIdx >= row.Length) continue;
            var sku = row[skuIdx].Trim();
            if (sku.Length == 0) continue;
            var qty = qtyIdx >= 0 && qtyIdx < row.Length ? ParseNumber(row[qtyIdx]) : 1;
            result[sku] = new Dictionary<string, object> { ["moq"] = qty <= 0 ? 1 : qty };
        }
        return result;
    }
}
