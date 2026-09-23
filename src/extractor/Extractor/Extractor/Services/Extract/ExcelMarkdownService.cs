using System.Text;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Spreadsheet;

namespace Extractor.Services.Extract;

public sealed class ExcelMarkdownService {
    private const int DefaultRowLimit = 10;

    public Task<string> ReadFirstRowsAsMarkdownAsync(
        Stream excelFile,
        int rowLimit = DefaultRowLimit,
        CancellationToken cancellationToken = default) {
        var table = ReadFirstRows(excelFile, rowLimit, cancellationToken);

        return Task.FromResult(ToMarkdown(table.RawRows));
    }

    public Task<ExcelTable> ReadTableAsync(
        Stream excelFile,
        int rowLimit = DefaultRowLimit,
        CancellationToken cancellationToken = default) {
        return Task.FromResult(ReadFirstRows(excelFile, rowLimit, cancellationToken));
    }

    private static ExcelTable ReadFirstRows(Stream excelFile, int rowLimit, CancellationToken cancellationToken) {
        if (rowLimit <= 0) {
            throw new ArgumentOutOfRangeException(nameof(rowLimit), "Row limit must be greater than zero.");
        }

        cancellationToken.ThrowIfCancellationRequested();

        using var document = SpreadsheetDocument.Open(excelFile, false);
        var workbookPart = document.WorkbookPart
            ?? throw new InvalidDataException("Excel workbook is missing workbook data.");
        var workbook = workbookPart.Workbook
            ?? throw new InvalidDataException("Excel workbook is missing workbook data.");
        var sheet = workbook.Sheets?.Elements<Sheet>().FirstOrDefault()
            ?? throw new InvalidDataException("Excel workbook does not contain worksheets.");
        var relationshipId = sheet.Id?.Value
            ?? throw new InvalidDataException("Worksheet relationship id is missing.");
        var worksheetPart = (WorksheetPart)workbookPart.GetPartById(relationshipId);
        var worksheet = worksheetPart.Worksheet
            ?? throw new InvalidDataException("Excel worksheet is missing worksheet data.");
        var sheetData = worksheet.GetFirstChild<SheetData>();
        var sharedStrings = ReadSharedStrings(workbookPart);

        if (sheetData is null) {
            return new ExcelTable(sheet.Name?.Value ?? "Sheet1", [], [], []);
        }

        var tableRows = new List<IReadOnlyList<string?>>();
        foreach (var row in sheetData.Elements<Row>()) {
            cancellationToken.ThrowIfCancellationRequested();

            var values = ReadRow(row, sharedStrings);
            if (values.Any(value => !string.IsNullOrWhiteSpace(value))) {
                tableRows.Add(values);
            }

            if (tableRows.Count >= rowLimit) {
                break;
            }
        }

        if (tableRows.Count == 0) {
            return new ExcelTable(sheet.Name?.Value ?? "Sheet1", [], [], []);
        }

        var headers = NormalizeHeaders(tableRows[0]);
        var rows = tableRows
            .Skip(1)
            .Select(row => ToDictionaryRow(headers, row))
            .ToList();

        return new ExcelTable(sheet.Name?.Value ?? "Sheet1", headers, rows, tableRows);
    }

    private static string ToMarkdown(IReadOnlyList<IReadOnlyList<string?>> rows) {
        if (rows.Count == 0) {
            return string.Empty;
        }

        var columnCount = rows.Max(row => row.Count);
        var builder = new StringBuilder();

        AppendMarkdownRow(builder, NormalizeMarkdownRow(rows[0], columnCount));
        AppendMarkdownRow(builder, Enumerable.Repeat("---", columnCount));

        foreach (var row in rows.Skip(1)) {
            AppendMarkdownRow(builder, NormalizeMarkdownRow(row, columnCount));
        }

        return builder.ToString().TrimEnd();
    }

    private static IEnumerable<string> NormalizeMarkdownRow(IReadOnlyList<string?> row, int columnCount) {
        for (var index = 0; index < columnCount; index++) {
            yield return index < row.Count ? EscapeMarkdownCell(row[index]) : string.Empty;
        }
    }

    private static void AppendMarkdownRow(StringBuilder builder, IEnumerable<string> cells) {
        builder
            .Append("| ")
            .AppendJoin(" | ", cells)
            .AppendLine(" |");
    }

    private static string EscapeMarkdownCell(string? value) {
        return (value ?? string.Empty)
            .Replace("\\", "\\\\", StringComparison.Ordinal)
            .Replace("|", "\\|", StringComparison.Ordinal)
            .Replace("\r", " ", StringComparison.Ordinal)
            .Replace("\n", " ", StringComparison.Ordinal)
            .Trim();
    }

    private static IReadOnlyList<string> ReadSharedStrings(WorkbookPart workbookPart) {
        return workbookPart
            .SharedStringTablePart?
            .SharedStringTable?
            .Elements<SharedStringItem>()
            .Select(item => item.InnerText)
            .ToList() ?? [];
    }

    private static List<string?> ReadRow(Row row, IReadOnlyList<string> sharedStrings) {
        var values = new List<string?>();

        foreach (var cell in row.Elements<Cell>()) {
            var columnIndex = GetColumnIndex(cell.CellReference?.Value);
            while (values.Count < columnIndex) {
                values.Add(null);
            }

            values.Add(ReadCellValue(cell, sharedStrings));
        }

        return values;
    }

    private static string? ReadCellValue(Cell cell, IReadOnlyList<string> sharedStrings) {
        if (cell.DataType?.Value == CellValues.InlineString) {
            return cell.InlineString?.Text?.Text;
        }

        var value = cell.CellValue?.Text;
        if (value is null) {
            return null;
        }

        if (cell.DataType?.Value == CellValues.SharedString && int.TryParse(value, out var sharedStringIndex)) {
            return sharedStringIndex >= 0 && sharedStringIndex < sharedStrings.Count
                ? sharedStrings[sharedStringIndex]
                : null;
        }

        if (cell.DataType?.Value == CellValues.Boolean) {
            return value == "1" ? "true" : "false";
        }

        return value;
    }

    private static int GetColumnIndex(string? cellReference) {
        if (string.IsNullOrWhiteSpace(cellReference)) {
            return 0;
        }

        var index = 0;
        foreach (var character in cellReference.TakeWhile(char.IsLetter)) {
            index = (index * 26) + char.ToUpperInvariant(character) - 'A' + 1;
        }

        return Math.Max(index - 1, 0);
    }

    private static IReadOnlyList<string> NormalizeHeaders(IReadOnlyList<string?> row) {
        var usedNames = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var headers = new List<string>(row.Count);

        for (var index = 0; index < row.Count; index++) {
            var baseName = string.IsNullOrWhiteSpace(row[index])
                ? $"Column{index + 1}"
                : row[index]!.Trim();

            if (!usedNames.TryAdd(baseName, 1)) {
                usedNames[baseName]++;
                baseName = $"{baseName}_{usedNames[baseName]}";
            }

            headers.Add(baseName);
        }

        return headers;
    }

    private static IReadOnlyDictionary<string, string?> ToDictionaryRow(IReadOnlyList<string> headers, IReadOnlyList<string?> row) {
        var result = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);

        for (var index = 0; index < headers.Count; index++) {
            result[headers[index]] = index < row.Count ? row[index] : null;
        }

        return result;
    }
}

public sealed record ExcelTable(
    string SheetName,
    IReadOnlyList<string> Headers,
    IReadOnlyList<IReadOnlyDictionary<string, string?>> Rows,
    IReadOnlyList<IReadOnlyList<string?>> RawRows);
