using System.IO;
using System.Text.Json;
using System.Net;
using System.Net.Http;
using System.Text.Json.Nodes;
using System.Windows;
using KakomonTrainer;
using OpenCvSharp;
using PdfSharp.Drawing;
using PdfSharp.Pdf;

internal static class Program
{
    private static int count;
    private static void Check(bool value, string name) { if (!value) throw new Exception("FAIL: " + name); Console.WriteLine("PASS: " + name); count++; }
    private static void Reject(Action action, string name) { try { action(); } catch (InvalidDataException) { Check(true, name); return; } throw new Exception("FAIL: " + name); }
    [STAThread]
    private static int Main()
    {
        var application = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown }; var code = 0;
        application.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("pack://application:,,,/PresentationFramework.Fluent;component/Themes/Fluent.xaml") });
        application.Resources.MergedDictionaries.Add(new ResourceDictionary { Source = new Uri("pack://application:,,,/KakomonTrainer;component/Appearance.xaml") });
        application.Startup += async (_, _) =>
        {
            try { await Run(); }
            catch (Exception ex) { Console.Error.WriteLine(ex); code = 1; }
            finally { application.Shutdown(); }
        };
        application.Run(); Console.WriteLine("Check exit code: " + code); return code;
    }
    private static async Task Run()
    {
        var root = Path.Combine(Path.GetTempPath(), "KakomonWpfChecks-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root);
        Console.WriteLine("Test workspace: " + root);
        Check(AnswerMatcher.Judge("1/2", "0.5", 0) == true, "fraction comparison");
        Check(AnswerMatcher.Judge("25%", "0.25", 0) == true, "percentage comparison");
        Check(AnswerMatcher.Judge("1.2×10³", "1200", 0) == true, "superscript exponent");
        Check(AnswerMatcher.Judge("12 m", "12 kg", 0) == null, "different units need review");
        Check(AnswerMatcher.Judge("2x+1", "2x+5", 0) == null, "expressions are not numbers");
        Check(AnswerMatcher.Judge("1", "1.02", .01) == false, "numeric mismatch");
        Check(AnswerMatcher.Judge("1", "1.005", .01) == true, "numeric tolerance");
        Check(AnswerMatcher.Judge("yes\nはい", "はい", 0) == true, "multiple answers");
        Check(AnswerMatcher.Judge("", "a", 0) == null, "missing key needs review");
        Reject(() => DataStore.Child(root, "../escape"), "path traversal");
        Reject(() => DataStore.ValidateId("CON"), "reserved filename");
        Reject(() => DataStore.ValidateId("a/b"), "unsafe question id");
        var groups = Registration.Plan(["C:/exam_sheet.pdf", "C:/EXAM_answers.pdf", "C:/exam_rubric.md"]);
        Check(groups.Count == 1 && groups[0].References.Count == 2, "registration pairing");
        Reject(() => Registration.Plan(["C:/exam_answers.pdf"]), "orphan reference rejected");
        Reject(() => Registration.Plan(["C:/a.pdf", "D:/A.pdf"]), "duplicate filename rejected");
        var old = JsonSerializer.Deserialize<Transcription>("{\"question_id\":\"Q1\",\"transcription\":\"既存の答案\",\"custom\":42}", DataStore.Json)!;
        Check(old.TranscriptionText == "既存の答案" && JsonSerializer.Serialize(old, DataStore.Json).Contains("\"custom\": 42"), "legacy transcription and extension data");
        var store = new DataStore(root); var pdf = new PdfService();
        var source = Path.Combine(root, "input.pdf");
        using (var document = new PdfDocument())
        {
            for (var i = 0; i < 2; i++)
            {
                var page = document.AddPage(); using var g = XGraphics.FromPdfPage(page);
                g.DrawString("Native WPF test " + (i + 1), new XFont("Arial", 18), XBrushes.Black, 100, 160);
                g.DrawRectangle(XPens.Gray, 90, 190, 380, 180);
            }
            document.Save(source);
        }
        var template = await pdf.RegisterAsync(store, source, "検証用紙", default);
        Check(template.Pages.Count == 2 && template.Pages.All(p => p.Markers.Count == 3 && p.QrQuad.Count == 4), "blank PDF generation and measured markers");
        template.Questions.Add(new Question { Id = "Q1", MaxScore = 10, AnswerFormat = "answer_only", AnswerKey = "1/2", Regions = [new Region { PageNo = 1, Rect = [.15, .22, .8, .44] }] });
        template.Questions.Add(new Question { Id = "Q2", MaxScore = 5, AnswerFormat = "answer_only", AnswerKey = "yes", Regions = [new Region { PageNo = 2, Rect = [.15, .22, .8, .44] }] });
        store.SaveTemplate(template);
        var loaded = store.LoadTemplate(template.TemplateId); Check(loaded.QuestionCount == 2 && loaded.TotalMaxScore == 15, "template round trip");
        template.Questions[0].Regions[0].Rect = [-.1, 0, 1, 1]; Reject(() => store.SaveTemplate(template), "invalid region rejected"); template = loaded;
        var images = await pdf.RenderAsync(Path.Combine(store.TemplateDir(template.TemplateId), "blank.pdf"), 300);
        var inputs = new List<string>();
        for (var i = 1; i >= 0; i--)
        {
            using var mat = Cv2.ImDecode(images[i], ImreadModes.Color); using var rotated = new Mat(); Cv2.Rotate(mat, rotated, RotateFlags.Rotate180);
            var filename = Path.Combine(root, $"scan-{i}.png"); File.WriteAllBytes(filename, rotated.ToBytes(".png")); inputs.Add(filename);
        }
        var settings = new AppSettings { DataRoot = root }; using var ai = new AiService(settings, store, pdf);
        var window = new MainWindow(settings, store);
        Check(window.Title == "過去問トレーナー", "WPF main window constructs");
        var pasted = new AppSettings(); pasted.Values["ANTHROPIC_API_KEY"] = "sk-pasted-key\r\n";
        Check(pasted.Get("ANTHROPIC_API_KEY") == "sk-pasted-key" && pasted.ApiKey == "sk-pasted-key", "whitespace pasted with a key is ignored on read");
        var fields = (System.Windows.Controls.Panel)window.FindName("SettingFields")!;
        var keyRow = fields.Children.OfType<System.Windows.Controls.Grid>().First(g => g.Children.OfType<System.Windows.Controls.PasswordBox>()
            .Any(p => System.Windows.Automation.AutomationProperties.GetAutomationId(p) == "ANTHROPIC_API_KEY"));
        var masked = keyRow.Children.OfType<System.Windows.Controls.PasswordBox>().Single();
        var plain = keyRow.Children.OfType<System.Windows.Controls.TextBox>().Single();
        var reveal = keyRow.Children.OfType<System.Windows.Controls.CheckBox>().Single();
        masked.Password = "sk-pasted-key"; reveal.IsChecked = true;
        Check(plain.Text == "sk-pasted-key" && plain.Visibility == Visibility.Visible && masked.Visibility == Visibility.Collapsed, "API key reveals for checking and copying");
        plain.Text = "sk-edited-key"; reveal.IsChecked = false;
        Check(masked.Password == "sk-edited-key" && masked.Visibility == Visibility.Visible && plain.Visibility == Visibility.Collapsed, "edits made while revealed become the saved key");
        window.Close();
        var scan = new ScanService(store, pdf, ai);
        var s = await scan.ScanAsync(template, inputs.ToArray(), false, new Progress<string>(Console.WriteLine), default);
        Check(s.Status == "ready" && s.Pages.Select(p => p.PageNo).SequenceEqual([2, 1]), "rotated shuffled scan identified by QR");
        Check(s.Crops.Count == 2 && s.Crops.All(c => c.CropOriginCanvas.Length == 2 && c.CropW > 0), "crop geometry persisted");
        using (var image = Cv2.ImDecode(images[0], ImreadModes.Color))
        using (var distorted = new Mat())
        {
            var from = new Point2f[] { new(0, 0), new(image.Width - 1, 0), new(image.Width - 1, image.Height - 1), new(0, image.Height - 1) };
            var to = new Point2f[] { new(35, 40), new(image.Width - 55, 20), new(image.Width - 20, image.Height - 65), new(20, image.Height - 25) };
            using var transform = Cv2.GetPerspectiveTransform(from, to); Cv2.WarpPerspective(image, distorted, transform, image.Size(), borderMode: BorderTypes.Constant, borderValue: Scalar.White);
            var aligned = ScanService.Align(distorted.ToBytes(".png"), template);
            Check(aligned.Page.PageNo == 1 && aligned.Error < 10, "perspective correction residual <10px");
        }
        s.Transcriptions[0].TranscriptionText = "0.5"; s.Transcriptions[1].IsBlank = true; store.SaveTranscriptions(s);
        var grader = new GradingService(store, pdf, ai, settings); var result = await grader.GradeAsync(template, s, new Progress<string>(Console.WriteLine), default);
        Check(result.TotalScore == 10 && result.TotalMaxScore == 15 && result.Questions[1].SkippedBlank, "offline grade and blank answer");
        Check(File.Exists(Path.Combine(store.ResultDir(s.SessionId), "graded.pdf")), "annotated PDF export");
        var gradedPages = await pdf.RenderAsync(Path.Combine(store.ResultDir(s.SessionId), "graded.pdf"), 96);
        Check(gradedPages.Count >= 3, "annotated PDF renders including feedback appendix");
        var preview = new PdfWindow("PDF検証", gradedPages);
        Check(((System.Windows.Controls.DocumentViewer)preview.Content).Document.DocumentPaginator.PageCount == gradedPages.Count, "native PDF viewer has all printable pages");
        preview.Close();
        s.Transcriptions[0].TranscriptionText = "0.3"; store.SaveTranscriptions(s);
        Check(s.Status == "ready" && store.Result(s) == null, "editing hides stale results");
        var second = await grader.GradeAsync(template, s, new Progress<string>(Console.WriteLine), default);
        Check(second.TotalScore == 0, "regrade uses corrected transcription");
        Check(File.ReadAllLines(Path.Combine(root, "logs", "review", s.SessionId + ".jsonl")).Length == 1, "regrade does not duplicate history");
        var cts = new CancellationTokenSource(); cts.Cancel();
        try { await grader.GradeAsync(template, s, new Progress<string>(), cts.Token); throw new Exception("Cancellation not observed"); }
        catch (OperationCanceledException) { Check(s.Status == "ready", "cancellation restores editable status"); }
        try { await grader.SaveResult(template, s, second, cts.Token); throw new Exception("Save cancellation not observed"); }
        catch (OperationCanceledException) { Check(s.Status == "ready" && store.Result(s) == null, "failed result save hides incomplete output"); }
        await CheckApiContracts(store, pdf, template, s);
        Console.WriteLine($"All {count} checks passed. Artifacts: {root}");
    }
    private static async Task CheckApiContracts(DataStore store, PdfService pdf, Template template, Session session)
    {
        const string grade = """
        {"score":8,"feedback":"根拠の説明が不足しています。","confidence":"high","issues":[{"quote":"答案","kind":"内容","tag":"論理展開","comment":"理由を補足してください。","deduction":2}]}
        """;
        var question = new Question { Id = "Q1", AnswerFormat = "essay", MaxScore = 10 };
        var transcription = new Transcription { QuestionId = "Q1", TranscriptionText = "答案" };
        foreach (var provider in new[] { "anthropic", "openai", "kimi", "openai_compatible" })
        {
            var settings = new AppSettings(); settings.Values["GRADING_PROVIDER"] = provider;
            settings.Values[settings.Prefix + "_API_KEY"] = "fake-test-key"; settings.Values[settings.Prefix + "_MODEL"] = "fake-test-model"; settings.Values["LLM_BASE_URL"] = "https://fake.invalid/v1";
            var handler = new StubHandler(async request =>
            {
                var body = JsonNode.Parse(await request.Content!.ReadAsStringAsync())!;
                Check(body["model"]!.ToString() == "fake-test-model" && body["tools"]!.AsArray().Count == 1, provider + " structured tool request");
                if (provider == "anthropic")
                {
                    Check(request.Headers.Contains("x-api-key") && body["temperature"] == null, "Anthropic headers and no temperature");
                    return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(new JsonObject { ["content"] = new JsonArray(new JsonObject { ["type"] = "tool_use", ["name"] = "submit_grade", ["input"] = JsonNode.Parse(grade) }) }.ToJsonString()) };
                }
                Check(request.Headers.Authorization?.Scheme == "Bearer" && request.RequestUri!.AbsolutePath.EndsWith("/chat/completions"), provider + " bearer and endpoint");
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(new JsonObject { ["choices"] = new JsonArray(new JsonObject { ["message"] = new JsonObject { ["tool_calls"] = new JsonArray(new JsonObject { ["function"] = new JsonObject { ["arguments"] = grade } }) } }) }.ToJsonString()) };
            });
            using var ai = new AiService(settings, store, pdf, handler);
            var result = await ai.GradeQuestion(template, session, question, transcription, default);
            Check(result.Score == 8 && result.Issues.Count == 1, provider + " structured response parsed");
        }
        var failureSettings = new AppSettings(); failureSettings.Values["ANTHROPIC_API_KEY"] = "fake-test-key"; failureSettings.Values["ANTHROPIC_MODEL"] = "fake-test-model";
        using var failedAi = new AiService(failureSettings, store, pdf, new StubHandler(_ => Task.FromResult(new HttpResponseMessage(HttpStatusCode.Unauthorized) { Content = new StringContent("do-not-expose-provider-response") })));
        try { await failedAi.GradeQuestion(template, session, question, transcription, default); throw new Exception("401 not surfaced"); }
        catch (HttpRequestException ex) { Check(ex.StatusCode == HttpStatusCode.Unauthorized && !ex.Message.Contains("do-not-expose"), "API failure sanitized and surfaced"); }
    }
    private sealed class StubHandler(Func<HttpRequestMessage, Task<HttpResponseMessage>> response) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => response(request);
    }
}
