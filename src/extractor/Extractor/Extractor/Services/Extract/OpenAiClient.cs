using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using OpenAI;
using OpenAI.Chat;

namespace Extractor.Services.Extract;

public sealed class OpenAiClient {
    private const int PreviewRowCount = 5;
    private const int DefaultTimeoutSeconds = 30;

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly IConfiguration _configuration;

    public OpenAiClient(IConfiguration configuration) {
        _configuration = configuration;
    }

    public async Task<FieldMappingAnalysis> AnalyzeFieldMappingAsync(ExcelTable table, CancellationToken cancellationToken = default) {
        var apiKey = _configuration["OpenAI:ApiKey"] ?? Environment.GetEnvironmentVariable("OPENAI_API_KEY");
        if (string.IsNullOrWhiteSpace(apiKey)) {
            throw new InvalidOperationException("OpenAI API key is not configured. Set OPENAI_API_KEY or OpenAI:ApiKey.");
        }

        var model = _configuration["OpenAI:Model"] ?? "gpt-4.1-mini";
        var timeoutSeconds = _configuration.GetValue("OpenAI:TimeoutSeconds", DefaultTimeoutSeconds);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(timeoutSeconds));

        var client = new OpenAIClient(apiKey).GetChatClient(model);
        var completion = await client.CompleteChatAsync(
            [
                new SystemChatMessage("""
                    You map supplier Excel columns to a canonical procurement template.
                    Return only JSON that matches the provided schema.
                    Use null for source_column when no uploaded column matches a canonical field.
                    """),
                new UserChatMessage(BuildPrompt(table))
            ],
            new ChatCompletionOptions {
                MaxOutputTokenCount = 1200,
                ResponseFormat = ChatResponseFormat.CreateJsonSchemaFormat(
                    jsonSchemaFormatName: "field_mapping",
                    jsonSchema: BinaryData.FromBytes(Encoding.UTF8.GetBytes(FieldMappingJsonSchema)),
                    jsonSchemaIsStrict: true)
            },
            timeout.Token);

        var json = completion.Value.Content[0].Text;
        return JsonSerializer.Deserialize<FieldMappingAnalysis>(json, JsonOptions)
            ?? throw new InvalidDataException("OpenAI returned an empty field mapping response.");
    }

    private static string BuildPrompt(ExcelTable table) {
        var preview = new {
            sheet = table.SheetName,
            headers = table.Headers,
            rows = table.Rows.Take(PreviewRowCount)
        };

        return $"""
            Canonical template fields:
            - supplier: supplier or brand/manufacturer name
            - sku: product SKU, article, code, barcode, or vendor item number
            - name: product title or description
            - category: product group/category
            - current_stock: current available stock quantity
            - in_transit_qty: incoming/in-transit purchase quantity
            - sales_qty: sales or demand quantity
            - sales_period: period/date/month related to sales quantity

            Uploaded Excel preview:
            {JsonSerializer.Serialize(preview, JsonOptions)}
            """;
    }

    private const string FieldMappingJsonSchema = """
        {
          "type": "object",
          "properties": {
            "source_sheet": { "type": "string" },
            "fields": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "canonical_field": { "type": "string" },
                  "source_column": { "type": ["string", "null"] },
                  "confidence": { "type": "number" },
                  "reason": { "type": "string" }
                },
                "required": ["canonical_field", "source_column", "confidence", "reason"],
                "additionalProperties": false
              }
            },
            "unknown_columns": {
              "type": "array",
              "items": { "type": "string" }
            }
          },
          "required": ["source_sheet", "fields", "unknown_columns"],
          "additionalProperties": false
        }
        """;
}

public sealed record FieldMappingAnalysis(
    [property: JsonPropertyName("source_sheet")] string SourceSheet,
    [property: JsonPropertyName("fields")] IReadOnlyList<FieldMapping> Fields,
    [property: JsonPropertyName("unknown_columns")] IReadOnlyList<string> UnknownColumns);

public sealed record FieldMapping(
    [property: JsonPropertyName("canonical_field")] string CanonicalField,
    [property: JsonPropertyName("source_column")] string? SourceColumn,
    [property: JsonPropertyName("confidence")] double Confidence,
    [property: JsonPropertyName("reason")] string Reason);
