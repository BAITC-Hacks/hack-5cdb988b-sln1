using Extractor.Services.Extract;

var builder = WebApplication.CreateBuilder(args);

builder.AddServiceDefaults();

// Add services to the container.
// Learn more about configuring OpenAPI at https://aka.ms/aspnet/openapi
builder.Services.AddOpenApi();
builder.Services.AddSingleton<ExcelMarkdownService>();
builder.Services.AddSingleton<OpenAiClient>();
builder.Services.AddTransient<ExtractorService>();

var app = builder.Build();

app.MapDefaultEndpoints();

// Configure the HTTP request pipeline.
if (app.Environment.IsDevelopment()) {
    app.MapOpenApi();
}

app.MapPost("/extract", async Task<IResult> (
        HttpRequest request,
        ExtractorService extractorService,
        ExcelMarkdownService excelMarkdownService,
        OpenAiClient openAiClient,
        CancellationToken cancellationToken) => {
            if (!request.HasFormContentType) {
                return Results.BadRequest(new { error = "Request must be multipart/form-data." });
            }

            if (request.ContentLength is null or 0) {
                return Results.BadRequest(new { error = "Request body is empty. Attach Excel file as multipart field 'file'." });
            }

            var form = await request.ReadFormAsync(cancellationToken);
            var file = form.Files.GetFile("file") ?? form.Files.FirstOrDefault();
            if (file is null) {
                return Results.BadRequest(new { error = "Excel file is required in multipart field 'file'." });
            }

            if (file.Length == 0) {
                return Results.BadRequest(new { error = "File is empty." });
            }

            await using var stream = file.OpenReadStream();

            try {
                var result = await excelMarkdownService.ReadFirstRowsAsMarkdownAsync(stream, rowLimit: 15, cancellationToken);


                // var result = await extractorService.ExtractAsync(stream, cancellationToken);
                return Results.Ok(result);
            }
            catch (InvalidDataException exception) {
                return Results.BadRequest(new { error = exception.Message });
            }
            catch (InvalidOperationException exception) {
                return Results.Problem(exception.Message);
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested) {
                return Results.Problem("OpenAI analysis timed out.", statusCode: StatusCodes.Status504GatewayTimeout);
            }
        })
    .Accepts<IFormFile>("multipart/form-data")
    .Produces<ExtractionResponse>()
    .Produces(StatusCodes.Status400BadRequest)
    .DisableAntiforgery();

app.Run();
