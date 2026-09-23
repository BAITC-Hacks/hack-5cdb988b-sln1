using System.Text.Json.Serialization;

namespace Extractor.Services.Extract;

public sealed class ExtractorService {
    private readonly ExcelMarkdownService _excelMarkdownService;
    private readonly OpenAiClient _openAiClient;

    public ExtractorService(ExcelMarkdownService excelMarkdownService, OpenAiClient openAiClient) {
        _excelMarkdownService = excelMarkdownService;
        _openAiClient = openAiClient;
    }

    public async Task<ExtractionResponse> ExtractAsync(Stream excelFile, CancellationToken cancellationToken = default) {
        var table = await ReadTableAsync(excelFile, cancellationToken);
        _ = await _openAiClient.AnalyzeFieldMappingAsync(table, cancellationToken);

        return CreateStubResponse();
    }

    public async Task<FieldMappingAnalysis> AnalyzeFieldMappingAsync(Stream excelFile, CancellationToken cancellationToken = default) {
        var table = await ReadTableAsync(excelFile, cancellationToken);

        return await _openAiClient.AnalyzeFieldMappingAsync(table, cancellationToken);
    }

    public Task<ExcelTable> ReadTableAsync(Stream excelFile, CancellationToken cancellationToken = default) =>
        _excelMarkdownService.ReadTableAsync(excelFile, cancellationToken: cancellationToken);

    public Task<string> ReadFirstRowsAsMarkdownAsync(Stream excelFile, CancellationToken cancellationToken = default) =>
        _excelMarkdownService.ReadFirstRowsAsMarkdownAsync(excelFile, cancellationToken: cancellationToken);

    private static ExtractionResponse CreateStubResponse() {
        return new ExtractionResponse(
            new DateOnly(2026, 9, 23),
            [
                new SupplierRecommendation(
                    "IEK",
                    [
                        new RecommendedItem(
                            "030200874_",
                            "Выкл 1-кл. прох. с инд. 10А BRITE графит",
                            "Выключатели",
                            45,
                            500,
                            120,
                            "critical",
                            "Ср. продажи 40 шт/мес, сезонный коэф. сентября ×1.2, дефицит в июле скорректирован (+15%), запас на 3 дня."),
                        new RecommendedItem(
                            "081100768_",
                            "Светильник аварийный ДПА 5030-1, NI-CD",
                            "Освещение",
                            300,
                            0,
                            0,
                            "planned",
                            "Остатка достаточно (запас на 4 месяца), заказ не требуется."),
                        new RecommendedItem(
                            "200400085_",
                            "LC1-C5E04-311 F/UTP кат.5Е 4 пары",
                            "Кабель",
                            80,
                            1000,
                            60,
                            "soon",
                            "Ср. продажи 150 м/мес, товар уже в пути (1000м), рекомендуем небольшой довоз под пиковый спрос.")
                    ]),
                new SupplierRecommendation(
                    "SystemElectric",
                    [
                        new RecommendedItem(
                            "300200445_",
                            "А409 Рамка 5-ная ATLAS алюминий",
                            "Розетки и выключатели",
                            12,
                            0,
                            200,
                            "critical",
                            "Разовый крупный заказ в марте (900 шт) исключён из расчёта регулярного спроса. Обычные продажи ~180 шт/мес, остаток критично низкий."),
                        new RecommendedItem(
                            "300200890_",
                            "Розетка ATLAS двойная с з/к",
                            "Розетки и выключатели",
                            150,
                            50,
                            0,
                            "planned",
                            "Остатка + товара в пути хватает на 3 месяца.")
                    ])
            ]);
    }
}

public sealed record ExtractionResponse(
    [property: JsonPropertyName("generated_at")] DateOnly GeneratedAt,
    [property: JsonPropertyName("suppliers")] IReadOnlyList<SupplierRecommendation> Suppliers);

public sealed record SupplierRecommendation(
    [property: JsonPropertyName("supplier")] string Supplier,
    [property: JsonPropertyName("items")] IReadOnlyList<RecommendedItem> Items);

public sealed record RecommendedItem(
    [property: JsonPropertyName("sku")] string Sku,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("category")] string Category,
    [property: JsonPropertyName("current_stock")] int CurrentStock,
    [property: JsonPropertyName("in_transit_qty")] int InTransitQty,
    [property: JsonPropertyName("recommended_qty")] int RecommendedQty,
    [property: JsonPropertyName("urgency")] string Urgency,
    [property: JsonPropertyName("reason")] string Reason);
